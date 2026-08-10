// SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

package io.github.shusek.kmediavlc.runtime.desktop;

import static io.github.shusek.kmediavlc.runtime.desktop.VlcRuntimeException.Reason.BRIDGE_LOAD_FAILED;
import static io.github.shusek.kmediavlc.runtime.desktop.VlcRuntimeException.Reason.INCOMPATIBLE_BRIDGE;

import java.nio.ByteBuffer;
import java.nio.file.Path;
import java.util.concurrent.atomic.AtomicReference;

final class NativeBridge {
    private static final AtomicReference<Path> LOADED_PATH = new AtomicReference<>();

    private NativeBridge() {}

    static void load(Path bridgePath) {
        Path normalized = bridgePath.toAbsolutePath().normalize();
        Path loaded = LOADED_PATH.get();
        if (loaded != null) {
            if (!loaded.equals(normalized)) {
                throw new VlcRuntimeException(
                        INCOMPATIBLE_BRIDGE,
                        "One process cannot load two different KMediaVlc bridge runtimes.");
            }
            return;
        }
        synchronized (LOADED_PATH) {
            loaded = LOADED_PATH.get();
            if (loaded != null) {
                if (!loaded.equals(normalized)) {
                    throw new VlcRuntimeException(
                            INCOMPATIBLE_BRIDGE,
                            "One process cannot load two different KMediaVlc bridge runtimes.");
                }
                return;
            }
            try {
                System.load(normalized.toString());
                int abi = bridgeAbiVersion();
                if (abi != 2) {
                    throw new VlcRuntimeException(
                            INCOMPATIBLE_BRIDGE, "The loaded KMediaVlc bridge ABI is incompatible.");
                }
                LOADED_PATH.set(normalized);
            } catch (VlcRuntimeException failure) {
                throw failure;
            } catch (Throwable failure) {
                throw new VlcRuntimeException(BRIDGE_LOAD_FAILED, "The KMediaVlc bridge could not be loaded.", failure);
            }
        }
    }

    static native int bridgeAbiVersion();
    static native long defaultWindowsAdapterLuid();
    static native float[] inspectWindowsD3D11Frame(long adapterLuid, long sharedHandle);
    static native long[] inspectMacIosurfaceFrame(long iosurfaceId);
    static native long createPlayer(
            String libVlcPath,
            String pluginDirectory,
            int deliveryMode,
            boolean requestHdr,
            float sdrWhiteNits,
            float displayPeakNits,
            Object eventSink);
    static native boolean open(long player, String uri, String[] headers, boolean autoplay);
    static native boolean play(long player);
    static native boolean pause(long player);
    static native boolean stop(long player);
    static native boolean seek(long player, long timeMicroseconds, boolean fast);
    static native boolean setVolume(long player, float volume);
    static native boolean setRate(long player, float rate);
    static native boolean setLoop(long player, boolean loop);
    static native boolean resize(long player, int width, int height);
    static native boolean updateOutput(
            long player,
            int targetType,
            long generation,
            int width,
            int height,
            boolean requestHdr,
            float sdrWhiteNits,
            float peakNits,
            long deviceHandle,
            long commandQueue,
            String renderNode,
            int[] drmFormats,
            long[] drmModifiers,
            boolean acquireFences,
            boolean releaseFences);
    static native long[] snapshot(long player);
    static native long[] acquireLatestFrame(long player);
    static native ByteBuffer cpuFrameBuffer(long nativeFrame);
    static native String lastError(long player);
    static native void destroyPlayer(long player);
    static native void releaseFrame(long nativeFrame, int releaseFenceFd);
    static native void closeFence(int fenceFd);
}
