// SPDX-License-Identifier: LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.desktop;

import java.util.concurrent.atomic.AtomicLong;

/** One retained same-process {@code IOSurfaceRef} acquired from a frame's IOSurface ID. */
public final class VlcMacIOSurface implements AutoCloseable {
    private final AtomicLong address;

    VlcMacIOSurface(long address) {
        if (address == 0) throw new IllegalArgumentException("IOSurface address must not be zero.");
        this.address = new AtomicLong(address);
    }

    /** Raw {@code IOSurfaceRef} address, valid until {@link #close()}. */
    public long address() {
        long current = address.get();
        if (current == 0) throw new IllegalStateException("The IOSurface lease is closed.");
        return current;
    }

    @Override
    public void close() {
        long current = address.getAndSet(0);
        if (current != 0) NativeBridge.releaseMacIosurface(current);
    }
}
