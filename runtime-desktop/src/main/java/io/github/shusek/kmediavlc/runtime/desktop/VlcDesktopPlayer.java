// SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

package io.github.shusek.kmediavlc.runtime.desktop;

import static io.github.shusek.kmediavlc.runtime.desktop.VlcRuntimeException.Reason.NATIVE_CALL_FAILED;
import static io.github.shusek.kmediavlc.runtime.desktop.VlcRuntimeException.Reason.PLAYER_INITIALIZATION_FAILED;

import java.nio.ByteBuffer;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.Supplier;

/** One optional libVLC 4 player using the KMediaVlc push-notify/pull-acquire bridge. */
public final class VlcDesktopPlayer implements AutoCloseable {
    private final long nativePlayer;
    private final VlcDesktopPlayerConfig config;
    private final NativeEventSink eventSink;
    private final AtomicBoolean closed = new AtomicBoolean();
    private final Object nativeCalls = new Object();

    private VlcDesktopPlayer(
            long nativePlayer, VlcDesktopPlayerConfig config, NativeEventSink eventSink) {
        this.nativePlayer = nativePlayer;
        this.config = config;
        this.eventSink = eventSink;
    }

    public static VlcDesktopPlayer create(
            VlcDesktopRuntimeResolution runtime, VlcDesktopPlayerConfig config) {
        Objects.requireNonNull(runtime, "runtime");
        Objects.requireNonNull(config, "config");
        if (!runtime.capabilities().frameDeliveryModes().contains(config.deliveryMode())) {
            throw new VlcRuntimeException(
                    PLAYER_INITIALIZATION_FAILED,
                    "The verified VLC payload does not support the requested delivery mode.");
        }
        NativeBridge.load(runtime.bridgePath());
        var sink = new NativeEventSink(config.listener());
        long handle = NativeBridge.createPlayer(
                runtime.libVlcPath().toString(),
                runtime.pluginDirectory().toString(),
                config.deliveryMode().nativeValue(),
                config.requestHdr(),
                config.sdrWhiteNits(),
                config.displayPeakNits(),
                sink);
        if (handle == 0) {
            throw new VlcRuntimeException(
                    PLAYER_INITIALIZATION_FAILED,
                    "The verified libVLC 4 runtime rejected player creation.");
        }
        return new VlcDesktopPlayer(handle, config, sink);
    }

    public VlcDesktopPlayerConfig config() { return config; }
    public long callbackFailures() { return eventSink.callbackFailures.get(); }

    public boolean open(String uri, Map<String, String> requestHeaders, boolean autoplay) {
        requireOpen();
        if (uri == null || uri.isBlank() || uri.indexOf('\0') >= 0) {
            throw new IllegalArgumentException("uri must be non-blank and must not contain NUL.");
        }
        Objects.requireNonNull(requestHeaders, "requestHeaders");
        String[] headers = new String[requestHeaders.size() * 2];
        int index = 0;
        for (Map.Entry<String, String> entry : requestHeaders.entrySet()) {
            String name = requireHeaderPart(entry.getKey(), "header name");
            String value = requireHeaderPart(entry.getValue(), "header value");
            if (name.indexOf(':') >= 0 || value.indexOf('\r') >= 0 || value.indexOf('\n') >= 0) {
                throw new IllegalArgumentException("requestHeaders contain an invalid name or value.");
            }
            headers[index++] = name;
            headers[index++] = value;
        }
        return callNative(() -> NativeBridge.open(nativePlayer, uri, headers, autoplay));
    }

    public boolean play() { return callNative(() -> NativeBridge.play(nativePlayer)); }
    public boolean pause() { return callNative(() -> NativeBridge.pause(nativePlayer)); }
    public boolean stop() { return callNative(() -> NativeBridge.stop(nativePlayer)); }
    public boolean seek(long timeMicroseconds, boolean fast) {
        return callNative(() -> NativeBridge.seek(nativePlayer, Math.max(0, timeMicroseconds), fast));
    }
    public boolean setVolume(float volume) {
        return callNative(() -> NativeBridge.setVolume(nativePlayer, Math.clamp(volume, 0f, 1f)));
    }
    public boolean setRate(float rate) {
        if (!Float.isFinite(rate) || rate <= 0f) throw new IllegalArgumentException("rate must be positive.");
        return callNative(() -> NativeBridge.setRate(nativePlayer, rate));
    }
    public boolean setLoop(boolean loop) { return callNative(() -> NativeBridge.setLoop(nativePlayer, loop)); }
    public boolean resize(int width, int height) {
        if (width <= 0 || height <= 0) return false;
        return callNative(() -> NativeBridge.resize(nativePlayer, width, height));
    }
    public boolean updateOutput(VlcOutputTarget target) {
        Objects.requireNonNull(target, "target");
        int targetType;
        long deviceHandle = 0;
        long commandQueue = 0;
        String renderNode = null;
        int[] drmFormats = null;
        long[] drmModifiers = null;
        boolean acquireFences = false;
        boolean releaseFences = false;
        if (target instanceof VlcUnavailableOutputTarget) {
            targetType = 0;
        } else if (target instanceof VlcWindowsOutputTarget windows) {
            targetType = 1;
            deviceHandle = windows.adapterLuid();
        } else if (target instanceof VlcMacOutputTarget mac) {
            targetType = 2;
            deviceHandle = mac.metalDevice();
            commandQueue = mac.metalCommandQueue();
        } else if (target instanceof VlcLinuxOutputTarget linux) {
            targetType = 3;
            renderNode = linux.renderNode();
            drmFormats = linux.drmFormats();
            drmModifiers = linux.drmModifiers();
            acquireFences = linux.acquireFences();
            releaseFences = linux.releaseFences();
        } else {
            throw new IllegalArgumentException("Unknown VLC output target.");
        }
        int finalTargetType = targetType;
        long finalDeviceHandle = deviceHandle;
        long finalCommandQueue = commandQueue;
        String finalRenderNode = renderNode;
        int[] finalDrmFormats = drmFormats;
        long[] finalDrmModifiers = drmModifiers;
        boolean finalAcquireFences = acquireFences;
        boolean finalReleaseFences = releaseFences;
        return callNative(() -> NativeBridge.updateOutput(
                    nativePlayer,
                    finalTargetType,
                    target.generation(),
                    target.width(),
                    target.height(),
                    target.hdr(),
                    target.sdrWhiteNits(),
                    target.peakNits(),
                    finalDeviceHandle,
                    finalCommandQueue,
                    finalRenderNode,
                    finalDrmFormats,
                    finalDrmModifiers,
                    finalAcquireFences,
                    finalReleaseFences));
    }

    public VlcPlayerSnapshot snapshot() {
        long[] values = callNative(() -> NativeBridge.snapshot(nativePlayer));
        if (values == null || values.length != 8) {
            throw nativeFailure("The VLC bridge returned a malformed player snapshot.");
        }
        return new VlcPlayerSnapshot(
                VlcPlaybackState.fromNative(Math.toIntExact(values[0])),
                values[1], values[2], values[3], Math.toIntExact(values[4]),
                Math.toIntExact(values[5]), Math.toIntExact(values[6]), values[7] != 0);
    }

    /** Pulls the newest frame after a non-owning push notification. */
    public Optional<VlcDesktopFrame> acquireLatestFrame() {
        long[] values = callNative(() -> NativeBridge.acquireLatestFrame(nativePlayer));
        if (values == null) return Optional.empty();
        ByteBuffer cpuPixels = values.length == 19 && values[8] == VlcNativeHandleType.CPU_ADDRESS.nativeValue()
                ? callNative(() -> NativeBridge.cpuFrameBuffer(values[0]))
                : null;
        return Optional.of(new VlcDesktopFrame(values, cpuPixels));
    }

    public Optional<String> lastError() {
        synchronized (nativeCalls) {
            if (closed.get()) return Optional.empty();
            return Optional.ofNullable(NativeBridge.lastError(nativePlayer))
                    .filter(value -> !value.isBlank());
        }
    }

    @Override
    public void close() {
        synchronized (nativeCalls) {
            if (!closed.compareAndSet(false, true)) return;
            eventSink.disable();
            NativeBridge.destroyPlayer(nativePlayer);
        }
    }

    private void requireOpen() {
        if (closed.get()) throw new IllegalStateException("VlcDesktopPlayer is closed.");
    }

    private <T> T callNative(Supplier<T> call) {
        synchronized (nativeCalls) {
            requireOpen();
            return call.get();
        }
    }

    private VlcRuntimeException nativeFailure(String fallback) {
        return new VlcRuntimeException(NATIVE_CALL_FAILED, lastError().orElse(fallback));
    }

    private static String requireHeaderPart(String value, String name) {
        if (value == null || value.isBlank() || value.indexOf('\0') >= 0) {
            throw new IllegalArgumentException(name + " must be non-blank and must not contain NUL.");
        }
        return value;
    }

    /** Entry points called from JNI. They never transfer frame ownership. */
    private static final class NativeEventSink {
        private final AtomicBoolean enabled = new AtomicBoolean(true);
        private final AtomicLong callbackFailures = new AtomicLong();
        private final VlcPlayerListener listener;

        NativeEventSink(VlcPlayerListener listener) { this.listener = listener; }
        void disable() { enabled.set(false); }

        @SuppressWarnings("unused")
        private void onFrameAvailable(long serial, long generation) {
            if (!enabled.get()) return;
            try { listener.onFrameAvailable(serial, generation); }
            catch (RuntimeException ignored) { callbackFailures.incrementAndGet(); }
        }

        @SuppressWarnings("unused")
        private void onPlaybackStateChanged(int state, long mediaGeneration) {
            if (!enabled.get()) return;
            try { listener.onPlaybackStateChanged(VlcPlaybackState.fromNative(state), mediaGeneration); }
            catch (RuntimeException ignored) { callbackFailures.incrementAndGet(); }
        }
    }
}
