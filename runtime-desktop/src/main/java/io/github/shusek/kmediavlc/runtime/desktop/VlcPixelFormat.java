// SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

package io.github.shusek.kmediavlc.runtime.desktop;

/** Color encoding of one bridge-owned frame. */
public enum VlcPixelFormat {
    RGBA8_SRGB(1, false),
    RGBA16F_LINEAR_SRGB(2, true);

    private final int nativeValue;
    private final boolean extendedLinear;

    VlcPixelFormat(int nativeValue, boolean extendedLinear) {
        this.nativeValue = nativeValue;
        this.extendedLinear = extendedLinear;
    }

    public int nativeValue() {
        return nativeValue;
    }

    public boolean extendedLinear() {
        return extendedLinear;
    }

    static VlcPixelFormat fromNative(int value) {
        for (VlcPixelFormat candidate : values()) {
            if (candidate.nativeValue == value) return candidate;
        }
        throw new VlcRuntimeException(
                VlcRuntimeException.Reason.NATIVE_CALL_FAILED,
                "The VLC bridge returned an unknown pixel format.");
    }
}
