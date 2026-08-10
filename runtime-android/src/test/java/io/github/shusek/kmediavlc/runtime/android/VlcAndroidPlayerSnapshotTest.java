// SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

package io.github.shusek.kmediavlc.runtime.android;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

final class VlcAndroidPlayerSnapshotTest {
    @Test
    void boundsAndCopiesTheNativeSnapshot() {
        VlcAndroidPlayerSnapshot snapshot =
                new VlcAndroidPlayerSnapshot(
                        new long[] {3, 7, -1, 2_000_000, 1920, 1080, 2_000, 1});
        assertEquals(VlcAndroidPlaybackState.PLAYING, snapshot.getState());
        assertEquals(7, snapshot.getMediaGeneration());
        assertEquals(0, snapshot.getPositionMicroseconds());
        assertEquals(2_000_000, snapshot.getDurationMicroseconds());
        assertEquals(1920, snapshot.getVideoWidth());
        assertEquals(1080, snapshot.getVideoHeight());
        assertEquals(1000, snapshot.getBufferedPermille());
        assertTrue(snapshot.isSeekable());
    }

    @Test
    void rejectsMalformedOrUnboundedSnapshots() {
        assertThrows(
                VlcAndroidException.class,
                () -> new VlcAndroidPlayerSnapshot(new long[] {0}));
        assertThrows(
                VlcAndroidException.class,
                () ->
                        new VlcAndroidPlayerSnapshot(
                                new long[] {0, 0, 0, 0, 16_385, 1, 0, 0}));
    }
}
