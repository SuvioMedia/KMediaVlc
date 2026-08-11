// SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

package io.github.shusek.kmediavlc.runtime.android;

/** Closed decode policy applied to every media opened by one player. */
public enum VlcAndroidDecodeMode {
    /** Uses VLC 4's default Android policy, including MediaCodec when it is compatible. */
    AUTOMATIC(0),
    /** Adds VLC 4's per-media {@code :no-hw-dec} option. */
    SOFTWARE_ONLY(1);

    private final int nativeValue;

    VlcAndroidDecodeMode(int nativeValue) {
        this.nativeValue = nativeValue;
    }

    int nativeValue() {
        return nativeValue;
    }
}
