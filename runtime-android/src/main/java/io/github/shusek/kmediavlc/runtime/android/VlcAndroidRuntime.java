// SPDX-License-Identifier: LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.android;

import android.os.Build;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;

/** Immutable Android ABI, load-order, and native identity contract. */
public final class VlcAndroidRuntime {
    public static final int MIN_SDK = 28;
    public static final int BRIDGE_ABI_VERSION = 1;
    public static final String VLC_VERSION = "4.0.0-dev";
    public static final String VLC_REVISION = "b5536cdea24b313ba9215eacfbd7fa3295d7f3ee";
    public static final String LIBVLCJNI_REVISION =
            "a8d53a9151d7e4a9a5dfd0a5eb1cd92669afdc21";
    public static final String LEGAL_ASSET_DIRECTORY = "kmediavlc/legal";
    public static final List<String> SUPPORTED_ABIS =
            Collections.unmodifiableList(Arrays.asList("arm64-v8a", "armeabi-v7a"));

    private enum LoadState {
        NOT_LOADED,
        LOADED,
        FAILED
    }

    private static LoadState loadState = LoadState.NOT_LOADED;

    private VlcAndroidRuntime() {}

    public static boolean isSupportedDevice() {
        return isSupported(Build.VERSION.SDK_INT, Build.SUPPORTED_ABIS);
    }

    static boolean isSupported(int sdk, String[] abis) {
        if (sdk < MIN_SDK || abis == null) return false;
        for (String abi : abis) {
            if (SUPPORTED_ABIS.contains(abi)) return true;
        }
        return false;
    }

    static void requireSupportedDevice() {
        if (!isSupportedDevice()) {
            throw new VlcAndroidException(
                    VlcAndroidException.Reason.UNSUPPORTED_DEVICE,
                    "KMediaVlc requires Android API 28+ and one of " + SUPPORTED_ABIS + '.');
        }
    }

    /**
     * Loads libVLC first so Android invokes its own JNI_OnLoad before the client bridge links to
     * it. This replaces the much larger org.videolan.libvlc Java/JNI wrapper.
     */
    static synchronized void ensureLoaded() {
        if (loadState == LoadState.LOADED) return;
        if (loadState == LoadState.FAILED) {
            throw new VlcAndroidException(
                    VlcAndroidException.Reason.NATIVE_LOAD_FAILED,
                    "The bundled Android libVLC runtime previously failed to load.");
        }
        try {
            System.loadLibrary("vlc");
            System.loadLibrary("kmediavlc_android");
            loadState = LoadState.LOADED;
        } catch (LinkageError | SecurityException failure) {
            loadState = LoadState.FAILED;
            throw new VlcAndroidException(
                    VlcAndroidException.Reason.NATIVE_LOAD_FAILED,
                    "The bundled Android libVLC runtime could not be loaded.",
                    failure);
        }
    }

    /** Returns a path-free report from the two native libraries loaded in this process. */
    public static VlcAndroidRuntimeReport inspectNativeRuntime() {
        requireSupportedDevice();
        ensureLoaded();
        return new VlcAndroidRuntimeReport(
                NativeBridge.bridgeAbiVersion(),
                decodeAscii(NativeBridge.nativeAbiUtf8(), 32, "native ABI"),
                decodeAscii(NativeBridge.vlcVersionUtf8(), 128, "VLC version"),
                decodeAscii(NativeBridge.vlcChangesetUtf8(), 256, "VLC changeset"),
                decodeAscii(NativeBridge.vlcRevisionUtf8(), 64, "VLC revision"),
                decodeAscii(NativeBridge.buildMarkerUtf8(), 64, "build marker"));
    }

    static String decodeUtf8(byte[] value, int maximum, String field) {
        if (value == null || value.length == 0 || value.length > maximum) {
            throw new VlcAndroidException(
                    VlcAndroidException.Reason.NATIVE_CALL_FAILED,
                    field + " is outside its native boundary.");
        }
        String decoded = new String(value, StandardCharsets.UTF_8);
        if (decoded.indexOf('\0') >= 0) {
            throw new VlcAndroidException(
                    VlcAndroidException.Reason.NATIVE_CALL_FAILED,
                    field + " contains NUL.");
        }
        return decoded;
    }

    private static String decodeAscii(byte[] value, int maximum, String field) {
        String decoded = decodeUtf8(value, maximum, field);
        if (!StandardCharsets.US_ASCII.newEncoder().canEncode(decoded)) {
            throw new VlcAndroidException(
                    VlcAndroidException.Reason.NATIVE_CALL_FAILED,
                    field + " is not bounded ASCII.");
        }
        return decoded;
    }
}
