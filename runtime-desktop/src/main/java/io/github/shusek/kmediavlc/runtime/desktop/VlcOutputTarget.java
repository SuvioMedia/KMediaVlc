// SPDX-License-Identifier: LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.desktop;

/** Exact Nucleus host generation/device selected for producer allocation. */
public sealed interface VlcOutputTarget
        permits VlcUnavailableOutputTarget, VlcWindowsOutputTarget, VlcMacOutputTarget, VlcLinuxOutputTarget {
    long generation();
    int width();
    int height();
    boolean hdr();
    float sdrWhiteNits();
    float peakNits();
}
