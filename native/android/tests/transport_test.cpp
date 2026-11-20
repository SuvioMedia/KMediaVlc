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

    // Delays survive a new input; tracks are listed and switched by id.
    const auto delays = Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_create(nullptr, nullptr, 0);
    assert(delays != 0);
    auto* delayed = player_from(delays);
    assert(Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_setAudioDelay(
        nullptr, nullptr, delays, 200'000));
    assert(Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_setSubtitleDelay(
        nullptr, nullptr, delays, -1'500'000));
    assert(!Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_setSubtitleDelay(
        nullptr, nullptr, delays, 600'000'001));
    const auto generation_before = delayed->tracks_generation.load();
    delayed->current_media = libvlc_media_new_path("fixture");
    libvlc_media_player_set_media(delayed->media_player, delayed->current_media);
    assert(Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_play(nullptr, nullptr, delays));
    assert(libvlc_audio_get_delay(delayed->media_player) == 200'000);
    assert(libvlc_video_get_spu_delay(delayed->media_player) == -1'500'000);
    assert(!has_track(delayed->media_player, libvlc_track_text, "spu/9"));
    assert(has_track(delayed->media_player, libvlc_track_text, "spu/1"));
    libvlc_media_player_select_tracks_by_ids(delayed->media_player, libvlc_track_text, "spu/1");
    assert(delayed->tracks_generation.load() != generation_before);
    assert(capture_selected_tracks(delayed->media_player, libvlc_track_text).ids == "spu/1");
    // Surface replacement creates a new input; delays and the choice carry over.
    assert(recreate_media_player(delayed));
    assert(libvlc_audio_get_delay(delayed->media_player) == 200'000);
    assert(libvlc_video_get_spu_delay(delayed->media_player) == -1'500'000);
    assert(capture_selected_tracks(delayed->media_player, libvlc_track_text).ids == "spu/1");
    // Subtitles off survives stop and replay, which give libVLC a fresh input with default tracks.
    assert(Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_selectTrack(
        nullptr, nullptr, delays, kTrackSubtitle, nullptr));
    assert(capture_selected_tracks(delayed->media_player, libvlc_track_text).ids.empty());
    assert(Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_stop(nullptr, nullptr, delays));
    assert(Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_play(nullptr, nullptr, delays));
    assert(capture_selected_tracks(delayed->media_player, libvlc_track_text).ids == "spu/0");
    apply_delays(delayed);         // Same steps as snapshot polling.
    apply_track_choices(delayed);
    assert(capture_selected_tracks(delayed->media_player, libvlc_track_text).ids.empty());
    assert(libvlc_video_get_spu_delay(delayed->media_player) == -1'500'000);
    Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_destroy(nullptr, nullptr, delays);

    std::puts("PASS: paused Surface replacement, Play/Stop cancellation, deferred Stop, unpausable source, delays, tracks");
}
