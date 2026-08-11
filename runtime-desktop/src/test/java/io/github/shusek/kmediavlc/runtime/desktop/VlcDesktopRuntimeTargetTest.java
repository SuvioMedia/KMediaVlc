// SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

package io.github.shusek.kmediavlc.runtime.desktop;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import org.junit.jupiter.api.Test;

final class VlcDesktopRuntimeTargetTest {
    @Test
    void resolvesAuditedWindowsAndAppleSiliconTargets() {
        assertEquals("windows-x86_64", VlcDesktopRuntime.targetFor("Windows 11", "amd64"));
        assertEquals("windows-aarch64", VlcDesktopRuntime.targetFor("Windows 11", "arm64"));
        assertEquals("macos-aarch64", VlcDesktopRuntime.targetFor("Mac OS X", "aarch64"));
    }

    @Test
    void rejectsTargetsWithoutAnAuditedGpuPayload() {
        assertThrows(
                VlcRuntimeException.class,
                () -> VlcDesktopRuntime.targetFor("Mac OS X", "x86_64"));
        assertThrows(
                VlcRuntimeException.class,
                () -> VlcDesktopRuntime.targetFor("Linux", "aarch64"));
        assertThrows(
                VlcRuntimeException.class,
                () -> VlcDesktopRuntime.targetFor("Windows 11", "riscv64"));
    }
}
