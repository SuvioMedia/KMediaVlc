// SPDX-License-Identifier: LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.desktop;

import java.nio.ByteBuffer;
import java.util.Optional;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

/** One bridge-owned video frame. Ownership is released exactly once. */
public final class VlcDesktopFrame implements VlcReleasableFrame {
    public static final int NO_FENCE = -1;

    private final long nativeFrame;
    private final long serial;
    private final long generation;
    private final long ptsMicroseconds;
    private final int width;
    private final int height;
    private final VlcPixelFormat pixelFormat;
    private final VlcSourceDynamicRange sourceDynamicRange;
    private final VlcNativeHandleType handleType;
    private final long platformHandle;
    private final AtomicInteger acquireFenceFd;
    private final int stride;
    private final int fourcc;
    private final int offset;
    private final long modifier;
    private final float sdrWhiteNits;
    private final float contentPeakNits;
    private final boolean premultipliedAlpha;
    private final ByteBuffer cpuPixels;
    private final AtomicBoolean released = new AtomicBoolean();

    VlcDesktopFrame(long[] values, ByteBuffer cpuPixels) {
        long ownedFrame = values != null && values.length > 0 ? values[0] : 0;
        try {
            if (values == null || values.length != 19) {
                throw new VlcRuntimeException(
                        VlcRuntimeException.Reason.NATIVE_CALL_FAILED,
                        "The VLC bridge returned malformed frame metadata.");
            }
            nativeFrame = requirePositive(values[0], "native frame");
            serial = requirePositive(values[1], "serial");
            generation = requirePositive(values[2], "output generation");
            ptsMicroseconds = values[3];
            width = requirePositiveInt(values[4], "width");
            height = requirePositiveInt(values[5], "height");
            pixelFormat = VlcPixelFormat.fromNative(Math.toIntExact(values[6]));
            sourceDynamicRange = VlcSourceDynamicRange.fromNative(Math.toIntExact(values[7]));
            handleType = VlcNativeHandleType.fromNative(Math.toIntExact(values[8]));
            platformHandle = values[9];
            acquireFenceFd = new AtomicInteger(Math.toIntExact(values[10]));
            stride = Math.toIntExact(values[11]);
            fourcc = Math.toIntExact(values[12]);
            offset = Math.toIntExact(values[13]);
            modifier = values[14];
            sdrWhiteNits = Float.intBitsToFloat(Math.toIntExact(values[15]));
            contentPeakNits = Float.intBitsToFloat(Math.toIntExact(values[16]));
            premultipliedAlpha = values[17] != 0;
            long cpuBytes = values[18];
            if (handleType == VlcNativeHandleType.CPU_ADDRESS) {
                if (cpuPixels == null || !cpuPixels.isDirect() || cpuPixels.capacity() != cpuBytes) {
                    throw new VlcRuntimeException(
                            VlcRuntimeException.Reason.NATIVE_CALL_FAILED,
                            "The VLC bridge returned an invalid CPU frame buffer.");
                }
                this.cpuPixels = cpuPixels.asReadOnlyBuffer();
            } else {
                this.cpuPixels = null;
            }
        } catch (RuntimeException failure) {
            if (values != null && values.length == 19 &&
                    values[10] >= 0 && values[10] <= Integer.MAX_VALUE) {
                NativeBridge.closeFence((int) values[10]);
            }
            if (ownedFrame > 0) NativeBridge.releaseFrame(ownedFrame, NO_FENCE);
            throw failure;
        }
    }

    @Override public long serial() { return serial; }
    @Override public long generation() { return generation; }
    public long ptsMicroseconds() { return ptsMicroseconds; }
    public int width() { return width; }
    public int height() { return height; }
    public VlcPixelFormat pixelFormat() { return pixelFormat; }
    public VlcSourceDynamicRange sourceDynamicRange() { return sourceDynamicRange; }
    public VlcNativeHandleType handleType() { return handleType; }
    public long platformHandle() { return platformHandle; }
    /** Transfers ownership of the acquire sync-file descriptor, or returns {@link #NO_FENCE}. */
    public int acquireFenceFd() { return acquireFenceFd.getAndSet(NO_FENCE); }
    public int stride() { return stride; }
    public int fourcc() { return fourcc; }
    public int offset() { return offset; }
    public long modifier() { return modifier; }
    public float sdrWhiteNits() { return sdrWhiteNits; }
    public float contentPeakNits() { return contentPeakNits; }
    public boolean premultipliedAlpha() { return premultipliedAlpha; }
    public Optional<ByteBuffer> cpuPixels() {
        return Optional.ofNullable(cpuPixels).map(ByteBuffer::asReadOnlyBuffer);
    }

    /**
     * Retains this frame's macOS IOSurface and exposes its same-process native address.
     *
     * <p>{@link #platformHandle()} remains the cross-process-safe IOSurface ID. Consumers whose
     * API explicitly requires an {@code IOSurfaceRef} pointer must keep the returned lease alive
     * until that API has retained the surface or finished consuming it.
     */
    public Optional<VlcMacIOSurface> retainMacIOSurface() {
        if (handleType != VlcNativeHandleType.IOSURFACE || released.get()) return Optional.empty();
        long address = NativeBridge.retainMacIosurface(platformHandle);
        return address == 0 ? Optional.empty() : Optional.of(new VlcMacIOSurface(address));
    }

    public void release(int releaseFenceFd) {
        if (released.compareAndSet(false, true)) {
            int unclaimedAcquireFence = acquireFenceFd.getAndSet(NO_FENCE);
            if (unclaimedAcquireFence >= 0) NativeBridge.closeFence(unclaimedAcquireFence);
            NativeBridge.releaseFrame(nativeFrame, releaseFenceFd);
        } else if (releaseFenceFd >= 0) {
            NativeBridge.closeFence(releaseFenceFd);
        }
    }

    @Override public void close() { release(NO_FENCE); }

    private static long requirePositive(long value, String name) {
        if (value <= 0) throw new IllegalArgumentException(name + " must be positive.");
        return value;
    }

    private static int requirePositiveInt(long value, String name) {
        int result = Math.toIntExact(value);
        if (result <= 0) throw new IllegalArgumentException(name + " must be positive.");
        return result;
    }
}
