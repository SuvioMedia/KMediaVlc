// SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

package io.github.shusek.kmediavlc.runtime.android;

import android.content.Context;
import android.view.Surface;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;

/** A serialized libVLC 4 lifecycle that renders directly into Android surfaces. */
public final class VlcAndroidPlayer implements AutoCloseable {
    private static final int MAX_LOCATION_CHARACTERS = 16_384;
    private static final int MAX_UTF8_BYTES = 65_536;
    private static final int MAX_HEADERS = 32;
    private static final int MAX_SURFACE_DIMENSION = 16_384;

    private long nativeHandle;
    private final VlcAndroidDecodeMode decodeMode;

    private VlcAndroidPlayer(long nativeHandle, VlcAndroidDecodeMode decodeMode) {
        this.nativeHandle = nativeHandle;
        this.decodeMode = decodeMode;
    }

    public static VlcAndroidPlayer create(Context context) {
        return create(context, VlcAndroidDecodeMode.AUTOMATIC);
    }

    public static VlcAndroidPlayer create(Context context, VlcAndroidDecodeMode decodeMode) {
        Objects.requireNonNull(context, "context");
        Objects.requireNonNull(decodeMode, "decodeMode");
        VlcAndroidRuntime.requireSupportedDevice();
        VlcAndroidRuntime.ensureLoaded();
        long handle = NativeBridge.create(decodeMode.nativeValue());
        if (handle == 0L) {
            throw new VlcAndroidException(
                    VlcAndroidException.Reason.PLAYER_INITIALIZATION_FAILED,
                    "The pinned Android libVLC runtime rejected player creation.");
        }
        return new VlcAndroidPlayer(handle, decodeMode);
    }

    public VlcAndroidDecodeMode getDecodeMode() {
        return decodeMode;
    }

    public synchronized void attachSurface(Surface video, int width, int height) {
        attachSurfaces(video, null, width, height);
    }

    /**
     * Attaches the opaque video surface and an optional transparent subtitle surface. The second
     * surface is needed for subtitles while MediaCodec renders directly into the first one.
     */
    public synchronized void attachSurfaces(
            Surface video, Surface subtitles, int width, int height) {
        requireOpen();
        Objects.requireNonNull(video, "video");
        if (!video.isValid()) throw new IllegalArgumentException("Video Surface is invalid.");
        if (subtitles != null && !subtitles.isValid()) {
            throw new IllegalArgumentException("Subtitle Surface is invalid.");
        }
        if (video == subtitles) {
            throw new IllegalArgumentException("Video and subtitle Surfaces must be distinct.");
        }
        if (width <= 0 || height <= 0 || width > MAX_SURFACE_DIMENSION ||
                height > MAX_SURFACE_DIMENSION) {
            throw new IllegalArgumentException("Surface dimensions are outside the closed boundary.");
        }
        requireNative(
                NativeBridge.setSurfaces(nativeHandle, video, subtitles, width, height),
                "libVLC rejected the Android surfaces.");
    }

    /**
     * Detaches surface output without immediately stopping playback. Attaching new surfaces while
     * media is open recreates the native media player, restores its position and controls, and
     * resumes its prior playing or paused state.
     */
    public synchronized void detachSurfaces() {
        requireOpen();
        requireNative(
                NativeBridge.setSurfaces(nativeHandle, null, null, 0, 0),
                "libVLC could not detach the Android surfaces.");
    }

    public synchronized boolean open(String location, boolean autoplay) {
        return open(location, Collections.emptyMap(), autoplay);
    }

    /** Opens a path/URI with the same closed HTTP-header set as the desktop bridge. */
    public synchronized boolean open(
            String location, Map<String, String> requestHeaders, boolean autoplay) {
        requireOpen();
        byte[] encodedLocation = encodeLocation(location);
        Objects.requireNonNull(requestHeaders, "requestHeaders");
        if (requestHeaders.size() > MAX_HEADERS) {
            throw new IllegalArgumentException("Too many HTTP request headers.");
        }
        byte[][] pairs = new byte[requestHeaders.size() * 2][];
        int index = 0;
        int totalBytes = 0;
        for (Map.Entry<String, String> entry : requestHeaders.entrySet()) {
            String name = requireHeaderPart(entry.getKey(), true);
            String value = requireHeaderPart(entry.getValue(), false);
            pairs[index++] = name.getBytes(StandardCharsets.UTF_8);
            pairs[index++] = value.getBytes(StandardCharsets.UTF_8);
            totalBytes += pairs[index - 2].length + pairs[index - 1].length;
        }
        if (totalBytes > MAX_UTF8_BYTES) {
            throw new IllegalArgumentException("HTTP headers exceed the UTF-8 boundary.");
        }
        return NativeBridge.open(nativeHandle, encodedLocation, pairs, autoplay);
    }

    public synchronized boolean play() {
        requireOpen();
        return NativeBridge.play(nativeHandle);
    }

    public synchronized boolean pause() {
        requireOpen();
        return NativeBridge.pause(nativeHandle);
    }

    public synchronized boolean stop() {
        requireOpen();
        return NativeBridge.stop(nativeHandle);
    }

    public synchronized boolean seek(long timeMicroseconds, boolean fast) {
        requireOpen();
        return NativeBridge.seek(nativeHandle, Math.max(0L, timeMicroseconds), fast);
    }

    public synchronized boolean setVolume(float volume) {
        requireOpen();
        if (!Float.isFinite(volume)) throw new IllegalArgumentException("volume must be finite.");
        return NativeBridge.setVolume(nativeHandle, Math.max(0.0f, Math.min(1.0f, volume)));
    }

    public synchronized boolean setRate(float rate) {
        requireOpen();
        if (!Float.isFinite(rate) || rate <= 0.0f) {
            throw new IllegalArgumentException("rate must be finite and positive.");
        }
        return NativeBridge.setRate(nativeHandle, rate);
    }

    /** Applies to media opened after this call. */
    public synchronized boolean setLoop(boolean loop) {
        requireOpen();
        return NativeBridge.setLoop(nativeHandle, loop);
    }

    public synchronized VlcAndroidPlayerSnapshot snapshot() {
        requireOpen();
        return new VlcAndroidPlayerSnapshot(NativeBridge.snapshot(nativeHandle));
    }

    public synchronized Optional<String> lastError() {
        if (nativeHandle == 0L) return Optional.empty();
        byte[] value = NativeBridge.lastErrorUtf8(nativeHandle);
        if (value == null || value.length == 0) return Optional.empty();
        return Optional.of(VlcAndroidRuntime.decodeUtf8(value, 4096, "native error"));
    }

    @Override
    public synchronized void close() {
        if (nativeHandle == 0L) return;
        long handle = nativeHandle;
        nativeHandle = 0L;
        NativeBridge.destroy(handle);
    }

    private void requireOpen() {
        if (nativeHandle == 0L) throw new IllegalStateException("VlcAndroidPlayer is closed.");
    }

    private void requireNative(boolean success, String fallback) {
        if (success) return;
        throw new VlcAndroidException(
                VlcAndroidException.Reason.NATIVE_CALL_FAILED,
                lastError().orElse(fallback));
    }

    private static byte[] encodeLocation(String location) {
        if (location == null || isBlank(location) || location.length() > MAX_LOCATION_CHARACTERS ||
                location.indexOf('\0') >= 0) {
            throw new IllegalArgumentException("location is blank, too long, or contains NUL.");
        }
        byte[] encoded = location.getBytes(StandardCharsets.UTF_8);
        if (encoded.length > MAX_UTF8_BYTES) {
            throw new IllegalArgumentException("location exceeds the UTF-8 boundary.");
        }
        return encoded;
    }

    private static String requireHeaderPart(String value, boolean name) {
        if (value == null || isBlank(value) || value.indexOf('\0') >= 0 ||
                value.indexOf('\r') >= 0 || value.indexOf('\n') >= 0) {
            throw new IllegalArgumentException("HTTP header contains an invalid name or value.");
        }
        if (name && !(value.equalsIgnoreCase("User-Agent") ||
                value.equalsIgnoreCase("Referer") || value.equalsIgnoreCase("Cookie"))) {
            throw new IllegalArgumentException("HTTP header is not supported by the pinned API.");
        }
        if (name && value.indexOf(':') >= 0) {
            throw new IllegalArgumentException("HTTP header name contains a colon.");
        }
        return value;
    }

    private static boolean isBlank(String value) {
        if (value.isEmpty()) return true;
        for (int index = 0; index < value.length(); index++) {
            if (!Character.isWhitespace(value.charAt(index))) return false;
        }
        return true;
    }
}
