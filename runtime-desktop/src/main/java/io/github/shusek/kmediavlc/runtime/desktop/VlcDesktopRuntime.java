// SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

package io.github.shusek.kmediavlc.runtime.desktop;

import static io.github.shusek.kmediavlc.runtime.desktop.VlcRuntimeException.Reason.EXTRACTION_FAILED;
import static io.github.shusek.kmediavlc.runtime.desktop.VlcRuntimeException.Reason.INTEGRITY_FAILURE;
import static io.github.shusek.kmediavlc.runtime.desktop.VlcRuntimeException.Reason.PAYLOAD_MISSING;
import static io.github.shusek.kmediavlc.runtime.desktop.VlcRuntimeException.Reason.UNSUPPORTED_PLATFORM;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Comparator;
import java.util.HexFormat;
import java.util.Locale;
import java.util.Optional;
import java.util.UUID;

/** Resolves and verifies the optional bundled KMediaVlc desktop runtime without loading it. */
public final class VlcDesktopRuntime {
    private static final String RESOURCE_ROOT = "META-INF/kmediavlc/native/";
    private static final Object EXTRACTION_LOCK = new Object();

    private VlcDesktopRuntime() {}

    /** Probes the current platform payload and never extracts or loads native code. */
    public static VlcRuntimeInspection inspectBundled() {
        String target;
        try {
            target = currentTarget();
        } catch (VlcRuntimeException failure) {
            return new VlcRuntimeInspection(
                    false, "unsupported", Optional.empty(), Optional.of(failure.reason()));
        }
        try {
            NativePayloadManifest manifest = readManifest(target);
            return new VlcRuntimeInspection(
                    true, target, Optional.of(manifest.capabilities()), Optional.empty());
        } catch (VlcRuntimeException failure) {
            return new VlcRuntimeInspection(
                    false, target, Optional.empty(), Optional.of(failure.reason()));
        }
    }

    /**
     * Extracts the verified runtime below an application-private directory.
     *
     * <p>An existing runtime is accepted only when every inventoried file still matches. Invalid
     * existing content is never overwritten automatically.
     */
    public static VlcDesktopRuntimeResolution resolveBundled(Path extractionRoot) {
        if (extractionRoot == null || !extractionRoot.isAbsolute()) {
            throw new IllegalArgumentException("Extraction root must be an absolute path.");
        }
        Path normalizedRoot = extractionRoot.normalize();
        String target = currentTarget();
        NativePayloadManifest manifest = readManifest(target);

        synchronized (EXTRACTION_LOCK) {
            Path runtimeDirectory = normalizedRoot.resolve(manifest.runtimeId()).normalize();
            requireDescendant(normalizedRoot, runtimeDirectory);
            if (Files.exists(runtimeDirectory)) {
                verifyExtracted(runtimeDirectory, manifest);
                return resolution(runtimeDirectory, manifest);
            }

            Path temporary =
                    normalizedRoot.resolve("." + manifest.runtimeId() + ".tmp-" + UUID.randomUUID()).normalize();
            requireDescendant(normalizedRoot, temporary);
            try {
                Files.createDirectories(normalizedRoot);
                rejectSymbolicLink(normalizedRoot);
                Files.createDirectory(temporary);
                for (NativePayloadManifest.FileEntry entry : manifest.files()) {
                    Path destination = temporary.resolve(entry.path()).normalize();
                    requireDescendant(temporary, destination);
                    Files.createDirectories(destination.getParent());
                    try (InputStream input = resource(target, entry.path())) {
                        Files.copy(input, destination);
                    }
                    verifyFile(destination, entry);
                }
                try {
                    Files.move(temporary, runtimeDirectory, StandardCopyOption.ATOMIC_MOVE);
                } catch (AtomicMoveNotSupportedException ignored) {
                    Files.move(temporary, runtimeDirectory);
                }
                verifyExtracted(runtimeDirectory, manifest);
                return resolution(runtimeDirectory, manifest);
            } catch (IOException failure) {
                deleteTemporaryTree(temporary);
                if (Files.exists(runtimeDirectory)) {
                    verifyExtracted(runtimeDirectory, manifest);
                    return resolution(runtimeDirectory, manifest);
                }
                throw new VlcRuntimeException(EXTRACTION_FAILED, "Verified VLC runtime extraction failed.", failure);
            } catch (RuntimeException failure) {
                deleteTemporaryTree(temporary);
                throw failure;
            }
        }
    }

    static String currentTarget() {
        return targetFor(
                System.getProperty("os.name", ""),
                System.getProperty("os.arch", ""));
    }

    static String targetFor(String osName, String architectureName) {
        String os = osName.toLowerCase(Locale.ROOT);
        String architecture = architectureName.toLowerCase(Locale.ROOT);
        String arch =
                switch (architecture) {
                    case "amd64", "x86_64" -> "x86_64";
                    case "aarch64", "arm64" -> "aarch64";
                    default -> throw new VlcRuntimeException(UNSUPPORTED_PLATFORM, "Unsupported desktop architecture.");
                };
        if (os.contains("windows")) return "windows-" + arch;
        if (os.contains("mac") && arch.equals("aarch64")) return "macos-aarch64";
        throw new VlcRuntimeException(
                UNSUPPORTED_PLATFORM,
                "A bundled KMediaVlc GPU payload is unavailable for this desktop target.");
    }

    private static NativePayloadManifest readManifest(String target) {
        String name = RESOURCE_ROOT + target + "/manifest.properties";
        try (InputStream input = VlcDesktopRuntime.class.getClassLoader().getResourceAsStream(name)) {
            if (input == null) throw new VlcRuntimeException(PAYLOAD_MISSING, "Bundled VLC runtime is unavailable.");
            var output = new ByteArrayOutputStream();
            input.transferTo(output);
            byte[] bytes = output.toByteArray();
            if (bytes.length > NativePayloadManifest.MAX_MANIFEST_BYTES) {
                throw new VlcRuntimeException(INTEGRITY_FAILURE, "Bundled VLC manifest is oversized.");
            }
            return NativePayloadManifest.parse(bytes, target);
        } catch (IOException failure) {
            throw new VlcRuntimeException(INTEGRITY_FAILURE, "Bundled VLC manifest cannot be read.", failure);
        }
    }

    private static InputStream resource(String target, String relativePath) {
        String name = RESOURCE_ROOT + target + "/" + relativePath;
        InputStream input = VlcDesktopRuntime.class.getClassLoader().getResourceAsStream(name);
        if (input == null) {
            throw new VlcRuntimeException(INTEGRITY_FAILURE, "An inventoried VLC runtime resource is missing.");
        }
        return input;
    }

    private static void verifyExtracted(Path directory, NativePayloadManifest manifest) {
        try {
            rejectSymbolicLink(directory);
            for (NativePayloadManifest.FileEntry entry : manifest.files()) {
                Path file = directory.resolve(entry.path()).normalize();
                requireDescendant(directory, file);
                verifyFile(file, entry);
            }
        } catch (IOException failure) {
            throw new VlcRuntimeException(INTEGRITY_FAILURE, "Extracted VLC runtime cannot be verified.", failure);
        }
    }

    private static void verifyFile(Path file, NativePayloadManifest.FileEntry entry) throws IOException {
        if (!Files.isRegularFile(file) || Files.isSymbolicLink(file)) {
            throw new VlcRuntimeException(INTEGRITY_FAILURE, "Extracted VLC runtime contains an invalid file.");
        }
        if (Files.size(file) != entry.size()) {
            throw new VlcRuntimeException(INTEGRITY_FAILURE, "Extracted VLC runtime file size is invalid.");
        }
        String hash = sha256(file);
        if (!hash.equals(entry.sha256())) {
            throw new VlcRuntimeException(INTEGRITY_FAILURE, "Extracted VLC runtime file hash is invalid.");
        }
    }

    private static String sha256(Path file) throws IOException {
        MessageDigest digest;
        try {
            digest = MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException impossible) {
            throw new AssertionError(impossible);
        }
        try (InputStream input = Files.newInputStream(file, StandardOpenOption.READ)) {
            byte[] buffer = new byte[64 * 1024];
            int count;
            while ((count = input.read(buffer)) >= 0) {
                if (count > 0) digest.update(buffer, 0, count);
            }
        }
        return HexFormat.of().formatHex(digest.digest());
    }

    private static VlcDesktopRuntimeResolution resolution(
            Path directory, NativePayloadManifest manifest) {
        Path bridge = directory.resolve(manifest.bridgePath()).normalize();
        Path libVlc = directory.resolve(manifest.libVlcPath()).normalize();
        Path plugins = directory.resolve(manifest.pluginDirectory()).normalize();
        if (!Files.isDirectory(plugins) || Files.isSymbolicLink(plugins)) {
            throw new VlcRuntimeException(INTEGRITY_FAILURE, "Extracted VLC plugin directory is invalid.");
        }
        return new VlcDesktopRuntimeResolution(
                bridge, libVlc, plugins, manifest.runtimeId(), manifest.capabilities());
    }

    private static void rejectSymbolicLink(Path path) throws IOException {
        if (Files.isSymbolicLink(path)) {
            throw new VlcRuntimeException(INTEGRITY_FAILURE, "VLC runtime path must not be a symbolic link.");
        }
    }

    private static void requireDescendant(Path root, Path candidate) {
        if (candidate.equals(root) || !candidate.startsWith(root)) {
            throw new VlcRuntimeException(INTEGRITY_FAILURE, "VLC runtime path escaped its extraction root.");
        }
    }

    private static void deleteTemporaryTree(Path temporary) {
        if (!Files.exists(temporary) || Files.isSymbolicLink(temporary)) return;
        try (var stream = Files.walk(temporary)) {
            stream.sorted(Comparator.reverseOrder()).forEach(path -> {
                try {
                    Files.deleteIfExists(path);
                } catch (IOException ignored) {
                    // A failed temporary cleanup does not justify touching any wider directory.
                }
            });
        } catch (IOException ignored) {
            // The next extraction uses a unique temporary directory.
        }
    }
}
