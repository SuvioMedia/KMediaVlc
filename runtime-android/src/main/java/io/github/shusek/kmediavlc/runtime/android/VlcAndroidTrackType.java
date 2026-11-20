// SPDX-License-Identifier: MIT OR LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.android;

/** Track kinds the player lists and switches. */
public enum VlcAndroidTrackType {
    AUDIO(1),
    SUBTITLE(2);

    private final int nativeValue;

    VlcAndroidTrackType(int nativeValue) {
        this.nativeValue = nativeValue;
    }

    int nativeValue() {
        return nativeValue;
    }
}
