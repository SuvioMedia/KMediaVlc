// SPDX-License-Identifier: LGPL-2.1-or-later
// Exercise the real bridge against a deterministic asynchronous libVLC fixture.
#include "../kmediavlc_android_jni.cpp"
#include <cassert>
#include <cstdio>

extern "C" void kmediavlc_fake_pause_ready(bool);
extern "C" void kmediavlc_fake_deferred_stop(bool);

int main() {
    const auto handle = Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_create(nullptr, nullptr, 0);
    assert(handle != 0);
    auto* player = player_from(handle);
    player->current_media = libvlc_media_new_path("fixture");
    libvlc_media_player_set_media(player->media_player, player->current_media);
    assert(Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_play(nullptr, nullptr, handle));

    assert(Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_pause(nullptr, nullptr, handle));
    assert(!player->pause_pending && player->state == kStatePaused);

    // Replacing a paused Surface starts a fresh input with capabilities pending.
    assert(libvlc_media_player_set_time(player->media_player, 22'000'000, false) == 0);
    kmediavlc_fake_pause_ready(false);
    assert(recreate_media_player(player));
    assert(player->pause_pending && player->state == kStatePlaying);
    assert(libvlc_media_player_get_time(player->media_player) == 22'000'000);
    kmediavlc_fake_pause_ready(true);
    apply_pending_pause(player); // Same step as snapshot polling.
    assert(!player->pause_pending && player->state == kStatePaused);

    // New explicit Play must cancel the older pending Pause.
    kmediavlc_fake_pause_ready(false);
    assert(recreate_media_player(player));
    assert(player->pause_pending);
    assert(Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_play(nullptr, nullptr, handle));
    kmediavlc_fake_pause_ready(true);
    apply_pending_pause(player);
    assert(!player->pause_pending && player->state == kStatePlaying);

    // Accepted Stop wins even while libVLC still reports the old PLAYING state.
    assert(Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_pause(nullptr, nullptr, handle));
    kmediavlc_fake_pause_ready(false);
    assert(recreate_media_player(player));
    assert(player->pause_pending);
    kmediavlc_fake_deferred_stop(true);
    assert(Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_stop(nullptr, nullptr, handle));
    assert(player->state == kStatePlaying);
    assert(recreate_media_player(player));
    assert(player->state == kStateIdle && !player->pause_pending);
    kmediavlc_fake_deferred_stop(false);

    // A genuinely unpausable source retains libVLC's existing explicit-Pause behavior.
    assert(Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_play(nullptr, nullptr, handle));
    assert(Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_pause(nullptr, nullptr, handle));
    assert(player->state == kStateStopped);

    Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_destroy(nullptr, nullptr, handle);

    std::puts("PASS: paused Surface replacement, Play/Stop cancellation, deferred Stop, unpausable source");
}
