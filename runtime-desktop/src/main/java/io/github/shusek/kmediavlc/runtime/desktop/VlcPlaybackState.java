// SPDX-License-Identifier: LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.desktop;

/** Coarse native playback state. */
public enum VlcPlaybackState {
    IDLE(0),
    OPENING(1),
    BUFFERING(2),
    PLAYING(3),
    PAUSED(4),
    STOPPED(5),
    ENDED(6),
    ERROR(7);

    private final int nativeValue;

    VlcPlaybackState(int nativeValue) {
        this.nativeValue = nativeValue;
    }

    static VlcPlaybackState fromNative(int value) {
        for (VlcPlaybackState candidate : values()) {
            if (candidate.nativeValue == value) return candidate;
        }
        return ERROR;
    }
}
