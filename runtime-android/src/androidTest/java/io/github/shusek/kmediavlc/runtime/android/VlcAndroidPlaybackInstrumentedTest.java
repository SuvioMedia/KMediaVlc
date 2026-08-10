// SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

package io.github.shusek.kmediavlc.runtime.android;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import android.app.Instrumentation;
import android.app.UiAutomation;
import android.content.Context;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.graphics.Rect;
import android.media.MediaCodecInfo;
import android.media.MediaCodecList;
import android.os.SystemClock;
import android.view.Surface;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Locale;
import java.util.function.BooleanSupplier;
import org.junit.Test;
import org.junit.runner.RunWith;

/** Real bundled-runtime playback coverage for an ARM Android device or emulator. */
@RunWith(AndroidJUnit4.class)
public final class VlcAndroidPlaybackInstrumentedTest {
    private static final String FIXTURE = "kmediavlc-android-playback.mkv";
    private static final String FIXTURE_SHA256 =
            "f9cee3480b4619e2d94979a30b40f19cbb417289d3453e7bbb890a871c6f9718";
    private static final String MEDIACODEC_THREAD = "vlc-mediacodec";
    private static final String SPU_THREAD = "vlc-dec-spu";
    private static final long PLAYBACK_TIMEOUT_MILLIS = 20_000L;
    private static final long TRANSITION_TIMEOUT_MILLIS = 10_000L;
    private static final long SURFACE_POSITION_TOLERANCE_MICROSECONDS = 750_000L;

    @Test
    public void automaticDecodeUsesMediaCodecAndSurvivesSurfaceLifecycle() throws Exception {
        assertTrue("The device must advertise an AVC MediaCodec decoder.", hasAvcDecoder());
        runPlaybackLifecycle(VlcAndroidDecodeMode.AUTOMATIC, true);
    }

    @Test
    public void softwareDecodeAvoidsMediaCodecAndSurvivesSurfaceLifecycle() throws Exception {
        runPlaybackLifecycle(VlcAndroidDecodeMode.SOFTWARE_ONLY, false);
    }

    private void runPlaybackLifecycle(VlcAndroidDecodeMode mode, boolean expectMediaCodec)
            throws Exception {
        awaitCondition(
                () -> !nativeThreadExists(MEDIACODEC_THREAD),
                TRANSITION_TIMEOUT_MILLIS,
                "a previous MediaCodec decoder thread to stop");
        Instrumentation instrumentation = InstrumentationRegistry.getInstrumentation();
        File fixture = new File(instrumentation.getContext().getCacheDir(), FIXTURE);
        VlcAndroidSurfaceTestActivity host = null;
        VlcAndroidPlayer player = null;
        try {
            copyFixture(instrumentation, fixture);
            host = startSurfaceHost(instrumentation);
            VlcAndroidSurfaceTestActivity activeHost = host;
            ScreenProbe pixels = new ScreenProbe(instrumentation);
            Context targetContext = instrumentation.getTargetContext();
            VlcAndroidRuntimeReport runtime = VlcAndroidRuntime.inspectNativeRuntime();
            assertEquals(VlcAndroidRuntime.BRIDGE_ABI_VERSION, runtime.getBridgeAbiVersion());
            assertEquals(VlcAndroidRuntime.VLC_REVISION, runtime.getVlcRevision());
            assertTrue(VlcAndroidRuntime.SUPPORTED_ABIS.contains(runtime.getNativeAbi()));

            player = VlcAndroidPlayer.create(targetContext, mode);
            VlcAndroidPlayer activePlayer = player;
            assertEquals(mode, activePlayer.getDecodeMode());
            attachCurrentSurfaces(activePlayer, activeHost);
            assertTrue(
                    "libVLC rejected the owned playback fixture: "
                            + activePlayer.lastError().orElse("no native error"),
                    activePlayer.open(fixture.getAbsolutePath(), true));

            long videoSignature =
                    awaitRenderedPlayback(activePlayer, activeHost, pixels, "initial attachment");
            verifyDecoderRoute(expectMediaCodec);
            assertTrue(
                    "libVLC rejected the lifecycle setup seek.",
                    activePlayer.seek(2_000_000L, false));
            awaitCondition(
                    () -> requireHealthySnapshot(activePlayer).getPositionMicroseconds() >= 1_500_000L,
                    PLAYBACK_TIMEOUT_MILLIS,
                    "the lifecycle setup position");

            for (int replacement = 1; replacement <= 2; replacement++) {
                long positionBeforeReplacement =
                        requireHealthySnapshot(activePlayer).getPositionMicroseconds();
                activePlayer.detachSurfaces();
                instrumentation.runOnMainSync(activeHost::replaceSurfaces);
                assertTrue(
                        "Replacement SurfaceViews were not created.",
                        activeHost.awaitSurfaces(TRANSITION_TIMEOUT_MILLIS));
                attachCurrentSurfaces(activePlayer, activeHost);
                videoSignature =
                        awaitRenderedPlayback(
                                activePlayer, activeHost, pixels, "replacement " + replacement);
                verifyDecoderRoute(expectMediaCodec);
                long minimumPreservedPosition =
                        Math.max(
                                0L,
                                positionBeforeReplacement
                                        - SURFACE_POSITION_TOLERANCE_MICROSECONDS);
                awaitCondition(
                        () ->
                                requireHealthySnapshot(activePlayer).getPositionMicroseconds()
                                        >= minimumPreservedPosition,
                        PLAYBACK_TIMEOUT_MILLIS,
                        "the preserved position after replacement " + replacement);
            }

            assertTrue("libVLC rejected an exact seek.", activePlayer.seek(7_000_000L, false));
            awaitCondition(
                    () -> requireHealthySnapshot(activePlayer).getPositionMicroseconds() >= 6_500_000L,
                    PLAYBACK_TIMEOUT_MILLIS,
                    "the post-seek position");
            long postSeekSignature =
                    pixels.awaitVideo(activeHost, "a rendered frame after seek");
            assertNotEquals(
                    "Seek did not produce a distinct video frame.",
                    videoSignature,
                    postSeekSignature);
            pixels.awaitSubtitle(activeHost, "subtitle output after seek");

            assertTrue("libVLC rejected stop.", activePlayer.stop());
            awaitCondition(
                    () -> activePlayer.snapshot().getState() == VlcAndroidPlaybackState.STOPPED,
                    TRANSITION_TIMEOUT_MILLIS,
                    "the stopped state");
            activePlayer.detachSurfaces();
            activePlayer.close();
            assertClosed(activePlayer);
            player = null;
            awaitCondition(
                    () -> !nativeThreadExists(MEDIACODEC_THREAD),
                    TRANSITION_TIMEOUT_MILLIS,
                    "the MediaCodec decoder thread to stop after close");
        } finally {
            try {
                if (player != null) player.close();
            } finally {
                try {
                    if (host != null) {
                        VlcAndroidSurfaceTestActivity hostToFinish = host;
                        instrumentation.runOnMainSync(hostToFinish::finish);
                        instrumentation.waitForIdleSync();
                    }
                } finally {
                    if (!fixture.delete() && fixture.exists()) {
                        throw new IOException(
                                "Could not remove the copied Android playback fixture.");
                    }
                }
            }
        }
    }

    private static VlcAndroidSurfaceTestActivity startSurfaceHost(Instrumentation instrumentation)
            throws Exception {
        Intent intent =
                new Intent(
                                instrumentation.getTargetContext(),
                                VlcAndroidSurfaceTestActivity.class)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        VlcAndroidSurfaceTestActivity host =
                (VlcAndroidSurfaceTestActivity) instrumentation.startActivitySync(intent);
        try {
            instrumentation.waitForIdleSync();
            assertTrue(
                    "Initial SurfaceViews were not created.",
                    host.awaitSurfaces(TRANSITION_TIMEOUT_MILLIS));
            return host;
        } catch (Exception | AssertionError failure) {
            instrumentation.runOnMainSync(host::finish);
            instrumentation.waitForIdleSync();
            throw failure;
        }
    }

    private static void attachCurrentSurfaces(
            VlcAndroidPlayer player, VlcAndroidSurfaceTestActivity host) {
        Surface video = host.getVideoSurface();
        Surface subtitles = host.getSubtitleSurface();
        assertTrue("Video SurfaceView is invalid.", video.isValid());
        assertTrue("Subtitle SurfaceView is invalid.", subtitles.isValid());
        player.attachSurfaces(
                video,
                subtitles,
                VlcAndroidSurfaceTestActivity.SURFACE_WIDTH,
                VlcAndroidSurfaceTestActivity.SURFACE_HEIGHT);
    }

    private static long awaitRenderedPlayback(
            VlcAndroidPlayer player,
            VlcAndroidSurfaceTestActivity host,
            ScreenProbe pixels,
            String phase)
            throws Exception {
        awaitCondition(
                () -> {
                    VlcAndroidPlayerSnapshot snapshot = requireHealthySnapshot(player);
                    VlcAndroidPlaybackState state = snapshot.getState();
                    return (state == VlcAndroidPlaybackState.PLAYING
                                    || state == VlcAndroidPlaybackState.BUFFERING)
                            && snapshot.getVideoWidth() > 0
                            && snapshot.getVideoHeight() > 0
                            && nativeThreadExists(SPU_THREAD);
                },
                PLAYBACK_TIMEOUT_MILLIS,
                "active video and subtitle decoders for " + phase);
        long videoSignature = pixels.awaitVideo(host, "video for " + phase);
        pixels.awaitSubtitle(host, "subtitles for " + phase);
        return videoSignature;
    }

    private static VlcAndroidPlayerSnapshot requireHealthySnapshot(VlcAndroidPlayer player) {
        VlcAndroidPlayerSnapshot snapshot = player.snapshot();
        if (snapshot.getState() == VlcAndroidPlaybackState.ERROR) {
            throw new AssertionError(
                    "libVLC entered ERROR: " + player.lastError().orElse("no native error"));
        }
        return snapshot;
    }

    private static void verifyDecoderRoute(boolean expectMediaCodec) throws Exception {
        if (expectMediaCodec) {
            awaitCondition(
                    () -> nativeThreadExists(MEDIACODEC_THREAD),
                    TRANSITION_TIMEOUT_MILLIS,
                    "VLC's MediaCodec output thread");
        } else {
            SystemClock.sleep(500L);
            assertFalse(
                    "SOFTWARE_ONLY unexpectedly created VLC's MediaCodec output thread.",
                    nativeThreadExists(MEDIACODEC_THREAD));
        }
    }

    private static void copyFixture(Instrumentation instrumentation, File output)
            throws Exception {
        Context testContext = instrumentation.getContext();
        try (InputStream source =
                        new BufferedInputStream(testContext.getAssets().open(FIXTURE));
                OutputStream destination =
                        new BufferedOutputStream(new FileOutputStream(output, false))) {
            byte[] buffer = new byte[32 * 1024];
            while (true) {
                int count = source.read(buffer);
                if (count < 0) break;
                destination.write(buffer, 0, count);
            }
        }
        assertEquals("The playback fixture bytes changed.", FIXTURE_SHA256, sha256(output));
    }

    private static String sha256(File file) throws IOException, NoSuchAlgorithmException {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        try (InputStream input = new BufferedInputStream(new FileInputStream(file))) {
            byte[] buffer = new byte[32 * 1024];
            while (true) {
                int count = input.read(buffer);
                if (count < 0) break;
                digest.update(buffer, 0, count);
            }
        }
        StringBuilder result = new StringBuilder(64);
        for (byte value : digest.digest()) {
            result.append(String.format(Locale.ROOT, "%02x", value & 0xff));
        }
        return result.toString();
    }

    private static boolean hasAvcDecoder() {
        for (MediaCodecInfo codec :
                new MediaCodecList(MediaCodecList.ALL_CODECS).getCodecInfos()) {
            if (codec.isEncoder()) continue;
            for (String type : codec.getSupportedTypes()) {
                if ("video/avc".equalsIgnoreCase(type)) return true;
            }
        }
        return false;
    }

    private static boolean nativeThreadExists(String expectedName) {
        File[] tasks = new File("/proc/self/task").listFiles();
        if (tasks == null) return false;
        for (File task : tasks) {
            File nameFile = new File(task, "comm");
            try (InputStreamReader reader =
                    new InputStreamReader(
                            new FileInputStream(nameFile), StandardCharsets.US_ASCII)) {
                char[] characters = new char[64];
                int count = reader.read(characters);
                if (count > 0 && expectedName.equals(new String(characters, 0, count).trim())) {
                    return true;
                }
            } catch (IOException ignored) {
                // A native thread may terminate between listing /proc and opening its name.
            }
        }
        return false;
    }

    private static void awaitCondition(
            BooleanSupplier condition, long timeoutMillis, String description) throws Exception {
        long deadline = SystemClock.elapsedRealtime() + timeoutMillis;
        while (SystemClock.elapsedRealtime() < deadline) {
            if (condition.getAsBoolean()) return;
            SystemClock.sleep(50L);
        }
        fail("Timed out waiting for " + description + '.');
    }

    private static void assertClosed(VlcAndroidPlayer player) {
        try {
            player.snapshot();
            fail("A closed player still accepted a snapshot call.");
        } catch (IllegalStateException expected) {
            // Expected closed-state boundary.
        }
    }

    private static final class ScreenProbe {
        private final UiAutomation automation;

        ScreenProbe(Instrumentation instrumentation) {
            automation = instrumentation.getUiAutomation();
        }

        long awaitVideo(VlcAndroidSurfaceTestActivity host, String description) throws Exception {
            long deadline = SystemClock.elapsedRealtime() + PLAYBACK_TIMEOUT_MILLIS;
            boolean captured = false;
            while (SystemClock.elapsedRealtime() < deadline) {
                Bitmap bitmap = capture(host);
                captured |= bitmap != null;
                if (bitmap != null) {
                    long signature = videoSignature(bitmap);
                    bitmap.recycle();
                    if (signature != 0L) return signature;
                }
                SystemClock.sleep(75L);
            }
            fail("Timed out waiting for " + description + "; screenshotCaptured=" + captured);
            return 0L;
        }

        void awaitSubtitle(VlcAndroidSurfaceTestActivity host, String description)
                throws Exception {
            long deadline = SystemClock.elapsedRealtime() + PLAYBACK_TIMEOUT_MILLIS;
            boolean captured = false;
            while (SystemClock.elapsedRealtime() < deadline) {
                Bitmap bitmap = capture(host);
                captured |= bitmap != null;
                if (bitmap != null) {
                    boolean visible = hasVisibleSubtitle(bitmap);
                    bitmap.recycle();
                    if (visible) return;
                }
                SystemClock.sleep(75L);
            }
            fail("Timed out waiting for " + description + "; screenshotCaptured=" + captured);
        }

        private Bitmap capture(VlcAndroidSurfaceTestActivity host) throws Exception {
            Rect source = host.getSurfaceRectOnScreen(TRANSITION_TIMEOUT_MILLIS);
            Bitmap screenshot = automation.takeScreenshot();
            if (screenshot == null
                    || source.left < 0
                    || source.top < 0
                    || source.right > screenshot.getWidth()
                    || source.bottom > screenshot.getHeight()
                    || source.isEmpty()) {
                if (screenshot != null) screenshot.recycle();
                return null;
            }
            Bitmap cropped =
                    Bitmap.createBitmap(
                            screenshot,
                            source.left,
                            source.top,
                            source.width(),
                            source.height());
            screenshot.recycle();
            return cropped;
        }

        private static long videoSignature(Bitmap bitmap) {
            int minimum = 255;
            int maximum = 0;
            long signature = 0xcbf29ce484222325L;
            for (int y = 0; y < bitmap.getHeight(); y += 8) {
                for (int x = 0; x < bitmap.getWidth(); x += 8) {
                    int pixel = bitmap.getPixel(x, y);
                    int luminance =
                            (Color.red(pixel) * 54
                                            + Color.green(pixel) * 183
                                            + Color.blue(pixel) * 19)
                                    >> 8;
                    minimum = Math.min(minimum, luminance);
                    maximum = Math.max(maximum, luminance);
                    signature ^= pixel & 0xffffffffL;
                    signature *= 0x100000001b3L;
                }
            }
            return maximum - minimum >= 24 ? signature : 0L;
        }

        private static boolean hasVisibleSubtitle(Bitmap bitmap) {
            int brightPixels = 0;
            // The fixture's grayscale video plane stays below this threshold by construction.
            for (int y = bitmap.getHeight() / 2; y < bitmap.getHeight(); y += 2) {
                for (int x = 0; x < bitmap.getWidth(); x += 2) {
                    int pixel = bitmap.getPixel(x, y);
                    if (Color.alpha(pixel) > 16
                            && Color.red(pixel) + Color.green(pixel) + Color.blue(pixel) > 540) {
                        if (++brightPixels >= 8) return true;
                    }
                }
            }
            return false;
        }
    }
}
