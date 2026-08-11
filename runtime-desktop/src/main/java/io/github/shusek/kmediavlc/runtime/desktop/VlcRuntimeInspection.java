// SPDX-License-Identifier: LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.desktop;

import java.util.Objects;
import java.util.Optional;

/** Read-only bundled-runtime probe that never loads native code. */
public record VlcRuntimeInspection(
        boolean available,
        String target,
        Optional<VlcRuntimeCapabilities> capabilities,
        Optional<VlcRuntimeException.Reason> unavailableReason) {

    public VlcRuntimeInspection {
        Objects.requireNonNull(target, "target");
        capabilities = Objects.requireNonNull(capabilities, "capabilities");
        unavailableReason = Objects.requireNonNull(unavailableReason, "unavailableReason");
        if (available != capabilities.isPresent() || available == unavailableReason.isPresent()) {
            throw new IllegalArgumentException("Inspection availability fields are inconsistent.");
        }
    }
}
