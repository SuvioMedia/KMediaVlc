// SPDX-License-Identifier: LGPL-2.1-or-later
package io.github.shusek.kmediavlc.runtime.desktop;

import static org.junit.jupiter.api.Assertions.*;

import java.nio.file.Path;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.Test;

final class WindowsVideoScalingIntegrationTest {
    @Test
    void everyScalingOptionProducesAnImportableD3d11Frame() throws Exception {
        Assumptions.assumeTrue(System.getProperty("os.name", "").startsWith("Windows"));
        String media = System.getProperty("kmediavlc.test.pauseMedia");
        Assumptions.assumeTrue(Boolean.getBoolean("kmediavlc.test.bundledRuntime") && media != null);
        var runtime = VlcDesktopRuntime.resolveBundled(Path.of("build/scaling-test-runtime").toAbsolutePath());
        NativeBridge.load(runtime.bridgePath());
        long adapter = NativeBridge.defaultWindowsAdapterLuid();
        Assumptions.assumeTrue(adapter != 0);
        for (int mode = 0; mode <= 4; mode++) {
            var signal = new CountDownLatch(1);
            var config = new VlcDesktopPlayerConfig(VlcFrameDeliveryMode.GPU_PUSH, false, 203, 203,
                    new VlcPlayerListener() {
                        @Override public void onFrameAvailable(long serial, long generation) { signal.countDown(); }
                    });
            try (var player = VlcDesktopPlayer.create(runtime, config)) {
                assertTrue(player.setVideoScalingMode(mode));
                assertTrue(player.updateOutput(new VlcWindowsOutputTarget(1, 1280, 720, false, 203, 203, adapter)));
                assertTrue(player.open(Path.of(media).toUri().toString(), Map.of(), true));
                assertTrue(signal.await(15, TimeUnit.SECONDS), "No frame for scaling mode " + mode);
                try (var frame = player.acquireLatestFrame().orElseThrow()) {
                    assertEquals(1280, frame.width());
                    assertEquals(720, frame.height());
                    assertNotNull(NativeBridge.inspectWindowsD3D11Frame(adapter, frame.platformHandle()));
                }
                assertTrue((player.videoScalingCapabilities() & 15) == 15);
                assertTrue(player.pause());
                assertTrue(player.stop());
            }
        }
        try (var cpu = VlcDesktopPlayer.create(runtime, VlcDesktopPlayerConfig.cpuPull(null))) {
            assertFalse(cpu.setVideoScalingMode(4));
            assertEquals(0, cpu.videoScalingCapabilities());
        }
    }
}
