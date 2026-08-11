// SPDX-License-Identifier: LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.desktop;

/** Runtime behavior selected before a libVLC 4 player is created. */
public record VlcDesktopPlayerConfig(
        VlcFrameDeliveryMode deliveryMode,
        boolean requestHdr,
        float sdrWhiteNits,
        float displayPeakNits,
        VlcPlayerListener listener) {

    public VlcDesktopPlayerConfig {
        if (deliveryMode == null) throw new NullPointerException("deliveryMode");
        if (!Float.isFinite(sdrWhiteNits) || sdrWhiteNits <= 0f) {
            throw new IllegalArgumentException("sdrWhiteNits must be finite and positive.");
        }
        if (!Float.isFinite(displayPeakNits) || displayPeakNits < sdrWhiteNits) {
            throw new IllegalArgumentException("displayPeakNits must be at least sdrWhiteNits.");
        }
        if (listener == null) listener = new VlcPlayerListener() {};
    }

    public static VlcDesktopPlayerConfig gpuPush(VlcPlayerListener listener) {
        return new VlcDesktopPlayerConfig(
                VlcFrameDeliveryMode.GPU_PUSH, true, 203f, 1_000f, listener);
    }

    public static VlcDesktopPlayerConfig cpuPull(VlcPlayerListener listener) {
        return new VlcDesktopPlayerConfig(
                VlcFrameDeliveryMode.CPU_PULL, false, 203f, 203f, listener);
    }
}
