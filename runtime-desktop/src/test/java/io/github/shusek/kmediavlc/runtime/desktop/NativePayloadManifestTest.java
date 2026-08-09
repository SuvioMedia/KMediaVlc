// SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

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
        assertTrue(manifest.capabilities().frameDeliveryModes().contains(VlcFrameDeliveryMode.GPU_PUSH));
        assertTrue(manifest.capabilities().frameDeliveryModes().contains(VlcFrameDeliveryMode.CPU_PULL));
        assertTrue(manifest.capabilities().renderEngines().contains(VlcRenderEngine.D3D11));
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
                bridge.abiVersion=1
                runtimeId=kmediavlc4-0123456789abcdef
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
                file.0.licenseSpdx=LicenseRef-KMediaVlc-Proprietary
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
                file.2.path=libvlccore.dll
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
