// SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

package io.github.shusek.kmediavlc.runtime.desktop;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.awt.Color;
import java.awt.image.BufferedImage;
import java.nio.file.Path;
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
        Path plugins = temporaryDirectory.resolve("fake-vlc-plugins").toAbsolutePath();
        java.nio.file.Files.createDirectories(plugins);
        var runtime = new VlcDesktopRuntimeResolution(
                Path.of(bridge).toAbsolutePath(),
                Path.of(fakeLibVlc).toAbsolutePath(),
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
                        Set.of(VlcRenderEngine.D3D11),
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

    private static String timeoutDiagnostics(VlcDesktopPlayer player, String fallback) {
        return player.lastError().orElse(fallback) + " Snapshot: " + player.snapshot();
    }

    private record Fixture(VlcDesktopRuntimeResolution runtime, Path image) {}
}
