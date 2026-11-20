// SPDX-License-Identifier: MIT OR LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.desktop;

/** Track kinds the player lists and switches. */
public enum VlcTrackType {
    AUDIO(1),
    SUBTITLE(2);

    private final int nativeValue;

    VlcTrackType(int nativeValue) { this.nativeValue = nativeValue; }

    int nativeValue() { return nativeValue; }
}
