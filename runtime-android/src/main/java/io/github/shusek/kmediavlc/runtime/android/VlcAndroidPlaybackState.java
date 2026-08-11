// SPDX-License-Identifier: LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.android;

/** Coarse state copied from the pinned libVLC 4 player callbacks. */
public enum VlcAndroidPlaybackState {
    IDLE(0),
    OPENING(1),
    BUFFERING(2),
    PLAYING(3),
    PAUSED(4),
    STOPPED(5),
    ENDED(6),
    ERROR(7);

    private final int nativeValue;

    VlcAndroidPlaybackState(int nativeValue) {
        this.nativeValue = nativeValue;
    }

    static VlcAndroidPlaybackState fromNative(long value) {
        for (VlcAndroidPlaybackState candidate : values()) {
            if (candidate.nativeValue == value) return candidate;
        }
        return ERROR;
    }
}
