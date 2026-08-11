// SPDX-License-Identifier: LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.desktop;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;
import org.junit.jupiter.api.Test;

class LatestFrameTransportTest {
    @Test
    void skippedAndPendingFramesAreReleasedExactlyOnce() {
        var transport = new LatestFrameTransport<TestFrame>();
        var first = new TestFrame(1, 7);
        var second = new TestFrame(2, 7);

        assertTrue(transport.publish(first));
        assertTrue(transport.publish(second));
        assertEquals(1, first.closes.get());
        assertEquals(1, transport.droppedFrames());

        var acquired = transport.acquireLatest().orElseThrow();
        assertEquals(second, acquired);
        assertEquals(0, second.closes.get());
        acquired.close();
        transport.close();
        assertEquals(1, second.closes.get());
    }

    @Test
    void notificationDoesNotTransferOwnership() {
        var transport = new LatestFrameTransport<TestFrame>();
        var serial = new AtomicLong();
        var generation = new AtomicLong();
        try (var ignored = transport.subscribe((value, output) -> {
            serial.set(value);
            generation.set(output);
        })) {
            assertTrue(transport.publish(new TestFrame(9, 4)));
            assertEquals(9, serial.get());
            assertEquals(4, generation.get());
            assertTrue(transport.acquireLatest().isPresent());
        }
    }

    @Test
    void transportHasOneConsumerAndContainsListenerFailures() {
        var transport = new LatestFrameTransport<TestFrame>();
        var subscription = transport.subscribe((serial, generation) -> {
            throw new IllegalStateException("consumer failure");
        });
        assertThrows(IllegalStateException.class, () -> transport.subscribe((serial, generation) -> {}));
        assertTrue(transport.publish(new TestFrame(1, 1)));
        assertEquals(1, transport.listenerFailures());
        subscription.close();
        transport.close();
    }

    @Test
    void postClosePublicationIsRejectedAndReleased() {
        var transport = new LatestFrameTransport<TestFrame>();
        transport.close();
        var frame = new TestFrame(1, 1);
        assertFalse(transport.publish(frame));
        assertEquals(1, frame.closes.get());
        assertThrows(IllegalStateException.class, () -> transport.subscribe((serial, generation) -> {}));
    }

    private static final class TestFrame implements VlcReleasableFrame {
        private final long serial;
        private final long generation;
        private final AtomicInteger closes = new AtomicInteger();

        private TestFrame(long serial, long generation) {
            this.serial = serial;
            this.generation = generation;
        }

        @Override
        public long serial() {
            return serial;
        }

        @Override
        public long generation() {
            return generation;
        }

        @Override
        public void close() {
            if (closes.incrementAndGet() != 1) throw new IllegalStateException("double close");
        }
    }
}
