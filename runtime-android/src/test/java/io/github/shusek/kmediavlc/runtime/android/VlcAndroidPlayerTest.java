// SPDX-License-Identifier: LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.android;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import android.content.Context;
import android.view.Surface;
import java.lang.reflect.Modifier;
import java.util.Map;
import java.util.Optional;
import org.junit.jupiter.api.Test;

final class VlcAndroidPlayerTest {
    @Test
    void exposesOnlyTheTwoReviewedDecodeModes() {
        assertEquals(
                java.util.List.of(
                        VlcAndroidDecodeMode.AUTOMATIC,
                        VlcAndroidDecodeMode.SOFTWARE_ONLY),
                java.util.List.of(VlcAndroidDecodeMode.values()));
    }

    @Test
    void exposesTheClosedSurfaceAndLifecycleContract() throws Exception {
        assertTrue(Modifier.isFinal(VlcAndroidPlayer.class.getModifiers()));
        assertTrue(AutoCloseable.class.isAssignableFrom(VlcAndroidPlayer.class));
        assertEquals(
                VlcAndroidPlayer.class,
                VlcAndroidPlayer.class.getMethod("create", Context.class).getReturnType());
        assertEquals(
                VlcAndroidPlayer.class,
                VlcAndroidPlayer.class
                        .getMethod("create", Context.class, VlcAndroidDecodeMode.class)
                        .getReturnType());
        assertEquals(
                void.class,
                VlcAndroidPlayer.class
                        .getMethod("attachSurface", Surface.class, int.class, int.class)
                        .getReturnType());
        assertEquals(
                void.class,
                VlcAndroidPlayer.class
                        .getMethod(
                                "attachSurfaces",
                                Surface.class,
                                Surface.class,
                                int.class,
                                int.class)
                        .getReturnType());
        assertEquals(
                void.class,
                VlcAndroidPlayer.class.getMethod("detachSurfaces").getReturnType());
    }

    @Test
    void exposesTheDesktopCompatibleControlBoundary() throws Exception {
        assertEquals(
                boolean.class,
                VlcAndroidPlayer.class
                        .getMethod("open", String.class, Map.class, boolean.class)
                        .getReturnType());
        assertEquals(boolean.class, VlcAndroidPlayer.class.getMethod("play").getReturnType());
        assertEquals(boolean.class, VlcAndroidPlayer.class.getMethod("pause").getReturnType());
        assertEquals(boolean.class, VlcAndroidPlayer.class.getMethod("stop").getReturnType());
        assertEquals(
                boolean.class,
                VlcAndroidPlayer.class
                        .getMethod("seek", long.class, boolean.class)
                        .getReturnType());
        assertEquals(
                boolean.class,
                VlcAndroidPlayer.class.getMethod("setVolume", float.class).getReturnType());
        assertEquals(
                boolean.class,
                VlcAndroidPlayer.class.getMethod("setRate", float.class).getReturnType());
        assertEquals(
                boolean.class,
                VlcAndroidPlayer.class.getMethod("setAudioDelay", long.class).getReturnType());
        assertEquals(60_000_000L, VlcAndroidPlayer.MAX_AUDIO_DELAY_MICROSECONDS);
        assertEquals(
                boolean.class,
                NativeBridge.class
                        .getDeclaredMethod("setAudioDelay", long.class, long.class)
                        .getReturnType());
        assertEquals(
                boolean.class,
                VlcAndroidPlayer.class.getMethod("setSubtitleDelay", long.class).getReturnType());
        assertEquals(600_000_000L, VlcAndroidPlayer.MAX_SUBTITLE_DELAY_MICROSECONDS);
        assertEquals(long.class, VlcAndroidPlayer.class.getMethod("tracksGeneration").getReturnType());
        assertEquals(
                java.util.List.class,
                VlcAndroidPlayer.class.getMethod("tracks", VlcAndroidTrackType.class).getReturnType());
        assertEquals(
                boolean.class,
                VlcAndroidPlayer.class
                        .getMethod("selectTrack", VlcAndroidTrackType.class, String.class)
                        .getReturnType());
        assertEquals(
                boolean.class,
                VlcAndroidPlayer.class.getMethod("disableTracks", VlcAndroidTrackType.class).getReturnType());
        assertEquals(
                boolean.class,
                NativeBridge.class
                        .getDeclaredMethod("selectTrack", long.class, int.class, byte[].class)
                        .getReturnType());
        assertEquals(
                byte[].class,
                NativeBridge.class.getDeclaredMethod("tracks", long.class, int.class).getReturnType());
        assertEquals(
                boolean.class,
                VlcAndroidPlayer.class.getMethod("setLoop", boolean.class).getReturnType());
        assertEquals(
                VlcAndroidPlayerSnapshot.class,
                VlcAndroidPlayer.class.getMethod("snapshot").getReturnType());
        assertEquals(Optional.class, VlcAndroidPlayer.class.getMethod("lastError").getReturnType());
    }

    @Test
    void nativeBoundaryHasNoOfficialJavaWrapperOrPathArgument() throws Exception {
        assertEquals(
                long.class,
                NativeBridge.class.getDeclaredMethod("create", int.class).getReturnType());
        assertEquals(
                boolean.class,
                NativeBridge.class
                        .getDeclaredMethod(
                                "setSurfaces",
                                long.class,
                                Surface.class,
                                Surface.class,
                                int.class,
                                int.class)
                        .getReturnType());
    }
}
