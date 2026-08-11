// SPDX-License-Identifier: LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.desktop;

import static io.github.shusek.kmediavlc.runtime.desktop.VlcRuntimeException.Reason.MANIFEST_REJECTED;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.net.URI;
import java.net.URISyntaxException;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.EnumSet;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Properties;
import java.util.Set;
import java.util.regex.Pattern;

record NativePayloadManifest(
        String target,
        String runtimeId,
        String recipeRevision,
        String sourceOffer,
        String bridgePath,
        String libVlcPath,
        String pluginDirectory,
        VlcRuntimeCapabilities capabilities,
        List<FileEntry> files) {

    static final String VLC_VERSION = "4.0.0-dev";
    static final String VLC_REVISION = "b5536cdea24b313ba9215eacfbd7fa3295d7f3ee";
    static final int BRIDGE_ABI_VERSION = 2;
    static final int MAX_MANIFEST_BYTES = 256 * 1024;
    private static final int MAX_FILES = 2_048;
    private static final long MAX_FILE_BYTES = 1024L * 1024L * 1024L;
    private static final long MAX_PAYLOAD_BYTES = 3L * 1024L * 1024L * 1024L;
    private static final Pattern SHA_256 = Pattern.compile("[0-9a-f]{64}");
    private static final Pattern RUNTIME_ID = Pattern.compile("kmediavlc4-[0-9a-f]{16}");
    private static final Pattern COMMIT_REVISION = Pattern.compile("[0-9a-f]{40}");
    private static final Pattern COMPONENT = Pattern.compile("[A-Za-z0-9][A-Za-z0-9._+\\-]{0,127}");
    private static final Pattern SAFE_DIRECTORY = Pattern.compile("[A-Za-z0-9][A-Za-z0-9._+\\-/]{0,510}");
    private static final Set<String> ALLOWED_LICENSES =
            Set.of(
                    "0BSD",
                    "Apache-2.0",
                    "BSD-2-Clause",
                    "BSD-3-Clause",
                    "BSL-1.0",
                    "FTL",
                    "IJG",
                    "ISC",
                    "LGPL-2.0-or-later",
                    "LGPL-2.1-or-later",
                    "LGPL-3.0-or-later",
                    "Libpng-2.0",
                    "MIT",
                    "MPL-2.0",
                    "TU-Berlin-1.0",
                    "Zlib");

    static NativePayloadManifest parse(byte[] bytes, String expectedTarget) {
        if (bytes.length == 0 || bytes.length > MAX_MANIFEST_BYTES) {
            return reject("Native manifest size is invalid.");
        }

        var properties = new DuplicateRejectingProperties();
        try {
            properties.load(new ByteArrayInputStream(bytes));
        } catch (IOException | IllegalArgumentException failure) {
            return reject("Native manifest cannot be parsed.");
        }

        requireEqual("1", required(properties, "schemaVersion"), "manifest schema");
        requireEqual(expectedTarget, required(properties, "target"), "runtime target");
        requireTrue(properties, "releaseEligible");
        requireFalse(properties, "stockNightly");
        requireFalse(properties, "gplComponents");
        requireFalse(properties, "nonfreeComponents");
        requireEqual("4", required(properties, "libvlc.abiMajor"), "libVLC ABI major");
        requireEqual(VLC_VERSION, required(properties, "libvlc.version"), "libVLC version");
        requireEqual(VLC_REVISION, required(properties, "libvlc.revision"), "libVLC revision");
        requireEqual(
                Integer.toString(BRIDGE_ABI_VERSION),
                required(properties, "bridge.abiVersion"),
                "bridge ABI");

        String runtimeId = required(properties, "runtimeId");
        if (!RUNTIME_ID.matcher(runtimeId).matches()) {
            return reject("Runtime identity is invalid.");
        }
        String recipeRevision = required(properties, "recipeRevision");
        if (!COMMIT_REVISION.matcher(recipeRevision).matches()) {
            return reject("Runtime recipe revision is invalid.");
        }
        String sourceOffer = required(properties, "sourceOffer");
        validateSourceOffer(sourceOffer);

        EnumSet<VlcFrameDeliveryMode> modes =
                parseEnumSet(properties, "frameDeliveryModes", VlcFrameDeliveryMode.class);
        if (!modes.containsAll(EnumSet.allOf(VlcFrameDeliveryMode.class))) {
            return reject("Runtime must provide GPU push and CPU pull.");
        }
        EnumSet<VlcRenderEngine> engines =
                parseEnumSet(properties, "renderEngines", VlcRenderEngine.class);
        validateEngines(expectedTarget, engines);
        boolean hdr10Metadata = parseBoolean(properties, "hdr10Metadata");

        String bridgePath = requireSafeRelativeFile(required(properties, "bridge.path"));
        String libVlcPath = requireSafeRelativeFile(required(properties, "libvlc.path"));
        String pluginDirectory = requireSafeRelativeDirectory(required(properties, "plugins.path"));
        int count = parsePositiveInt(required(properties, "file.count"), MAX_FILES, "file count");

        var files = new ArrayList<FileEntry>(count);
        var paths = new HashSet<String>();
        var roles = EnumSet.noneOf(FileRole.class);
        long totalBytes = 0L;
        for (int index = 0; index < count; index++) {
            String prefix = "file." + index + ".";
            String path = requireSafeRelativeFile(required(properties, prefix + "path"));
            if (!paths.add(path)) {
                return reject("Native manifest contains duplicate file paths.");
            }
            long size = parsePositiveLong(required(properties, prefix + "size"), MAX_FILE_BYTES, "file size");
            if (totalBytes > MAX_PAYLOAD_BYTES - size) {
                return reject("Native payload is oversized.");
            }
            totalBytes += size;
            String sha256 = required(properties, prefix + "sha256");
            if (!SHA_256.matcher(sha256).matches()) {
                return reject("Native manifest contains an invalid SHA-256 value.");
            }
            String component = required(properties, prefix + "component");
            if (!COMPONENT.matcher(component).matches()) {
                return reject("Native manifest contains an invalid component identifier.");
            }
            String licenseSpdx = required(properties, prefix + "licenseSpdx");
            if (!isAllowedLicenseExpression(licenseSpdx)) {
                return reject("Native manifest contains a forbidden or unknown license.");
            }
            FileRole role = parseEnum(required(properties, prefix + "role"), FileRole.class, "file role");
            String source = required(properties, prefix + "source");
            validateSourceOffer(source);
            Linkage linkage = parseEnum(required(properties, prefix + "linkage"), Linkage.class, "file linkage");
            Linkage expectedLinkage =
                    role == FileRole.DATA || role == FileRole.LEGAL ? Linkage.NONE : Linkage.DYNAMIC;
            if (linkage != expectedLinkage) {
                return reject("Native manifest contains unsafe or incomplete relinking metadata.");
            }
            roles.add(role);
            files.add(new FileEntry(path, size, sha256, component, licenseSpdx, role, source, linkage));
        }

        if (!paths.contains(bridgePath) || !paths.contains(libVlcPath)) {
            return reject("Bridge or libVLC path is absent from the file inventory.");
        }
        if (!roles.containsAll(Set.of(FileRole.BRIDGE, FileRole.LIBVLC, FileRole.CORE, FileRole.PLUGIN))) {
            return reject("Native payload has an incomplete runtime role inventory.");
        }
        if (files.stream().noneMatch(entry -> entry.path().startsWith(pluginDirectory + "/"))) {
            return reject("Plugin directory does not contain an inventoried plugin.");
        }
        FileEntry bridge = files.stream().filter(entry -> entry.path().equals(bridgePath)).findFirst().orElseThrow();
        FileEntry libVlc = files.stream().filter(entry -> entry.path().equals(libVlcPath)).findFirst().orElseThrow();
        if (bridge.role() != FileRole.BRIDGE || libVlc.role() != FileRole.LIBVLC) {
            return reject("Bridge or libVLC inventory role is inconsistent.");
        }

        rejectUnknownKeys(properties, count);
        return new NativePayloadManifest(
                expectedTarget,
                runtimeId,
                recipeRevision,
                sourceOffer,
                bridgePath,
                libVlcPath,
                pluginDirectory,
                new VlcRuntimeCapabilities(4, 2, VLC_VERSION, VLC_REVISION, modes, engines, hdr10Metadata),
                List.copyOf(files));
    }

    private static boolean isAllowedLicenseExpression(String expression) {
        String previous = null;
        for (String identifier : expression.split(" AND ", -1)) {
            if (!ALLOWED_LICENSES.contains(identifier)
                    || (previous != null && previous.compareTo(identifier) >= 0)) {
                return false;
            }
            previous = identifier;
        }
        return previous != null;
    }

    private static void validateEngines(String target, Set<VlcRenderEngine> engines) {
        switch (target) {
            case "windows-x86_64", "windows-aarch64" -> {
                if (!engines.equals(Set.of(VlcRenderEngine.D3D11))) {
                    reject("Windows runtime must expose only the D3D11 output engine.");
                }
            }
            case "macos-aarch64" -> {
                if (!engines.equals(Set.of(VlcRenderEngine.OPENGL))) {
                    reject("macOS runtime must expose only the OpenGL IOSurface output engine.");
                }
            }
            case "linux-x86_64", "linux-aarch64" -> {
                if (!engines.equals(Set.of(VlcRenderEngine.GLES2))) {
                    reject("Linux runtime must expose only the GLES2 DMA-BUF output engine.");
                }
            }
            default -> reject("This KMediaVlc release does not support the requested native target.");
        }
    }

    private static void validateSourceOffer(String value) {
        try {
            URI uri = new URI(value);
            if (uri.isAbsolute()) {
                if (!"https".equalsIgnoreCase(uri.getScheme())
                        || uri.getHost() == null
                        || uri.getUserInfo() != null
                        || uri.getQuery() != null
                        || uri.getFragment() != null) {
                    reject("Source offer URL is invalid.");
                }
            } else {
                requireSafeRelativeFile(value);
            }
        } catch (URISyntaxException failure) {
            reject("Source offer reference is invalid.");
        }
    }

    private static String requireSafeRelativeDirectory(String value) {
        if (!SAFE_DIRECTORY.matcher(value).matches()
                || value.contains("\\")
                || value.endsWith("/")
                || value.contains("//")) {
            return reject("Native manifest contains an unsafe directory path.");
        }
        Path normalized = Path.of(value).normalize();
        if (normalized.isAbsolute() || normalized.startsWith("..") || !normalized.toString().replace('\\', '/').equals(value)) {
            return reject("Native manifest contains an unsafe directory path.");
        }
        return value;
    }

    private static String requireSafeRelativeFile(String value) {
        String safe = requireSafeRelativeDirectory(value);
        if (!safe.contains(".") || safe.endsWith(".")) {
            return reject("Native manifest file path is invalid.");
        }
        return safe;
    }

    private static <E extends Enum<E>> EnumSet<E> parseEnumSet(
            Properties properties, String key, Class<E> enumClass) {
        String raw = required(properties, key);
        var result = EnumSet.noneOf(enumClass);
        for (String token : raw.split(",", -1)) {
            if (token.isBlank() || !result.add(parseEnum(token, enumClass, key))) {
                return reject("Native manifest contains an invalid " + key + " list.");
            }
        }
        return result;
    }

    private static <E extends Enum<E>> E parseEnum(String value, Class<E> enumClass, String field) {
        try {
            return Enum.valueOf(enumClass, value);
        } catch (IllegalArgumentException failure) {
            return reject("Native manifest contains an invalid " + field + ".");
        }
    }

    private static boolean parseBoolean(Properties properties, String key) {
        String value = required(properties, key);
        if ("true".equals(value)) return true;
        if ("false".equals(value)) return false;
        return reject("Native manifest boolean is invalid: " + key);
    }

    private static void requireTrue(Properties properties, String key) {
        if (!parseBoolean(properties, key)) reject("Native payload is not release eligible: " + key);
    }

    private static void requireFalse(Properties properties, String key) {
        if (parseBoolean(properties, key)) reject("Native payload violates release policy: " + key);
    }

    private static int parsePositiveInt(String value, int maximum, String field) {
        long parsed = parsePositiveLong(value, maximum, field);
        return (int) parsed;
    }

    private static long parsePositiveLong(String value, long maximum, String field) {
        try {
            long parsed = Long.parseLong(value);
            if (parsed < 1 || parsed > maximum) return reject("Native manifest has an invalid " + field + ".");
            return parsed;
        } catch (NumberFormatException failure) {
            return reject("Native manifest has an invalid " + field + ".");
        }
    }

    private static String required(Properties properties, String key) {
        String value = properties.getProperty(key);
        if (value == null || value.isBlank() || !value.equals(value.trim())) {
            return reject("Native manifest field is missing or malformed: " + key);
        }
        return value;
    }

    private static void requireEqual(String expected, String actual, String field) {
        if (!expected.equals(actual)) reject("Native manifest has a mismatched " + field + ".");
    }

    private static void rejectUnknownKeys(Properties properties, int count) {
        var allowed =
                new HashSet<>(
                        Set.of(
                                "schemaVersion",
                                "target",
                                "releaseEligible",
                                "stockNightly",
                                "gplComponents",
                                "nonfreeComponents",
                                "libvlc.abiMajor",
                                "libvlc.version",
                                "libvlc.revision",
                                "bridge.abiVersion",
                                "runtimeId",
                                "recipeRevision",
                                "sourceOffer",
                                "frameDeliveryModes",
                                "renderEngines",
                                "hdr10Metadata",
                                "bridge.path",
                                "libvlc.path",
                                "plugins.path",
                                "file.count"));
        for (int index = 0; index < count; index++) {
            String prefix = "file." + index + ".";
            allowed.add(prefix + "path");
            allowed.add(prefix + "size");
            allowed.add(prefix + "sha256");
            allowed.add(prefix + "component");
            allowed.add(prefix + "licenseSpdx");
            allowed.add(prefix + "role");
            allowed.add(prefix + "source");
            allowed.add(prefix + "linkage");
        }
        for (Object key : properties.keySet()) {
            if (!(key instanceof String stringKey) || !allowed.contains(stringKey)) {
                reject("Native manifest contains an unsupported field.");
            }
        }
    }

    private static <T> T reject(String message) {
        throw new VlcRuntimeException(MANIFEST_REJECTED, message);
    }

    enum FileRole {
        BRIDGE,
        LIBVLC,
        CORE,
        PLUGIN,
        DEPENDENCY,
        DATA,
        LEGAL
    }

    enum Linkage {
        DYNAMIC,
        NONE
    }

    record FileEntry(
            String path,
            long size,
            String sha256,
            String component,
            String licenseSpdx,
            FileRole role,
            String source,
            Linkage linkage) {}

    private static final class DuplicateRejectingProperties extends Properties {
        @Override
        public synchronized Object put(Object key, Object value) {
            if (containsKey(key)) reject("Native manifest contains a duplicate field.");
            return super.put(key, value);
        }
    }
}
