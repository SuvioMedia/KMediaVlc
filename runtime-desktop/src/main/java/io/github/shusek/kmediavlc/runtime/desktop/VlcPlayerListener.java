// SPDX-License-Identifier: LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.desktop;

/** Non-owning native notifications. Implementations must return immediately. */
public interface VlcPlayerListener {
    default void onFrameAvailable(long serial, long outputGeneration) {}

    default void onPlaybackStateChanged(VlcPlaybackState state, long mediaGeneration) {}
}
