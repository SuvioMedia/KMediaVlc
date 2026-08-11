// SPDX-License-Identifier: LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.desktop;

import java.util.Objects;
import java.util.Optional;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Bounded single-consumer transport used by GPU push and CPU pull producers.
 *
 * <p>Notifications never transfer frame ownership. The consumer calls {@link #acquireLatest()} and
 * closes the returned frame. Replaced, skipped, and post-close frames are always released.
 */
public final class LatestFrameTransport<T extends VlcReleasableFrame> implements AutoCloseable {
    @FunctionalInterface
    public interface FrameAvailableListener {
        void onFrameAvailable(long serial, long generation);
    }

    public interface Subscription extends AutoCloseable {
        @Override
        void close();
    }

    private final AtomicReference<T> pending = new AtomicReference<>();
    private final AtomicReference<FrameAvailableListener> listener = new AtomicReference<>();
    private final AtomicBoolean closed = new AtomicBoolean();
    private final AtomicLong droppedFrames = new AtomicLong();
    private final AtomicLong listenerFailures = new AtomicLong();

    /** Publishes a frame and releases the previous unacquired frame, if any. */
    public boolean publish(T frame) {
        Objects.requireNonNull(frame, "frame");
        if (closed.get()) {
            frame.close();
            return false;
        }

        T skipped = pending.getAndSet(frame);
        if (skipped != null) {
            droppedFrames.incrementAndGet();
            skipped.close();
        }

        if (closed.get() && pending.compareAndSet(frame, null)) {
            frame.close();
            return false;
        }

        FrameAvailableListener currentListener = listener.get();
        if (currentListener != null && pending.get() == frame) {
            try {
                currentListener.onFrameAvailable(frame.serial(), frame.generation());
            } catch (RuntimeException ignored) {
                listenerFailures.incrementAndGet();
            }
        }
        return true;
    }

    /** Transfers ownership of the latest pending frame to the single consumer. */
    public Optional<T> acquireLatest() {
        return Optional.ofNullable(pending.getAndSet(null));
    }

    /** Registers the only notification consumer. Closing the subscription unregisters it. */
    public Subscription subscribe(FrameAvailableListener newListener) {
        Objects.requireNonNull(newListener, "newListener");
        if (closed.get()) throw new IllegalStateException("Frame transport is closed.");
        if (!listener.compareAndSet(null, newListener)) {
            throw new IllegalStateException("Frame transport already has a consumer.");
        }
        if (closed.get() && listener.compareAndSet(newListener, null)) {
            throw new IllegalStateException("Frame transport is closed.");
        }
        return () -> listener.compareAndSet(newListener, null);
    }

    public long droppedFrames() {
        return droppedFrames.get();
    }

    public long listenerFailures() {
        return listenerFailures.get();
    }

    public boolean isClosed() {
        return closed.get();
    }

    @Override
    public void close() {
        if (!closed.compareAndSet(false, true)) return;
        listener.set(null);
        T frame = pending.getAndSet(null);
        if (frame != null) frame.close();
    }
}
