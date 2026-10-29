// SPDX-License-Identifier: LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.desktop;

import static org.junit.jupiter.api.Assertions.*;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.Properties;
import java.io.StringReader;
import java.io.StringWriter;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class VlcExternalRuntimeTest {
    @TempDir Path directory;

    private Properties fixture() throws Exception {
        var properties = new Properties();
        properties.load(new StringReader(NativePayloadManifestTest.validManifest()));
        String target = VlcDesktopRuntime.currentTarget();
        properties.setProperty("target", target);
        properties.setProperty("renderEngines", target.startsWith("macos") ? "OPENGL" :
                target.startsWith("linux") ? "GLES2" : "D3D11");
        properties.setProperty("libvlc.revision", "b".repeat(40));
        properties.setProperty("libvlc.version", "4.0.0-recipient");
        properties.setProperty("releaseEligible", "false");
        for (int i = 0; i < 4; i++) {
            byte[] modified = ("recipient-built-library-" + i).getBytes(StandardCharsets.UTF_8);
            Path file = directory.resolve(properties.getProperty("file." + i + ".path"));
            Files.createDirectories(file.getParent());
            Files.write(file, modified);
            properties.setProperty("file." + i + ".size", Integer.toString(modified.length));
            properties.setProperty("file." + i + ".sha256",
                    HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(modified)));
        }
        save(properties);
        return properties;
    }

    private void save(Properties properties) throws Exception {
        var output = new StringWriter();
        properties.store(output, "Recipient replacement test");
        Files.writeString(directory.resolve("manifest.properties"), output.toString(), StandardCharsets.ISO_8859_1);
    }

    @Test void acceptsChangedBytesAndRevisionWithoutLoadingThem() throws Exception {
        fixture();
        var result = VlcDesktopRuntime.resolveExternalDirectory(directory);
        assertEquals(directory.resolve("libvlc.dll"), result.libVlcPath());
        assertEquals("b".repeat(40), result.capabilities().libVlcRevision());
        assertEquals("4.0.0-recipient", result.capabilities().libVlcVersion());
        assertThrows(VlcRuntimeException.class, () -> NativePayloadManifest.parse(
                Files.readAllBytes(directory.resolve("manifest.properties")), VlcDesktopRuntime.currentTarget()));
    }

    @Test void acceptsALibraryReSignedAfterItsManifestWasWritten() throws Exception {
        var properties = fixture();
        Path library = publishMachO(properties, NativeLibraryDigestTest.MachO.library().build());
        Files.write(library, NativeLibraryDigestTest.MachO.library().reSigned().build());
        assertEquals(directory.resolve("libvlc.dll"), VlcDesktopRuntime.resolveExternalDirectory(directory).libVlcPath());
    }

    @Test void rejectsAReSignedLibraryWithChangedCode() throws Exception {
        var properties = fixture();
        Path library = publishMachO(properties, NativeLibraryDigestTest.MachO.library().build());
        Files.write(library, NativeLibraryDigestTest.MachO.library().reSigned().code((byte) 0x7f).build());
        assertThrows(VlcRuntimeException.class, () -> VlcDesktopRuntime.resolveExternalDirectory(directory));
    }

    /** Replaces file 0 with a signed Mach-O library and records both of its digests. */
    private Path publishMachO(Properties properties, byte[] library) throws Exception {
        Path file = directory.resolve(properties.getProperty("file.0.path"));
        Files.write(file, library);
        properties.setProperty("file.0.size", Integer.toString(library.length));
        properties.setProperty("file.0.sha256",
                HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(library)));
        properties.setProperty("file.0.signatureIndependentSha256", NativeLibraryDigestTest.EXPECTED_DIGEST);
        save(properties);
        return file;
    }

    @Test void rejectsChangedFileUnlessItsLocalManifestIsUpdated() throws Exception {
        fixture();
        Files.writeString(directory.resolve("libvlc.dll"), "changed again");
        assertThrows(VlcRuntimeException.class, () -> VlcDesktopRuntime.resolveExternalDirectory(directory));
    }

    @Test void rejectsIncompatibleAbiAndWrongPlatform() throws Exception {
        var properties = fixture();
        properties.setProperty("bridge.abiVersion", "99");
        save(properties);
        assertThrows(VlcRuntimeException.class, () -> VlcDesktopRuntime.resolveExternalDirectory(directory));
        properties.setProperty("bridge.abiVersion", "2");
        properties.setProperty("target", "wrong-platform");
        save(properties);
        assertThrows(VlcRuntimeException.class, () -> VlcDesktopRuntime.resolveExternalDirectory(directory));
    }

    @Test void rejectsTraversalSymlinkAndExtraPlugin() throws Exception {
        var properties = fixture();
        Files.writeString(directory.resolve("plugins/unlisted.dll"), "extra");
        assertThrows(VlcRuntimeException.class, () -> VlcDesktopRuntime.resolveExternalDirectory(directory));
        Files.delete(directory.resolve("plugins/unlisted.dll"));
        Files.move(directory.resolve("plugins"), directory.resolve("other"));
        Files.createSymbolicLink(directory.resolve("plugins"), directory.resolve("other"));
        assertThrows(VlcRuntimeException.class, () -> VlcDesktopRuntime.resolveExternalDirectory(directory));
        properties.setProperty("file.3.path", "../outside.dll");
        save(properties);
        assertThrows(VlcRuntimeException.class, () -> VlcDesktopRuntime.resolveExternalDirectory(directory));
    }

    @Test void rejectsOversizedManifestAndRelativeDirectory() throws Exception {
        Files.write(directory.resolve("manifest.properties"), new byte[NativePayloadManifest.MAX_MANIFEST_BYTES + 1]);
        assertThrows(VlcRuntimeException.class, () -> VlcDesktopRuntime.resolveExternalDirectory(directory));
        assertThrows(IllegalArgumentException.class, () -> VlcDesktopRuntime.resolveExternalDirectory(Path.of("relative")));
    }
}
