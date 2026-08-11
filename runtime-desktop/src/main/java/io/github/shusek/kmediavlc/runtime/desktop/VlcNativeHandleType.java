// SPDX-License-Identifier: LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.desktop;

/** Native ownership mechanism carried by a decoded frame. */
public enum VlcNativeHandleType {
    CPU_ADDRESS(1),
    D3D11_SHARED_HANDLE(2),
    IOSURFACE(3),
    DMABUF(4);

    private final int nativeValue;

    VlcNativeHandleType(int nativeValue) {
        this.nativeValue = nativeValue;
    }

    public int nativeValue() {
        return nativeValue;
    }

    static VlcNativeHandleType fromNative(int value) {
        for (VlcNativeHandleType candidate : values()) {
            if (candidate.nativeValue == value) return candidate;
        }
        throw new VlcRuntimeException(
                VlcRuntimeException.Reason.NATIVE_CALL_FAILED,
                "The VLC bridge returned an unknown frame handle type.");
    }
}
