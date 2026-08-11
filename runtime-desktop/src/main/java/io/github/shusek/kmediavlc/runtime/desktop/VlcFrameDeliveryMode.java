// SPDX-License-Identifier: LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.desktop;

/** Supported ownership modes exposed by the stable KMediaVlc bridge. */
public enum VlcFrameDeliveryMode {
    /** The producer emits a non-owning notification and the consumer pulls the latest GPU frame. */
    GPU_PUSH(1),

    /** The consumer explicitly pulls the latest controlled SDR CPU frame. */
    CPU_PULL(2);

    private final int nativeValue;

    VlcFrameDeliveryMode(int nativeValue) {
        this.nativeValue = nativeValue;
    }

    int nativeValue() {
        return nativeValue;
    }
}
