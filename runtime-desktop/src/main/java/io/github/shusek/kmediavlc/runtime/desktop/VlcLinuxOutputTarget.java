// SPDX-License-Identifier: LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.desktop;

import java.util.Objects;

/** Linux GBM producer target selected from current Wayland/EGL import capabilities. */
public record VlcLinuxOutputTarget(
        long generation,
        int width,
        int height,
        boolean hdr,
        float sdrWhiteNits,
        float peakNits,
        String renderNode,
        int[] drmFormats,
        long[] drmModifiers,
        boolean acquireFences,
        boolean releaseFences) implements VlcOutputTarget {
    public VlcLinuxOutputTarget {
        VlcOutputTargets.validate(generation, width, height, sdrWhiteNits, peakNits);
        if (renderNode == null || renderNode.isBlank() || renderNode.indexOf('\0') >= 0) {
            throw new IllegalArgumentException("A Linux output requires a valid render node.");
        }
        Objects.requireNonNull(drmFormats, "drmFormats");
        Objects.requireNonNull(drmModifiers, "drmModifiers");
        if (drmFormats.length == 0 || drmFormats.length != drmModifiers.length) {
            throw new IllegalArgumentException("Each Linux DRM format requires one modifier entry.");
        }
        drmFormats = drmFormats.clone();
        drmModifiers = drmModifiers.clone();
    }

    @Override public int[] drmFormats() { return drmFormats.clone(); }
    @Override public long[] drmModifiers() { return drmModifiers.clone(); }
}
