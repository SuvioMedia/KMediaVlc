// SPDX-License-Identifier: LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.desktop;

/** macOS IOSurface producer target and borrowed active Metal device identifiers. */
public record VlcMacOutputTarget(
        long generation,
        int width,
        int height,
        boolean hdr,
        float sdrWhiteNits,
        float peakNits,
        long metalDevice,
        long metalCommandQueue) implements VlcOutputTarget {
    public VlcMacOutputTarget {
        VlcOutputTargets.validate(generation, width, height, sdrWhiteNits, peakNits);
        if (metalDevice == 0 || metalCommandQueue == 0) {
            throw new IllegalArgumentException("A macOS output requires its Metal device and command queue.");
        }
    }
}
