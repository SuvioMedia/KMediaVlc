// SPDX-License-Identifier: LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.desktop;

/** Lock-free snapshot copied from the native player. Time values use microseconds. */
public record VlcPlayerSnapshot(
        VlcPlaybackState state,
        long mediaGeneration,
        long positionMicroseconds,
        long durationMicroseconds,
        int videoWidth,
        int videoHeight,
        int bufferedPermille,
        boolean seekable) {

    public VlcPlayerSnapshot {
        if (state == null) throw new NullPointerException("state");
        positionMicroseconds = Math.max(0, positionMicroseconds);
        durationMicroseconds = Math.max(0, durationMicroseconds);
        videoWidth = Math.max(0, videoWidth);
        videoHeight = Math.max(0, videoHeight);
        bufferedPermille = Math.clamp(bufferedPermille, 0, 1000);
    }
}
