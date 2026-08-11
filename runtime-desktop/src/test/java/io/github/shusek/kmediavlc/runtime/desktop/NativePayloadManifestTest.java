// SPDX-License-Identifier: LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.desktop;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.charset.StandardCharsets;
import org.junit.jupiter.api.Test;

class NativePayloadManifestTest {
    @Test
    void acceptsPinnedGpuPushAndCpuPullPayload() {
        var manifest = NativePayloadManifest.parse(validManifest().getBytes(StandardCharsets.ISO_8859_1), "windows-x86_64");
        assertEquals(4, manifest.capabilities().libVlcAbiMajor());
        assertEquals(NativePayloadManifest.BRIDGE_ABI_VERSION, manifest.capabilities().bridgeAbiVersion());
        assertEquals("0123456789abcdef0123456789abcdef01234567", manifest.recipeRevision());
        assertTrue(manifest.capabilities().frameDeliveryModes().contains(VlcFrameDeliveryMode.GPU_PUSH));
        assertTrue(manifest.capabilities().frameDeliveryModes().contains(VlcFrameDeliveryMode.CPU_PULL));
        assertTrue(manifest.capabilities().renderEngines().contains(VlcRenderEngine.D3D11));
    }

    @Test
    void acceptsPinnedMacOsOpenGlIosurfacePayload() {
        String macManifest = validManifest()
                .replace("target=windows-x86_64", "target=macos-aarch64")
                .replace("renderEngines=D3D11", "renderEngines=OPENGL");
        var manifest = NativePayloadManifest.parse(
                macManifest.getBytes(StandardCharsets.ISO_8859_1), "macos-aarch64");
        assertTrue(manifest.capabilities().renderEngines().contains(VlcRenderEngine.OPENGL));

        String wrongEngine = macManifest.replace("renderEngines=OPENGL", "renderEngines=D3D11");
        assertThrows(VlcRuntimeException.class, () -> NativePayloadManifest.parse(
                wrongEngine.getBytes(StandardCharsets.ISO_8859_1), "macos-aarch64"));

        String extraEngine = macManifest.replace("renderEngines=OPENGL", "renderEngines=OPENGL,D3D11");
        assertThrows(VlcRuntimeException.class, () -> NativePayloadManifest.parse(
                extraEngine.getBytes(StandardCharsets.ISO_8859_1), "macos-aarch64"));
    }

    @Test
    void acceptsPinnedLinuxGles2DmaBufPayloads() {
        for (String target : new String[] {"linux-x86_64", "linux-aarch64"}) {
            String linuxManifest = validManifest()
                    .replace("target=windows-x86_64", "target=" + target)
                    .replace("renderEngines=D3D11", "renderEngines=GLES2");
            var manifest = NativePayloadManifest.parse(
                    linuxManifest.getBytes(StandardCharsets.ISO_8859_1), target);
            assertTrue(manifest.capabilities().renderEngines().contains(VlcRenderEngine.GLES2));

            String wrongEngine = linuxManifest.replace("renderEngines=GLES2", "renderEngines=OPENGL");
            assertThrows(VlcRuntimeException.class, () -> NativePayloadManifest.parse(
                    wrongEngine.getBytes(StandardCharsets.ISO_8859_1), target));
        }
    }

    @Test
    void rejectsGplPayloadAndPathTraversal() {
        String gpl = validManifest().replace("gplComponents=false", "gplComponents=true");
        assertThrows(VlcRuntimeException.class, () -> NativePayloadManifest.parse(gpl.getBytes(StandardCharsets.ISO_8859_1), "windows-x86_64"));
        String traversal = validManifest().replace("plugins/codec.dll", "../codec.dll");
        assertThrows(VlcRuntimeException.class, () -> NativePayloadManifest.parse(traversal.getBytes(StandardCharsets.ISO_8859_1), "windows-x86_64"));
    }

    @Test
    void rejectsUnknownPreviewRevisionAndMissingPullMode() {
        String revision = validManifest().replace(NativePayloadManifest.VLC_REVISION, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
        assertThrows(VlcRuntimeException.class, () -> NativePayloadManifest.parse(revision.getBytes(StandardCharsets.ISO_8859_1), "windows-x86_64"));
        String mode = validManifest().replace("GPU_PUSH,CPU_PULL", "GPU_PUSH");
        assertThrows(VlcRuntimeException.class, () -> NativePayloadManifest.parse(mode.getBytes(StandardCharsets.ISO_8859_1), "windows-x86_64"));
    }

    @Test
    void rejectsRecipeRevisionThatIsNotAnExactLowercaseCommit() {
        String revision = validManifest().replace(
                "0123456789abcdef0123456789abcdef01234567", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA");
        assertThrows(VlcRuntimeException.class, () -> NativePayloadManifest.parse(
                revision.getBytes(StandardCharsets.ISO_8859_1), "windows-x86_64"));
    }

    @Test
    void acceptsOnlyCanonicalAllowedLicenseConjunctions() {
        String conjunction = validManifest().replace(
                "file.3.licenseSpdx=LGPL-2.1-or-later",
                "file.3.licenseSpdx=BSD-3-Clause AND LGPL-2.1-or-later");
        NativePayloadManifest.parse(
                conjunction.getBytes(StandardCharsets.ISO_8859_1), "windows-x86_64");

        for (String invalid : new String[] {
            "LGPL-2.1-or-later AND BSD-3-Clause",
            "BSD-3-Clause AND BSD-3-Clause",
            "BSD-3-Clause OR LGPL-2.1-or-later",
            "GPL-2.0-or-later"
        }) {
            String manifest = validManifest().replace(
                    "file.3.licenseSpdx=LGPL-2.1-or-later",
                    "file.3.licenseSpdx=" + invalid);
            assertThrows(VlcRuntimeException.class, () -> NativePayloadManifest.parse(
                    manifest.getBytes(StandardCharsets.ISO_8859_1), "windows-x86_64"));
        }
    }

    private static String validManifest() {
        String hash = "a".repeat(64);
        return """
                schemaVersion=1
                target=windows-x86_64
                releaseEligible=true
                stockNightly=false
                gplComponents=false
                nonfreeComponents=false
                libvlc.abiMajor=4
                libvlc.version=4.0.0-dev
                libvlc.revision=%s
                bridge.abiVersion=2
                runtimeId=kmediavlc4-0123456789abcdef
                recipeRevision=0123456789abcdef0123456789abcdef01234567
                sourceOffer=corresponding-source.tar.gz
                frameDeliveryModes=GPU_PUSH,CPU_PULL
                renderEngines=D3D11
                hdr10Metadata=true
                bridge.path=kmediavlc.dll
                libvlc.path=libvlc.dll
                plugins.path=plugins
                file.count=4
                file.0.path=kmediavlc.dll
                file.0.size=10
                file.0.sha256=%s
                file.0.component=kmediavlc-bridge
                file.0.licenseSpdx=LGPL-2.1-or-later
                file.0.role=BRIDGE
                file.0.source=sources/kmediavlc-bridge.tar.gz
                file.0.linkage=DYNAMIC
                file.1.path=libvlc.dll
                file.1.size=20
                file.1.sha256=%s
                file.1.component=videolan-vlc
                file.1.licenseSpdx=LGPL-2.1-or-later
                file.1.role=LIBVLC
                file.1.source=sources/vlc-b5536cde.tar.xz
                file.1.linkage=DYNAMIC
                file.2.path=libvlccore-9.dll
                file.2.size=30
                file.2.sha256=%s
                file.2.component=videolan-vlc
                file.2.licenseSpdx=LGPL-2.1-or-later
                file.2.role=CORE
                file.2.source=sources/vlc-b5536cde.tar.xz
                file.2.linkage=DYNAMIC
                file.3.path=plugins/codec.dll
                file.3.size=40
                file.3.sha256=%s
                file.3.component=videolan-vlc
                file.3.licenseSpdx=LGPL-2.1-or-later
                file.3.role=PLUGIN
                file.3.source=sources/vlc-b5536cde.tar.xz
                file.3.linkage=DYNAMIC
                """.formatted(NativePayloadManifest.VLC_REVISION, hash, hash, hash, hash);
    }
}
