// SPDX-License-Identifier: LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.desktop;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import org.junit.jupiter.api.Test;

final class VlcDesktopRuntimeTargetTest {
    @Test
    void resolvesDesktopRuntimeTargets() {
        assertEquals("windows-x86_64", VlcDesktopRuntime.targetFor("Windows 11", "amd64"));
        assertEquals("windows-aarch64", VlcDesktopRuntime.targetFor("Windows 11", "arm64"));
        assertEquals("macos-aarch64", VlcDesktopRuntime.targetFor("Mac OS X", "aarch64"));
        assertEquals("linux-x86_64", VlcDesktopRuntime.targetFor("Linux", "amd64"));
        assertEquals("linux-aarch64", VlcDesktopRuntime.targetFor("Linux", "aarch64"));
    }

    @Test
    void rejectsTargetsWithoutAnAuditedGpuPayload() {
        assertThrows(
                VlcRuntimeException.class,
                () -> VlcDesktopRuntime.targetFor("Mac OS X", "x86_64"));
        assertThrows(
                VlcRuntimeException.class,
                () -> VlcDesktopRuntime.targetFor("Windows 11", "riscv64"));
    }
}
