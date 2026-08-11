// SPDX-License-Identifier: LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.android;

import android.view.Surface;

final class NativeBridge {
    private NativeBridge() {}

    static native long create(int decodeMode);

    static native void destroy(long handle);

    static native boolean setSurfaces(
            long handle, Surface video, Surface subtitles, int width, int height);

    static native boolean open(long handle, byte[] locationUtf8, byte[][] headerPairsUtf8, boolean autoplay);

    static native boolean play(long handle);

    static native boolean pause(long handle);

    static native boolean stop(long handle);

    static native boolean seek(long handle, long timeMicroseconds, boolean fast);

    static native boolean setVolume(long handle, float volume);

    static native boolean setRate(long handle, float rate);

    static native boolean setLoop(long handle, boolean loop);

    static native long[] snapshot(long handle);

    static native byte[] lastErrorUtf8(long handle);

    static native int bridgeAbiVersion();

    static native byte[] nativeAbiUtf8();

    static native byte[] vlcVersionUtf8();

    static native byte[] vlcChangesetUtf8();

    static native byte[] vlcRevisionUtf8();

    static native byte[] buildMarkerUtf8();
}
