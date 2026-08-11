// SPDX-License-Identifier: LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.desktop;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

final class VlcDesktopFrameOwnershipTest {
    @Test
    void transfersAcquireFenceOwnershipExactlyOnce() {
        long[] values = {
            1,
            2,
            3,
            4,
            16,
            9,
            VlcPixelFormat.RGBA8_SRGB.nativeValue(),
            VlcSourceDynamicRange.SDR.nativeValue(),
            VlcNativeHandleType.D3D11_SHARED_HANDLE.nativeValue(),
            5,
            41,
            0,
            0,
            0,
            0,
            Float.floatToRawIntBits(203.0f),
            Float.floatToRawIntBits(203.0f),
            1,
            0,
        };
        var frame = new VlcDesktopFrame(values, null);

        assertEquals(41, frame.acquireFenceFd());
        assertEquals(VlcDesktopFrame.NO_FENCE, frame.acquireFenceFd());
    }
}
