// SPDX-License-Identifier: LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.android;

import java.util.Objects;

/** Path-free identity of the native libraries loaded in the current process. */
public final class VlcAndroidRuntimeReport {
    private final int bridgeAbiVersion;
    private final String nativeAbi;
    private final String vlcVersion;
    private final String vlcChangeset;
    private final String vlcRevision;
    private final String buildMarker;

    VlcAndroidRuntimeReport(
            int bridgeAbiVersion,
            String nativeAbi,
            String vlcVersion,
            String vlcChangeset,
            String vlcRevision,
            String buildMarker) {
        if (bridgeAbiVersion != VlcAndroidRuntime.BRIDGE_ABI_VERSION) {
            throw invalid("The Android bridge ABI differs from the Java API.");
        }
        this.nativeAbi = requireToken(nativeAbi, 32, "native ABI");
        if (!VlcAndroidRuntime.SUPPORTED_ABIS.contains(this.nativeAbi)) {
            throw invalid("The loaded native ABI is unsupported.");
        }
        this.vlcVersion = requireText(vlcVersion, 128, "VLC version");
        if (!this.vlcVersion.startsWith("4.0.0")) {
            throw invalid("The loaded native library is not libVLC 4.0.0 preview.");
        }
        this.vlcChangeset = requireText(vlcChangeset, 256, "VLC changeset");
        // VLC generates this with `git describe --long`; Git controls the abbreviation length.
        String expectedPrefix = VlcAndroidRuntime.VLC_REVISION.substring(0, 7);
        if (!this.vlcChangeset.contains(expectedPrefix)) {
            throw invalid("The loaded libVLC changeset differs from the pinned source revision.");
        }
        this.vlcRevision = requireToken(vlcRevision, 64, "VLC revision");
        if (!VlcAndroidRuntime.VLC_REVISION.equals(this.vlcRevision)) {
            throw invalid("The Android bridge targets another VLC revision.");
        }
        this.buildMarker = requireToken(buildMarker, 64, "build marker");
        if (!"kmediavlc-android-anw-abi1".equals(this.buildMarker)) {
            throw invalid("The Android bridge build marker is unknown.");
        }
        this.bridgeAbiVersion = bridgeAbiVersion;
    }

    public int getBridgeAbiVersion() {
        return bridgeAbiVersion;
    }

    public String getNativeAbi() {
        return nativeAbi;
    }

    public String getVlcVersion() {
        return vlcVersion;
    }

    public String getVlcChangeset() {
        return vlcChangeset;
    }

    public String getVlcRevision() {
        return vlcRevision;
    }

    public String getBuildMarker() {
        return buildMarker;
    }

    private static String requireText(String value, int maximum, String field) {
        Objects.requireNonNull(value, field);
        if (value.isEmpty() || value.length() > maximum || value.indexOf('\0') >= 0) {
            throw invalid(field + " is outside its closed boundary.");
        }
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            if (character < 0x20 || character > 0x7e) {
                throw invalid(field + " contains a non-printable character.");
            }
        }
        return value;
    }

    private static String requireToken(String value, int maximum, String field) {
        value = requireText(value, maximum, field);
        if (!value.matches("[A-Za-z0-9._-]+")) {
            throw invalid(field + " is not a path-free token.");
        }
        return value;
    }

    private static VlcAndroidException invalid(String message) {
        return new VlcAndroidException(VlcAndroidException.Reason.NATIVE_CALL_FAILED, message);
    }
}
