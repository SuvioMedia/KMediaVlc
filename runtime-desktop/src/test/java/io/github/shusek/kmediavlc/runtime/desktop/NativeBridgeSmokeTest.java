// SPDX-License-Identifier: LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.desktop;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;

import java.nio.file.Path;
import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.Test;

final class NativeBridgeSmokeTest {
    @Test
    void loadsBridgeBuiltAgainstPinnedLibVlcHeaders() {
        String bridge = System.getProperty("kmediavlc.test.nativeBridge");
        Assumptions.assumeTrue(bridge != null && !bridge.isBlank());
        assertDoesNotThrow(() -> NativeBridge.load(Path.of(bridge)));
    }
}
