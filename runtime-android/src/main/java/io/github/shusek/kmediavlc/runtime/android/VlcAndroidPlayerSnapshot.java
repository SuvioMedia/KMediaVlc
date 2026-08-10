// SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

package io.github.shusek.kmediavlc.runtime.android;

/** Immutable, bounded player state. Time values use microseconds. */
public final class VlcAndroidPlayerSnapshot {
    private final VlcAndroidPlaybackState state;
    private final long mediaGeneration;
    private final long positionMicroseconds;
    private final long durationMicroseconds;
    private final int videoWidth;
    private final int videoHeight;
    private final int bufferedPermille;
    private final boolean seekable;

    VlcAndroidPlayerSnapshot(long[] values) {
        if (values == null || values.length != 8) {
            throw new VlcAndroidException(
                    VlcAndroidException.Reason.NATIVE_CALL_FAILED,
                    "The Android VLC bridge returned a malformed snapshot.");
        }
        state = VlcAndroidPlaybackState.fromNative(values[0]);
        mediaGeneration = Math.max(0L, values[1]);
        positionMicroseconds = Math.max(0L, values[2]);
        durationMicroseconds = Math.max(0L, values[3]);
        videoWidth = boundedDimension(values[4]);
        videoHeight = boundedDimension(values[5]);
        bufferedPermille = (int) Math.max(0L, Math.min(1000L, values[6]));
        seekable = values[7] != 0L;
    }

    public VlcAndroidPlaybackState getState() {
        return state;
    }

    public long getMediaGeneration() {
        return mediaGeneration;
    }

    public long getPositionMicroseconds() {
        return positionMicroseconds;
    }

    public long getDurationMicroseconds() {
        return durationMicroseconds;
    }

    public int getVideoWidth() {
        return videoWidth;
    }

    public int getVideoHeight() {
        return videoHeight;
    }

    public int getBufferedPermille() {
        return bufferedPermille;
    }

    public boolean isSeekable() {
        return seekable;
    }

    private static int boundedDimension(long value) {
        if (value < 0L || value > 16_384L) {
            throw new VlcAndroidException(
                    VlcAndroidException.Reason.NATIVE_CALL_FAILED,
                    "Native video dimensions escaped the Android boundary.");
        }
        return (int) value;
    }
}
