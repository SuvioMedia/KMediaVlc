// SPDX-License-Identifier: LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.desktop;

final class VlcOutputTargets {
    private VlcOutputTargets() {}

    static void validate(long generation, int width, int height, float white, float peak) {
        if (generation < 0 || width <= 0 || height <= 0) {
            throw new IllegalArgumentException("Output generation and dimensions are invalid.");
        }
        if (!Float.isFinite(white) || white <= 0f || !Float.isFinite(peak) || peak < white) {
            throw new IllegalArgumentException("Output luminance values are invalid.");
        }
    }
}
