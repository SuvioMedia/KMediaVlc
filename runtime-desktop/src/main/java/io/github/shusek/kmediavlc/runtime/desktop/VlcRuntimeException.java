// SPDX-License-Identifier: LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.desktop;

/** Failure raised before an unverified native VLC binary is loaded. */
public final class VlcRuntimeException extends RuntimeException {
    public enum Reason {
        UNSUPPORTED_PLATFORM,
        PAYLOAD_MISSING,
        MANIFEST_REJECTED,
        EXTRACTION_FAILED,
        INTEGRITY_FAILURE,
        BRIDGE_LOAD_FAILED,
        INCOMPATIBLE_BRIDGE,
        PLAYER_INITIALIZATION_FAILED,
        NATIVE_CALL_FAILED
    }

    private final Reason reason;

    public VlcRuntimeException(Reason reason, String message) {
        super(message);
        this.reason = reason;
    }

    public VlcRuntimeException(Reason reason, String message, Throwable cause) {
        super(message, cause);
        this.reason = reason;
    }

    public Reason reason() {
        return reason;
    }
}
