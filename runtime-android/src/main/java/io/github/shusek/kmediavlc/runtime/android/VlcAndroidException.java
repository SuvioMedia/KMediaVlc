// SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

package io.github.shusek.kmediavlc.runtime.android;

/** Failure reported by the closed Android/libVLC boundary. */
public final class VlcAndroidException extends RuntimeException {
    public enum Reason {
        UNSUPPORTED_DEVICE,
        NATIVE_LOAD_FAILED,
        PLAYER_INITIALIZATION_FAILED,
        NATIVE_CALL_FAILED
    }

    private final Reason reason;

    VlcAndroidException(Reason reason, String message) {
        super(message);
        this.reason = reason;
    }

    VlcAndroidException(Reason reason, String message, Throwable cause) {
        super(message, cause);
        this.reason = reason;
    }

    public Reason getReason() {
        return reason;
    }
}
