// SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

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
