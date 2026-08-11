// SPDX-License-Identifier: LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.desktop;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import org.junit.jupiter.api.Test;

final class VlcLinuxOutputTargetTest {
    @Test
    void ownsDefensiveCopiesOfNegotiatedFormatModifierPairs() {
        int[] formats = {0x34324241};
        long[] modifiers = {0x0100000000000002L};
        var target = target("/dev/dri/renderD128", formats, modifiers);

        formats[0] = 0;
        modifiers[0] = 0;
        assertArrayEquals(new int[] {0x34324241}, target.drmFormats());
        assertArrayEquals(new long[] {0x0100000000000002L}, target.drmModifiers());

        int[] exportedFormats = target.drmFormats();
        long[] exportedModifiers = target.drmModifiers();
        exportedFormats[0] = 0;
        exportedModifiers[0] = 0;
        assertArrayEquals(new int[] {0x34324241}, target.drmFormats());
        assertArrayEquals(new long[] {0x0100000000000002L}, target.drmModifiers());
    }

    @Test
    void rejectsMissingRenderNodeAndUnpairedModifiers() {
        assertThrows(
                IllegalArgumentException.class,
                () -> target(" ", new int[] {0x34324241}, new long[] {0}));
        assertThrows(
                IllegalArgumentException.class,
                () -> target("/dev/dri/renderD128\0suffix", new int[] {0x34324241}, new long[] {0}));
        assertThrows(
                IllegalArgumentException.class,
                () -> target("/dev/dri/renderD128", new int[0], new long[0]));
        assertThrows(
                IllegalArgumentException.class,
                () -> target("/dev/dri/renderD128", new int[] {0x34324241}, new long[0]));
    }

    private static VlcLinuxOutputTarget target(
            String renderNode,
            int[] formats,
            long[] modifiers) {
        return new VlcLinuxOutputTarget(
                7,
                1920,
                1080,
                false,
                203f,
                203f,
                renderNode,
                formats,
                modifiers,
                true,
                true);
    }
}
