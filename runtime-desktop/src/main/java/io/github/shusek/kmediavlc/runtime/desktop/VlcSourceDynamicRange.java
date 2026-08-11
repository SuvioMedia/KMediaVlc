// SPDX-License-Identifier: LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.desktop;

/** Dynamic-range signal reported by libVLC for the decoded source. */
public enum VlcSourceDynamicRange {
    UNKNOWN(0),
    SDR(1),
    HDR10(2),
    HLG(3);

    private final int nativeValue;

    VlcSourceDynamicRange(int nativeValue) {
        this.nativeValue = nativeValue;
    }

    int nativeValue() {
        return nativeValue;
    }

    static VlcSourceDynamicRange fromNative(int value) {
        for (var range : values()) {
            if (range.nativeValue == value) return range;
        }
        throw new VlcRuntimeException(
                VlcRuntimeException.Reason.NATIVE_CALL_FAILED,
                "The VLC bridge returned an unknown source dynamic range.");
    }
}
