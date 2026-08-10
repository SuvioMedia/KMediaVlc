// SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

package io.github.shusek.kmediavlc.runtime.android;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.List;
import org.junit.jupiter.api.Test;

final class VlcAndroidRuntimeTest {
    @Test
    void pinsTheClosedAndroidRuntimeIdentity() {
        assertEquals(28, VlcAndroidRuntime.MIN_SDK);
        assertEquals(1, VlcAndroidRuntime.BRIDGE_ABI_VERSION);
        assertEquals("4.0.0-dev", VlcAndroidRuntime.VLC_VERSION);
        assertEquals(
                "b5536cdea24b313ba9215eacfbd7fa3295d7f3ee",
                VlcAndroidRuntime.VLC_REVISION);
        assertEquals(
                "a8d53a9151d7e4a9a5dfd0a5eb1cd92669afdc21",
                VlcAndroidRuntime.LIBVLCJNI_REVISION);
        assertEquals(List.of("arm64-v8a", "armeabi-v7a"), VlcAndroidRuntime.SUPPORTED_ABIS);
        assertThrows(
                UnsupportedOperationException.class,
                () -> VlcAndroidRuntime.SUPPORTED_ABIS.add("x86_64"));
    }

    @Test
    void deviceSupportRequiresTheMinimumSdkAndOnePackagedAbi() {
        assertTrue(VlcAndroidRuntime.isSupported(28, new String[] {"arm64-v8a"}));
        assertTrue(VlcAndroidRuntime.isSupported(37, new String[] {"x86_64", "armeabi-v7a"}));
        assertFalse(VlcAndroidRuntime.isSupported(27, new String[] {"arm64-v8a"}));
        assertFalse(VlcAndroidRuntime.isSupported(37, new String[] {"x86_64"}));
        assertFalse(VlcAndroidRuntime.isSupported(37, null));
    }

    @Test
    void acceptsOnlyTheExactPathFreeNativeIdentity() {
        VlcAndroidRuntimeReport report =
                new VlcAndroidRuntimeReport(
                        1,
                        "arm64-v8a",
                        "4.0.0-dev Vetinari",
                        "b5536cdea24b313ba9215eacfbd7fa3295d7f3ee",
                        VlcAndroidRuntime.VLC_REVISION,
                        "kmediavlc-android-anw-abi1");
        assertEquals(1, report.getBridgeAbiVersion());
        assertEquals("arm64-v8a", report.getNativeAbi());
        assertEquals(VlcAndroidRuntime.VLC_REVISION, report.getVlcRevision());
        assertThrows(
                VlcAndroidException.class,
                () ->
                        new VlcAndroidRuntimeReport(
                                1,
                                "x86_64",
                                "4.0.0-dev",
                                VlcAndroidRuntime.VLC_REVISION,
                                VlcAndroidRuntime.VLC_REVISION,
                                "kmediavlc-android-anw-abi1"));
        assertThrows(
                VlcAndroidException.class,
                () ->
                        new VlcAndroidRuntimeReport(
                                1,
                                "arm64-v8a",
                                "3.0.21",
                                VlcAndroidRuntime.VLC_REVISION,
                                VlcAndroidRuntime.VLC_REVISION,
                                "kmediavlc-android-anw-abi1"));
    }
}
