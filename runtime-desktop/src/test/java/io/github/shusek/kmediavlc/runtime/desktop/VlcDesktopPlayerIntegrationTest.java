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
import java.util.concurrent.TimeUnit;
import javax.imageio.ImageIO;
import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

final class VlcDesktopPlayerIntegrationTest {
    @TempDir Path temporaryDirectory;

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

    private Fixture fixture() throws Exception {
        String bridge = System.getProperty("kmediavlc.test.nativeBridge");
        String libVlc = System.getProperty("kmediavlc.test.libVlc");
        String plugins = System.getProperty("kmediavlc.test.plugins");
        Assumptions.assumeTrue(
                bridge != null && libVlc != null && plugins != null,
                "The exact-commit VideoLAN fixture is opt-in and never a release payload.");
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

    private static String timeoutDiagnostics(VlcDesktopPlayer player, String fallback) {
        return player.lastError().orElse(fallback) + " Snapshot: " + player.snapshot();
    }

    private record Fixture(VlcDesktopRuntimeResolution runtime, Path image) {}
}
