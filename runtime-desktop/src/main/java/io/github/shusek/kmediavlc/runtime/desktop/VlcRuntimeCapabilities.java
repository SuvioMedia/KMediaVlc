// SPDX-License-Identifier: LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.desktop;

import java.util.Objects;
import java.util.Set;

/** Immutable capabilities verified from the signed native payload manifest. */
public record VlcRuntimeCapabilities(
        int libVlcAbiMajor,
        int bridgeAbiVersion,
        String libVlcVersion,
        String libVlcRevision,
        Set<VlcFrameDeliveryMode> frameDeliveryModes,
        Set<VlcRenderEngine> renderEngines,
        boolean hdr10Metadata) {

    public VlcRuntimeCapabilities {
        if (libVlcAbiMajor != 4) {
            throw new IllegalArgumentException("Only the audited libVLC 4 ABI is accepted.");
        }
        if (bridgeAbiVersion != 2) {
            throw new IllegalArgumentException("Unsupported KMediaVlc bridge ABI.");
        }
        Objects.requireNonNull(libVlcVersion, "libVlcVersion");
        Objects.requireNonNull(libVlcRevision, "libVlcRevision");
        frameDeliveryModes = Set.copyOf(frameDeliveryModes);
        renderEngines = Set.copyOf(renderEngines);
        if (!frameDeliveryModes.containsAll(Set.of(VlcFrameDeliveryMode.GPU_PUSH, VlcFrameDeliveryMode.CPU_PULL))) {
            throw new IllegalArgumentException("A release runtime must support GPU push and CPU pull.");
        }
        if (renderEngines.isEmpty()) {
            throw new IllegalArgumentException("At least one GPU render engine is required.");
        }
    }
}
