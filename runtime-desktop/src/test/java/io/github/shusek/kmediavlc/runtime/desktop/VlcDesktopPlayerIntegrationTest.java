// SPDX-License-Identifier: LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.desktop;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.awt.Color;
import java.awt.image.BufferedImage;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Semaphore;
import java.util.concurrent.TimeUnit;
import javax.imageio.ImageIO;
import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

final class VlcDesktopPlayerIntegrationTest {
    private static final int DRM_FORMAT_ABGR8888 = 0x34324241;

    @TempDir Path temporaryDirectory;

    @Test
    void bundledRuntimeExtractsAndPublishesCpuPullFrame() throws Exception {
        Assumptions.assumeTrue(Boolean.getBoolean("kmediavlc.test.bundledRuntime"));
        Path image = createImage();
        var inspection = VlcDesktopRuntime.inspectBundled();
        assertTrue(inspection.available());
        var runtime = VlcDesktopRuntime.resolveBundled(
                temporaryDirectory.resolve("bundled-runtime").toAbsolutePath());
        var signal = new CountDownLatch(1);
        var config = new VlcDesktopPlayerConfig(
                VlcFrameDeliveryMode.CPU_PULL,
                false,
                203f,
                203f,
                new VlcPlayerListener() {
                    @Override
                    public void onFrameAvailable(long serial, long outputGeneration) {
                        signal.countDown();
                    }
                });

        try (var player = VlcDesktopPlayer.create(runtime, config)) {
            assertTrue(player.open(image.toUri().toString(), Map.of(), true));
            assertTrue(
                    signal.await(15, TimeUnit.SECONDS),
                    () -> timeoutDiagnostics(player, "No bundled-runtime CPU frame arrived."));
            try (var frame = player.acquireLatestFrame().orElseThrow()) {
                assertEquals(VlcNativeHandleType.CPU_ADDRESS, frame.handleType());
                assertEquals(VlcPixelFormat.RGBA8_SRGB, frame.pixelFormat());
                assertEquals(64, frame.width());
                assertEquals(36, frame.height());
            }
        }
    }

    @Test
    void pinnedVideoLanFixturePublishesCpuPullFrame() throws Exception {
        var fixture = fixture();
        var signal = new CountDownLatch(1);
        var config = new VlcDesktopPlayerConfig(
                VlcFrameDeliveryMode.CPU_PULL,
                false,
                203f,
                203f,
                new VlcPlayerListener() {
                    @Override
                    public void onFrameAvailable(long serial, long outputGeneration) {
                        signal.countDown();
                    }
                });

        try (var player = VlcDesktopPlayer.create(fixture.runtime(), config)) {
            assertTrue(player.open(fixture.image().toUri().toString(), Map.of(), true));
            assertTrue(
                    signal.await(15, TimeUnit.SECONDS),
                    () -> timeoutDiagnostics(player, "No CPU frame arrived."));
            try (var frame = player.acquireLatestFrame().orElseThrow()) {
                assertEquals(VlcNativeHandleType.CPU_ADDRESS, frame.handleType());
                assertEquals(VlcPixelFormat.RGBA8_SRGB, frame.pixelFormat());
                assertEquals(64, frame.width());
                assertEquals(36, frame.height());
                assertTrue(frame.cpuPixels().orElseThrow().remaining() >= 64 * 36 * 4);
            }
        }
    }

    @Test
    void fakeLibVlcCpuPullPublishesVisibleDimensionsAndPreservesEndedState() throws Exception {
        String bridge = System.getProperty("kmediavlc.test.nativeBridge");
        String fakeLibVlc = System.getProperty("kmediavlc.test.fakeLibVlc");
        Assumptions.assumeTrue(
                bridge != null && fakeLibVlc != null, "The native CPU-pull fixture is opt-in.");
        Path fakeLibVlcPath = Path.of(fakeLibVlc).toAbsolutePath();
        Path plugins = fakeLibVlcPath.getParent();
        assertNotNull(plugins);
        var runtime = new VlcDesktopRuntimeResolution(
                Path.of(bridge).toAbsolutePath(),
                fakeLibVlcPath,
                plugins,
                "fake-libvlc-cpu-visible-dimensions",
                new VlcRuntimeCapabilities(
                        4,
                        2,
                        "4.0.0-dev",
                        "b5536cdea24b313ba9215eacfbd7fa3295d7f3ee",
                        Set.of(VlcFrameDeliveryMode.GPU_PUSH, VlcFrameDeliveryMode.CPU_PULL),
                        Set.of(VlcRenderEngine.OPENGL),
                        false));
        var frameSignal = new CountDownLatch(1);
        var endedSignal = new CountDownLatch(1);
        var config = new VlcDesktopPlayerConfig(
                VlcFrameDeliveryMode.CPU_PULL,
                false,
                203f,
                203f,
                new VlcPlayerListener() {
                    @Override
                    public void onFrameAvailable(long serial, long outputGeneration) {
                        frameSignal.countDown();
                    }

                    @Override
                    public void onPlaybackStateChanged(
                            VlcPlaybackState state, long mediaGeneration) {
                        if (state == VlcPlaybackState.ENDED) endedSignal.countDown();
                    }
                });

        try (var player = VlcDesktopPlayer.create(runtime, config)) {
            assertTrue(player.open("test://cpu-visible-dimensions/eos-terminal", Map.of(), true));
            assertTrue(
                    frameSignal.await(10, TimeUnit.SECONDS),
                    () -> timeoutDiagnostics(player, "The fake CPU-pull frame did not arrive."));
            try (var frame = player.acquireLatestFrame().orElseThrow()) {
                assertEquals(VlcNativeHandleType.CPU_ADDRESS, frame.handleType());
                assertEquals(VlcPixelFormat.RGBA8_SRGB, frame.pixelFormat());
                assertEquals(128, frame.width());
                assertEquals(72, frame.height());
                assertTrue(frame.stride() >= 128 * 4);
                assertTrue(frame.cpuPixels().orElseThrow().remaining() >= frame.stride() * 72);
            }
            assertTrue(
                    endedSignal.await(10, TimeUnit.SECONDS),
                    () -> timeoutDiagnostics(player, "The fake EOS state did not arrive."));
            var snapshot = player.snapshot();
            assertEquals(VlcPlaybackState.ENDED, snapshot.state());
            assertEquals(128, snapshot.videoWidth());
            assertEquals(72, snapshot.videoHeight());
            assertTrue(player.stop());
            assertEquals(VlcPlaybackState.STOPPED, player.snapshot().state());
        }
    }

    @Test
    void pinnedVideoLanFixtureImportsLinuxDmaBufsAndReturnsExplicitFences() throws Exception {
        Assumptions.assumeTrue(System.getProperty("os.name", "").toLowerCase().contains("linux"));
        String renderNode = System.getProperty("kmediavlc.test.linuxRenderNode");
        Assumptions.assumeTrue(renderNode != null, "The physical Linux DRM probe is opt-in.");
        var fixture = fixture();
        NativeBridge.load(fixture.runtime().bridgePath());
        long[] modifiers = NativeBridge.linuxDmaBufModifiers(renderNode);
        Assumptions.assumeTrue(
                modifiers != null && modifiers.length > 0,
                "The render node has no concrete ABGR8888 modifier with explicit fences.");
        int[] formats = new int[modifiers.length];
        Arrays.fill(formats, DRM_FORMAT_ABGR8888);
        var signal = new Semaphore(0);
        var config = new VlcDesktopPlayerConfig(
                VlcFrameDeliveryMode.GPU_PUSH,
                false,
                203f,
                203f,
                new VlcPlayerListener() {
                    @Override
                    public void onFrameAvailable(long serial, long outputGeneration) {
                        signal.release();
                    }
                });

        try (var player = VlcDesktopPlayer.create(fixture.runtime(), config)) {
            for (int iteration = 0; iteration < 7; iteration++) {
                long generation = 70L + iteration;
                signal.drainPermits();
                assertTrue(player.updateOutput(new VlcLinuxOutputTarget(
                        generation,
                        128,
                        72,
                        false,
                        203f,
                        203f,
                        renderNode,
                        formats,
                        modifiers,
                        true,
                        true)));
                assertTrue(player.open(fixture.image().toUri().toString(), Map.of(), true));
                try (var frame = awaitFrame(
                        player,
                        signal,
                        generation,
                        128,
                        72,
                        "Real libVLC did not publish an importable Linux DMA-BUF.")) {
                    assertEquals(VlcNativeHandleType.DMABUF, frame.handleType());
                    assertEquals(VlcPixelFormat.RGBA8_SRGB, frame.pixelFormat());
                    assertEquals(DRM_FORMAT_ABGR8888, frame.fourcc());
                    assertTrue(frame.platformHandle() >= 0);
                    assertTrue(frame.platformHandle() <= Integer.MAX_VALUE);
                    assertTrue(Arrays.stream(modifiers).anyMatch(value -> value == frame.modifier()));
                    int acquireFence = frame.acquireFenceFd();
                    assertTrue(acquireFence >= 0);
                    int[] inspection = NativeBridge.inspectLinuxDmaBufFrame(
                            renderNode,
                            frame.platformHandle(),
                            acquireFence,
                            frame.width(),
                            frame.height(),
                            frame.stride(),
                            frame.fourcc(),
                            frame.offset(),
                            frame.modifier());
                    assertNotNull(inspection, "A separate EGL context must import and read the DMA-BUF.");
                    assertEquals(5, inspection.length);
                    int releaseFence = inspection[0];
                    try {
                        assertTrue(releaseFence >= 0);
                        assertTrue(inspection[1] >= 128, "The imported center pixel must remain red.");
                        assertTrue(inspection[2] <= 128);
                        assertTrue(inspection[3] <= 128);
                        assertTrue(inspection[4] >= 200);
                        if (iteration == 2) {
                            // Exercise the fail-closed retirement path once,
                            // then require four more imported frames.
                            NativeBridge.closeFence(releaseFence);
                            releaseFence = VlcDesktopFrame.NO_FENCE;
                            frame.release(VlcDesktopFrame.NO_FENCE);
                        } else {
                            int transferredFence = releaseFence;
                            releaseFence = VlcDesktopFrame.NO_FENCE;
                            frame.release(transferredFence);
                        }
                    } finally {
                        if (releaseFence >= 0) NativeBridge.closeFence(releaseFence);
                    }
                }
            }
            assertTrue(player.stop());
        }
    }

    @Test
    void pinnedVideoLanFixturePublishesAndReplacesRealMacIosurfaceFrames() throws Exception {
        Assumptions.assumeTrue(System.getProperty("os.name", "").toLowerCase().contains("mac"));
        var fixture = fixture();
        var signal = new Semaphore(0);
        var config = new VlcDesktopPlayerConfig(
                VlcFrameDeliveryMode.GPU_PUSH,
                false,
                203f,
                203f,
                new VlcPlayerListener() {
                    @Override
                    public void onFrameAvailable(long serial, long outputGeneration) {
                        signal.release();
                    }
                });

        try (var player = VlcDesktopPlayer.create(fixture.runtime(), config)) {
            assertTrue(player.updateOutput(new VlcMacOutputTarget(
                    41,
                    128,
                    72,
                    false,
                    203f,
                    203f,
                    1,
                    1)));
            assertTrue(player.open(fixture.image().toUri().toString(), Map.of(), true));
            assertTrue(
                    signal.tryAcquire(20, TimeUnit.SECONDS),
                    () -> timeoutDiagnostics(player, "Real libVLC published no macOS IOSurface."));
            try (var frame = player.acquireLatestFrame().orElseThrow()) {
                assertEquals(VlcNativeHandleType.IOSURFACE, frame.handleType());
                assertEquals(VlcPixelFormat.RGBA8_SRGB, frame.pixelFormat());
                assertEquals(VlcSourceDynamicRange.SDR, frame.sourceDynamicRange());
                assertEquals(41, frame.generation());
                assertEquals(128, frame.width());
                assertEquals(72, frame.height());
                assertTrue(frame.platformHandle() > 0);
                assertTrue(frame.stride() >= 128 * 4);
                assertEquals(0x42475241, frame.fourcc(), "kCVPixelFormatType_32BGRA");
                long[] inspection = NativeBridge.inspectMacIosurfaceFrame(frame.platformHandle());
                assertNotNull(inspection);
                assertEquals(128, inspection[0]);
                assertEquals(72, inspection[1]);
                assertEquals(4, inspection[2]);
                assertEquals(frame.stride(), inspection[3]);
                assertTrue(inspection[4] >= frame.stride() * 72L);
                assertEquals(frame.fourcc(), inspection[5]);
            }

            signal.drainPermits();
            assertTrue(player.updateOutput(new VlcMacOutputTarget(
                    42,
                    96,
                    54,
                    false,
                    203f,
                    203f,
                    1,
                    1)));
            assertTrue(player.open(fixture.image().toUri().toString(), Map.of(), true));
            try (var frame = awaitFrame(
                    player,
                    signal,
                    42,
                    96,
                    54,
                    "Real libVLC did not replace its IOSurface output.")) {
                assertEquals(VlcNativeHandleType.IOSURFACE, frame.handleType());
                assertEquals(VlcPixelFormat.RGBA8_SRGB, frame.pixelFormat());
                assertEquals(42, frame.generation());
                assertEquals(96, frame.width());
                assertEquals(54, frame.height());
                long[] inspection = NativeBridge.inspectMacIosurfaceFrame(frame.platformHandle());
                assertNotNull(inspection);
                assertEquals(96, inspection[0]);
                assertEquals(54, inspection[1]);
                assertEquals(frame.stride(), inspection[3]);
            }
            assertTrue(player.stop());
        }
    }

    @Test
    void pinnedVideoLanFixtureKeepsSdrD3D11FrameSrgbOnHdrHost() throws Exception {
        Assumptions.assumeTrue(System.getProperty("os.name", "").toLowerCase().contains("windows"));
        var fixture = fixture();
        NativeBridge.load(fixture.runtime().bridgePath());
        long adapterLuid = NativeBridge.defaultWindowsAdapterLuid();
        Assumptions.assumeTrue(adapterLuid != 0, "No hardware DXGI adapter is available.");
        var signal = new CountDownLatch(1);
        var config = new VlcDesktopPlayerConfig(
                VlcFrameDeliveryMode.GPU_PUSH,
                true,
                203f,
                1_000f,
                new VlcPlayerListener() {
                    @Override
                    public void onFrameAvailable(long serial, long outputGeneration) {
                        signal.countDown();
                    }
                });

        try (var player = VlcDesktopPlayer.create(fixture.runtime(), config)) {
            assertTrue(player.updateOutput(new VlcWindowsOutputTarget(
                    17,
                    128,
                    72,
                    true,
                    203f,
                    1_000f,
                    adapterLuid)));
            assertTrue(player.open(fixture.image().toUri().toString(), Map.of(), true));
            assertTrue(
                    signal.await(15, TimeUnit.SECONDS),
                    () -> timeoutDiagnostics(player, "No D3D11 frame arrived."));
            try (var frame = player.acquireLatestFrame().orElseThrow()) {
                assertEquals(VlcNativeHandleType.D3D11_SHARED_HANDLE, frame.handleType());
                assertEquals(VlcPixelFormat.RGBA8_SRGB, frame.pixelFormat());
                assertEquals(VlcSourceDynamicRange.SDR, frame.sourceDynamicRange());
                assertEquals(17, frame.generation());
                assertEquals(128, frame.width());
                assertEquals(72, frame.height());
                assertTrue(frame.platformHandle() != 0);
                assertTrue(frame.cpuPixels().isEmpty());
                float[] inspection = NativeBridge.inspectWindowsD3D11Frame(
                        adapterLuid, frame.platformHandle());
                assertNotNull(inspection, "A second D3D11 device must import and lock the shared frame.");
                assertEquals(7, inspection.length);
                assertEquals(28, Math.round(inspection[0]), "DXGI_FORMAT_R8G8B8A8_UNORM");
                assertEquals(128, Math.round(inspection[1]));
                assertEquals(72, Math.round(inspection[2]));
                assertTrue(Float.isFinite(inspection[3]) && inspection[3] > 0.25f);
                assertTrue(Float.isFinite(inspection[4]));
                assertTrue(Float.isFinite(inspection[5]));
                assertTrue(Float.isFinite(inspection[6]) && inspection[6] > 0.9f);
            }
        }
    }

    @Test
    void fakeLibVlcPublishesRealSdrAndHdrMacIosurfaceFrames() throws Exception {
        Assumptions.assumeTrue(System.getProperty("os.name", "").toLowerCase().contains("mac"));
        String bridge = System.getProperty("kmediavlc.test.nativeBridge");
        String fakeLibVlc = System.getProperty("kmediavlc.test.fakeLibVlc");
        Assumptions.assumeTrue(bridge != null && fakeLibVlc != null, "The native macOS fixture is opt-in.");
        Path fakeLibVlcPath = Path.of(fakeLibVlc).toAbsolutePath();
        Path plugins = fakeLibVlcPath.getParent();
        assertNotNull(plugins);
        var runtime = new VlcDesktopRuntimeResolution(
                Path.of(bridge).toAbsolutePath(),
                fakeLibVlcPath,
                plugins,
                "fake-libvlc-macos-callback-test",
                new VlcRuntimeCapabilities(
                        4,
                        2,
                        "4.0.0-dev",
                        "b5536cdea24b313ba9215eacfbd7fa3295d7f3ee",
                        Set.of(VlcFrameDeliveryMode.GPU_PUSH, VlcFrameDeliveryMode.CPU_PULL),
                        Set.of(VlcRenderEngine.OPENGL),
                        false));
        var signal = new Semaphore(0);
        var config = new VlcDesktopPlayerConfig(
                VlcFrameDeliveryMode.GPU_PUSH,
                true,
                203f,
                1_000f,
                new VlcPlayerListener() {
                    @Override
                    public void onFrameAvailable(long serial, long outputGeneration) {
                        signal.release();
                    }
                });

        try (var player = VlcDesktopPlayer.create(runtime, config)) {
            assertTrue(player.updateOutput(new VlcMacOutputTarget(
                    31,
                    96,
                    54,
                    false,
                    203f,
                    203f,
                    1,
                    1)));
            assertTrue(player.open("test://macos-iosurface", Map.of(), true));
            assertTrue(signal.tryAcquire(10, TimeUnit.SECONDS), () -> timeoutDiagnostics(
                    player, "The fake libVLC callback sequence published no IOSurface."));
            try (var frame = player.acquireLatestFrame().orElseThrow()) {
                assertEquals(VlcNativeHandleType.IOSURFACE, frame.handleType());
                assertEquals(VlcPixelFormat.RGBA8_SRGB, frame.pixelFormat());
                assertEquals(VlcSourceDynamicRange.SDR, frame.sourceDynamicRange());
                assertEquals(31, frame.generation());
                assertEquals(96, frame.width());
                assertEquals(54, frame.height());
                assertTrue(frame.platformHandle() > 0);
                assertTrue(frame.stride() >= 96 * 4);
                assertEquals(0x42475241, frame.fourcc(), "kCVPixelFormatType_32BGRA");
                long[] inspection = NativeBridge.inspectMacIosurfaceFrame(frame.platformHandle());
                assertNotNull(inspection);
                assertEquals(6, inspection.length);
                assertEquals(96, inspection[0]);
                assertEquals(54, inspection[1]);
                assertEquals(4, inspection[2]);
                assertEquals(frame.stride(), inspection[3]);
                assertTrue(inspection[4] >= frame.stride() * 54L);
                assertEquals(frame.fourcc(), inspection[5]);
            }

            assertTrue(player.updateOutput(new VlcMacOutputTarget(
                    32,
                    96,
                    54,
                    true,
                    203f,
                    1_000f,
                    1,
                    1)));
            assertTrue(player.open("test://macos-iosurface-hdr", Map.of(), true));
            assertTrue(signal.tryAcquire(10, TimeUnit.SECONDS), () -> timeoutDiagnostics(
                    player, "The fake HDR10 callback sequence published no FP16 IOSurface."));
            try (var frame = player.acquireLatestFrame().orElseThrow()) {
                assertEquals(VlcNativeHandleType.IOSURFACE, frame.handleType());
                assertEquals(VlcPixelFormat.RGBA16F_LINEAR_SRGB, frame.pixelFormat());
                assertEquals(VlcSourceDynamicRange.HDR10, frame.sourceDynamicRange());
                assertEquals(32, frame.generation());
                assertEquals(96, frame.width());
                assertEquals(54, frame.height());
                assertTrue(frame.platformHandle() > 0);
                assertTrue(frame.stride() >= 96 * 8);
                assertEquals(0x52476841, frame.fourcc(), "kCVPixelFormatType_64RGBAHalf");
                assertTrue(frame.contentPeakNits() >= 1_000f);
                long[] inspection = NativeBridge.inspectMacIosurfaceFrame(frame.platformHandle());
                assertNotNull(inspection);
                assertEquals(6, inspection.length);
                assertEquals(96, inspection[0]);
                assertEquals(54, inspection[1]);
                assertEquals(8, inspection[2]);
                assertEquals(frame.stride(), inspection[3]);
                assertTrue(inspection[4] >= frame.stride() * 54L);
                assertEquals(frame.fourcc(), inspection[5]);
            }
        }
    }

    @Test
    void pinnedChromiumHdr10FixturePublishesFp16D3D11Frame() throws Exception {
        Assumptions.assumeTrue(System.getProperty("os.name", "").toLowerCase().contains("windows"));
        String mediaPath = System.getProperty("kmediavlc.test.hdrMedia");
        Assumptions.assumeTrue(mediaPath != null, "The immutable Chromium HDR10 fixture is opt-in.");
        Path media = Path.of(mediaPath).toAbsolutePath();
        Assumptions.assumeTrue(media.toFile().isFile(), "The Chromium HDR10 fixture is missing.");
        var fixture = fixture();
        NativeBridge.load(fixture.runtime().bridgePath());
        long adapterLuid = NativeBridge.defaultWindowsAdapterLuid();
        Assumptions.assumeTrue(adapterLuid != 0, "No hardware DXGI adapter is available.");
        var signal = new CountDownLatch(1);
        var config = new VlcDesktopPlayerConfig(
                VlcFrameDeliveryMode.GPU_PUSH,
                true,
                203f,
                1_000f,
                new VlcPlayerListener() {
                    @Override
                    public void onFrameAvailable(long serial, long outputGeneration) {
                        signal.countDown();
                    }
                });

        try (var player = VlcDesktopPlayer.create(fixture.runtime(), config)) {
            assertTrue(player.updateOutput(new VlcWindowsOutputTarget(
                    23,
                    320,
                    180,
                    true,
                    203f,
                    1_000f,
                    adapterLuid)));
            assertTrue(player.open(media.toUri().toString(), Map.of(), true));
            assertTrue(
                    signal.await(20, TimeUnit.SECONDS),
                    () -> timeoutDiagnostics(player, "No HDR10 D3D11 frame arrived."));
            try (var frame = player.acquireLatestFrame().orElseThrow()) {
                assertEquals(VlcNativeHandleType.D3D11_SHARED_HANDLE, frame.handleType());
                assertEquals(VlcPixelFormat.RGBA16F_LINEAR_SRGB, frame.pixelFormat());
                assertEquals(VlcSourceDynamicRange.HDR10, frame.sourceDynamicRange());
                assertEquals(23, frame.generation());
                assertEquals(320, frame.width());
                assertEquals(180, frame.height());
                assertTrue(frame.contentPeakNits() >= 1_000f);
                float[] inspection = NativeBridge.inspectWindowsD3D11Frame(
                        adapterLuid, frame.platformHandle());
                assertNotNull(inspection, "A second D3D11 device must import and lock the HDR frame.");
                assertEquals(7, inspection.length);
                assertEquals(10, Math.round(inspection[0]), "DXGI_FORMAT_R16G16B16A16_FLOAT");
                assertEquals(320, Math.round(inspection[1]));
                assertEquals(180, Math.round(inspection[2]));
                assertTrue(Float.isFinite(inspection[3]));
                assertTrue(Float.isFinite(inspection[4]));
                assertTrue(Float.isFinite(inspection[5]));
                assertTrue(Float.isFinite(inspection[6]) && inspection[6] > 0.9f);
            }
        }
    }

    @Test
    void pinnedChromiumHttpsFixturePublishesCpuPullFrame() throws Exception {
        String media = System.getProperty("kmediavlc.test.httpsHdrMedia");
        Assumptions.assumeTrue(media != null, "The immutable Chromium HTTPS fixture is opt-in.");
        var fixture = fixture();
        var signal = new CountDownLatch(1);
        var config = new VlcDesktopPlayerConfig(
                VlcFrameDeliveryMode.CPU_PULL,
                false,
                203f,
                203f,
                new VlcPlayerListener() {
                    @Override
                    public void onFrameAvailable(long serial, long outputGeneration) {
                        signal.countDown();
                    }
                });

        try (var player = VlcDesktopPlayer.create(fixture.runtime(), config)) {
            assertTrue(player.open(media, Map.of(), true));
            assertTrue(
                    signal.await(30, TimeUnit.SECONDS),
                    () -> timeoutDiagnostics(player, "No HTTPS CPU frame arrived."));
            try (var frame = player.acquireLatestFrame().orElseThrow()) {
                assertEquals(VlcNativeHandleType.CPU_ADDRESS, frame.handleType());
                assertEquals(VlcPixelFormat.RGBA8_SRGB, frame.pixelFormat());
                assertTrue(frame.width() > 0);
                assertTrue(frame.height() > 0);
                assertTrue(frame.cpuPixels().orElseThrow().remaining() >= frame.width() * frame.height() * 4);
            }
        }
    }

    private Fixture fixture() throws Exception {
        String bridge = System.getProperty("kmediavlc.test.nativeBridge");
        String libVlc = System.getProperty("kmediavlc.test.libVlc");
        String plugins = System.getProperty("kmediavlc.test.plugins");
        Assumptions.assumeTrue(
                bridge != null && libVlc != null && plugins != null,
                "The exact-commit VideoLAN fixture is opt-in and never a release payload.");
        Path image = createImage();
        String osName = System.getProperty("os.name", "").toLowerCase();
        Set<VlcRenderEngine> renderEngines = osName.contains("mac")
                ? Set.of(VlcRenderEngine.OPENGL)
                : osName.contains("linux")
                        ? Set.of(VlcRenderEngine.GLES2)
                        : Set.of(VlcRenderEngine.D3D11);
        var runtime = new VlcDesktopRuntimeResolution(
                Path.of(bridge).toAbsolutePath(),
                Path.of(libVlc).toAbsolutePath(),
                Path.of(plugins).toAbsolutePath(),
                "videolan-source-build-b5536cde-test-only",
                new VlcRuntimeCapabilities(
                        4,
                        2,
                        "4.0.0-dev",
                        "b5536cdea24b313ba9215eacfbd7fa3295d7f3ee",
                        Set.of(VlcFrameDeliveryMode.GPU_PUSH, VlcFrameDeliveryMode.CPU_PULL),
                        renderEngines,
                        true));
        return new Fixture(runtime, image);
    }

    private Path createImage() throws Exception {
        Path image = temporaryDirectory.resolve("frame.png");
        var pixels = new BufferedImage(64, 36, BufferedImage.TYPE_INT_ARGB);
        var graphics = pixels.createGraphics();
        try {
            graphics.setColor(new Color(255, 32, 8, 255));
            graphics.fillRect(0, 0, 64, 36);
        } finally {
            graphics.dispose();
        }
        assertTrue(ImageIO.write(pixels, "png", image.toFile()));
        return image;
    }

    private static VlcDesktopFrame awaitFrame(
            VlcDesktopPlayer player,
            Semaphore signal,
            long generation,
            int width,
            int height,
            String fallback) throws InterruptedException {
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(20);
        while (true) {
            long remaining = deadline - System.nanoTime();
            if (remaining <= 0 || !signal.tryAcquire(remaining, TimeUnit.NANOSECONDS)) {
                throw new AssertionError(timeoutDiagnostics(player, fallback));
            }
            var candidate = player.acquireLatestFrame();
            if (candidate.isEmpty()) continue;
            var frame = candidate.orElseThrow();
            if (frame.generation() == generation && frame.width() == width && frame.height() == height) {
                return frame;
            }
            frame.close();
        }
    }

    private static String timeoutDiagnostics(VlcDesktopPlayer player, String fallback) {
        return player.lastError().orElse(fallback) + " Snapshot: " + player.snapshot();
    }

    private record Fixture(VlcDesktopRuntimeResolution runtime, Path image) {}
}
