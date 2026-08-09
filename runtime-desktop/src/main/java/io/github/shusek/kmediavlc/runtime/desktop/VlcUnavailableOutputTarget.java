// SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

package io.github.shusek.kmediavlc.runtime.desktop;

/** Suspends GPU frame publication while no compatible TextureView host exists. */
public record VlcUnavailableOutputTarget(long generation) implements VlcOutputTarget {
    @Override public int width() { return 0; }
    @Override public int height() { return 0; }
    @Override public boolean hdr() { return false; }
    @Override public float sdrWhiteNits() { return 203f; }
    @Override public float peakNits() { return 203f; }
}
