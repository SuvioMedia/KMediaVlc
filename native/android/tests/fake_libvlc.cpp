// SPDX-License-Identifier: LGPL-2.1-or-later

#include <jni.h>

#include <vlc/vlc.h>

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <new>

struct libvlc_instance_t final {};
struct libvlc_media_t final {};
struct libvlc_media_tracklist_t final {};

struct libvlc_media_player_t final {
    libvlc_media_player_cbs callbacks{};
    void* callback_opaque = nullptr;
    libvlc_state_t state = libvlc_NothingSpecial;
    libvlc_time_t time = 0;
    libvlc_time_t length = 60'000'000;
    bool playing = false;

    libvlc_video_engine_t engine = libvlc_video_engine_disable;
    libvlc_video_output_setup_cb setup = nullptr;
    libvlc_video_output_cleanup_cb cleanup = nullptr;
    libvlc_video_update_output_cb update = nullptr;
    void* output_opaque = nullptr;
    void* active_opaque = nullptr;
};

namespace {

void cleanup_output(libvlc_media_player_t* player) {
    if (player->active_opaque != nullptr && player->cleanup != nullptr) {
        player->cleanup(player->active_opaque);
    }
    player->active_opaque = nullptr;
}

void start_output(libvlc_media_player_t* player) {
    cleanup_output(player);
    if (player->engine != libvlc_video_engine_anw || player->setup == nullptr ||
        player->update == nullptr) {
        return;
    }
    void* opaque = player->output_opaque;
    const libvlc_video_setup_device_cfg_t setup_configuration{true};
    libvlc_video_setup_device_info_t setup_output{};
    if (!player->setup(&opaque, &setup_configuration, &setup_output)) return;
    const libvlc_video_render_cfg_t render_configuration{
        1920,
        1080,
        8,
        false,
        libvlc_video_colorspace_BT709,
        libvlc_video_primaries_BT709,
        libvlc_video_transfer_func_SRGB,
        nullptr,
    };
    libvlc_video_output_cfg_t render_output{};
    if (!player->update(opaque, &render_configuration, &render_output) ||
        render_output.u.anw.video == nullptr) {
        if (player->cleanup != nullptr) player->cleanup(opaque);
        return;
    }
    player->active_opaque = opaque;
}

void state_changed(libvlc_media_player_t* player, libvlc_state_t state) {
    player->state = state;
    if (player->callbacks.on_state_changed != nullptr) {
        player->callbacks.on_state_changed(player->callback_opaque, state);
    }
}

}  // namespace

extern "C" JNIEXPORT jint JNICALL JNI_OnLoad(JavaVM*, void*) {
    return JNI_VERSION_1_2;
}

extern "C" libvlc_instance_t* libvlc_new(int, const char* const*) {
    return new (std::nothrow) libvlc_instance_t();
}

extern "C" void libvlc_release(libvlc_instance_t* instance) {
    delete instance;
}

extern "C" const char* libvlc_get_version(void) {
    return "4.0.0-dev Vetinari";
}

extern "C" const char* libvlc_get_changeset(void) {
    return "e439692079a75cacb5f07310d1ec2dc20bfd1fe0";
}

extern "C" libvlc_media_player_t* libvlc_media_player_new(
    libvlc_instance_t*,
    const libvlc_media_player_cbs* callbacks,
    void* opaque) {
    auto* player = new (std::nothrow) libvlc_media_player_t();
    if (player == nullptr) return nullptr;
    if (callbacks != nullptr) player->callbacks = *callbacks;
    player->callback_opaque = opaque;
    return player;
}

extern "C" void libvlc_media_player_release(libvlc_media_player_t* player) {
    if (player == nullptr) return;
    cleanup_output(player);
    delete player;
}

extern "C" bool libvlc_video_set_output_callbacks(
    libvlc_media_player_t* player,
    libvlc_video_engine_t engine,
    libvlc_video_output_setup_cb setup,
    libvlc_video_output_cleanup_cb cleanup,
    libvlc_video_output_set_window_cb,
    libvlc_video_update_output_cb update,
    libvlc_video_swap_cb,
    libvlc_video_makeCurrent_cb,
    libvlc_video_getProcAddress_cb,
    libvlc_video_frameMetadata_cb,
    libvlc_video_output_select_plane_cb,
    void* opaque) {
    if (player == nullptr) return false;
    cleanup_output(player);
    if (engine != libvlc_video_engine_disable && engine != libvlc_video_engine_anw) return false;
    player->engine = engine;
    player->setup = setup;
    player->cleanup = cleanup;
    player->update = update;
    player->output_opaque = opaque;
    if (player->playing && engine == libvlc_video_engine_anw) start_output(player);
    return true;
}

extern "C" libvlc_media_t* libvlc_media_new_location(const char*) {
    return new (std::nothrow) libvlc_media_t();
}

extern "C" libvlc_media_t* libvlc_media_new_path(const char*) {
    return new (std::nothrow) libvlc_media_t();
}

extern "C" void libvlc_media_add_option(libvlc_media_t*, const char*) {}

extern "C" void libvlc_media_release(libvlc_media_t* media) {
    delete media;
}

extern "C" void libvlc_media_player_set_media(libvlc_media_player_t*, libvlc_media_t*) {}

extern "C" libvlc_media_tracklist_t* libvlc_media_player_get_tracklist(
    libvlc_media_player_t* player,
    libvlc_track_type_t,
    bool) {
    return player == nullptr ? nullptr : new (std::nothrow) libvlc_media_tracklist_t();
}

extern "C" std::size_t libvlc_media_tracklist_count(const libvlc_media_tracklist_t*) {
    return 0;
}

extern "C" libvlc_media_track_t* libvlc_media_tracklist_at(
    libvlc_media_tracklist_t*,
    std::size_t) {
    return nullptr;
}

extern "C" void libvlc_media_tracklist_delete(libvlc_media_tracklist_t* tracks) {
    delete tracks;
}

extern "C" void libvlc_media_player_select_tracks_by_ids(
    libvlc_media_player_t*,
    libvlc_track_type_t,
    const char*) {}

extern "C" int libvlc_media_player_play(libvlc_media_player_t* player) {
    if (player == nullptr) return -1;
    state_changed(player, libvlc_Opening);
    if (player->callbacks.on_buffering_changed != nullptr) {
        player->callbacks.on_buffering_changed(player->callback_opaque, 0.0F);
        player->callbacks.on_buffering_changed(player->callback_opaque, 1.0F);
    }
    player->playing = true;
    start_output(player);
    state_changed(player, libvlc_Playing);
    if (player->callbacks.on_position_changed != nullptr) {
        player->callbacks.on_position_changed(player->callback_opaque, player->time, 0.0);
    }
    if (player->callbacks.on_length_changed != nullptr) {
        player->callbacks.on_length_changed(player->callback_opaque, player->length);
    }
    return 0;
}

extern "C" void libvlc_media_player_set_pause(libvlc_media_player_t* player, int pause) {
    if (player != nullptr) state_changed(player, pause == 0 ? libvlc_Playing : libvlc_Paused);
}

extern "C" int libvlc_media_player_stop_async(libvlc_media_player_t* player) {
    if (player == nullptr) return -1;
    player->playing = false;
    cleanup_output(player);
    if (player->callbacks.on_media_stopping != nullptr) {
        player->callbacks.on_media_stopping(
            player->callback_opaque,
            nullptr,
            libvlc_stopping_reason_user);
    }
    state_changed(player, libvlc_Stopped);
    return 0;
}

extern "C" int libvlc_media_player_set_time(
    libvlc_media_player_t* player,
    libvlc_time_t time,
    bool) {
    if (player == nullptr || time < 0) return -1;
    player->time = time;
    return 0;
}

extern "C" libvlc_time_t libvlc_media_player_get_time(libvlc_media_player_t* player) {
    return player == nullptr ? -1 : player->time;
}

extern "C" libvlc_time_t libvlc_media_player_get_length(libvlc_media_player_t* player) {
    return player == nullptr ? -1 : player->length;
}

extern "C" bool libvlc_media_player_is_seekable(libvlc_media_player_t* player) {
    return player != nullptr;
}

extern "C" int libvlc_media_player_set_rate(libvlc_media_player_t* player, float rate) {
    return player != nullptr && rate > 0.0F ? 0 : -1;
}

extern "C" int libvlc_audio_set_volume(libvlc_media_player_t* player, int volume) {
    return player != nullptr && volume >= 0 && volume <= 100 ? 0 : -1;
}
