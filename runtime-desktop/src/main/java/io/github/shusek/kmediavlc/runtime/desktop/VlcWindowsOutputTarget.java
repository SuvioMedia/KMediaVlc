// SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

package io.github.shusek.kmediavlc.runtime.desktop;

/** Windows D3D11 producer target on the same DXGI adapter as the Nucleus host. */
public record VlcWindowsOutputTarget(
        long generation,
        int width,
        int height,
        boolean hdr,
        float sdrWhiteNits,
        float peakNits,
        long adapterLuid) implements VlcOutputTarget {
    public VlcWindowsOutputTarget {
        VlcOutputTargets.validate(generation, width, height, sdrWhiteNits, peakNits);
    }
}
