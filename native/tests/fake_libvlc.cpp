// SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

#include <vlc/vlc.h>

#include <cstdint>
#include <cstring>
#include <new>
#include <string_view>

struct libvlc_instance_t final {};
struct libvlc_media_t final {
    bool hdr = false;
    bool eos_sequence = false;
};

struct FakeOutputCallbacks final {
    libvlc_video_engine_t engine = libvlc_video_engine_disable;
    libvlc_video_output_setup_cb setup = nullptr;
    libvlc_video_output_cleanup_cb cleanup = nullptr;
    libvlc_video_output_set_window_cb window = nullptr;
    libvlc_video_update_output_cb update_output = nullptr;
    libvlc_video_swap_cb swap = nullptr;
    libvlc_video_makeCurrent_cb make_current = nullptr;
    libvlc_video_getProcAddress_cb get_proc_address = nullptr;
    void* opaque = nullptr;
    bool setup_active = false;
};

struct FakeVmemCallbacks final {
    libvlc_video_lock_cb lock = nullptr;
    libvlc_video_unlock_cb unlock = nullptr;
    libvlc_video_display_cb display = nullptr;
    libvlc_video_format_cb format = nullptr;
    libvlc_video_cleanup_cb cleanup = nullptr;
    void* opaque = nullptr;
    bool setup_active = false;
};

struct libvlc_media_player_t final {
    const libvlc_media_player_cbs* callbacks = nullptr;
    void* callbacks_opaque = nullptr;
    FakeOutputCallbacks output;
    FakeVmemCallbacks vmem;
    bool hdr = false;
    bool eos_sequence = false;
};

namespace {

void cleanup_output(libvlc_media_player_t* player) {
    if (player == nullptr || !player->output.setup_active) return;
    if (player->output.cleanup != nullptr) player->output.cleanup(player->output.opaque);
    player->output.setup_active = false;
}

void cleanup_vmem(libvlc_media_player_t* player) {
    if (player == nullptr || !player->vmem.setup_active) return;
    if (player->vmem.cleanup != nullptr) player->vmem.cleanup(player->vmem.opaque);
    player->vmem.setup_active = false;
}

bool publish_cpu_test_frame(libvlc_media_player_t* player) {
    if (player == nullptr || player->vmem.lock == nullptr || player->vmem.display == nullptr ||
        player->vmem.format == nullptr) {
        return false;
    }
    cleanup_vmem(player);
    char chroma[5] = {'I', '4', '2', '0', '\0'};
    unsigned widths[2] = {128U, 128U};
    unsigned heights[2] = {86U, 72U};
    unsigned pitches[4]{};
    unsigned lines[4]{};
    void* opaque = player->vmem.opaque;
    if (player->vmem.format(&opaque, chroma, widths, heights, pitches, lines) == 0U) {
        return false;
    }
    player->vmem.opaque = opaque;
    player->vmem.setup_active = true;
    if (std::memcmp(chroma, "RGBA", 4U) != 0 || widths[0] != widths[1] ||
        heights[0] != heights[1] || widths[0] != 128U || heights[0] != 72U ||
        pitches[0] < widths[0] * 4U || lines[0] < heights[0]) {
        cleanup_vmem(player);
        return false;
    }
    void* planes[4]{};
    void* picture = player->vmem.lock(opaque, planes);
    if (picture == nullptr || planes[0] == nullptr) {
        cleanup_vmem(player);
        return false;
    }
    auto* pixels = static_cast<std::uint8_t*>(planes[0]);
    for (unsigned y = 0; y < heights[0]; ++y) {
        for (unsigned x = 0; x < widths[0]; ++x) {
            const std::size_t offset = static_cast<std::size_t>(y) * pitches[0] + x * 4U;
            pixels[offset] = static_cast<std::uint8_t>(x);
            pixels[offset + 1U] = static_cast<std::uint8_t>(y);
            pixels[offset + 2U] = UINT8_C(64);
            pixels[offset + 3U] = UINT8_MAX;
        }
    }
    if (player->vmem.unlock != nullptr) player->vmem.unlock(opaque, picture, planes);
    player->vmem.display(opaque, picture);
    return true;
}

bool publish_test_frame(libvlc_media_player_t* player) {
    if (player == nullptr || player->output.engine != libvlc_video_engine_opengl ||
        player->output.setup == nullptr || player->output.update_output == nullptr ||
        player->output.swap == nullptr || player->output.make_current == nullptr ||
        player->output.get_proc_address == nullptr) {
        return false;
    }
    cleanup_output(player);
    void* opaque = player->output.opaque;
    libvlc_video_setup_device_cfg_t device_config{};
    libvlc_video_setup_device_info_t device_info{};
    if (!player->output.setup(&opaque, &device_config, &device_info)) return false;
    player->output.opaque = opaque;
    player->output.setup_active = true;
    if (player->output.window != nullptr) {
        player->output.window(opaque, nullptr, nullptr, nullptr, nullptr, nullptr);
    }
    if (player->output.get_proc_address(opaque, "glFlush") == nullptr ||
        !player->output.make_current(opaque, true)) {
        return false;
    }
    libvlc_video_render_cfg_t render_config{};
    render_config.width = 96;
    render_config.height = 54;
    render_config.bitdepth = player->hdr ? 10U : 8U;
    render_config.full_range = true;
    render_config.colorspace = player->hdr
        ? libvlc_video_colorspace_BT2020
        : libvlc_video_colorspace_BT709;
    render_config.primaries = player->hdr
        ? libvlc_video_primaries_BT2020
        : libvlc_video_primaries_BT709;
    render_config.transfer = player->hdr
        ? libvlc_video_transfer_func_PQ
        : libvlc_video_transfer_func_SRGB;
    libvlc_video_output_cfg_t output_config{};
    const bool updated = player->output.update_output(opaque, &render_config, &output_config);
    const bool left_context = player->output.make_current(opaque, false);
    constexpr int gl_rgba = 0x1908;
    const auto expected_transfer = player->hdr
        ? libvlc_video_transfer_func_LINEAR
        : libvlc_video_transfer_func_SRGB;
    if (!updated || !left_context || output_config.u.opengl_format != gl_rgba ||
        !output_config.full_range || output_config.colorspace != libvlc_video_colorspace_BT709 ||
        output_config.primaries != libvlc_video_primaries_BT709 ||
        output_config.transfer != expected_transfer ||
        output_config.orientation != libvlc_video_orient_top_left) {
        return false;
    }
    player->output.swap(opaque);
    return true;
}

} // namespace

extern "C" {

libvlc_instance_t* libvlc_new(int, const char* const*) {
    return new (std::nothrow) libvlc_instance_t();
}

void libvlc_release(libvlc_instance_t* instance) { delete instance; }

const char* libvlc_errmsg() { return "KMediaVlc fake libVLC test fixture"; }

void libvlc_log_set(libvlc_instance_t*, libvlc_log_cb, void*) {}

libvlc_media_player_t* libvlc_media_player_new(
    libvlc_instance_t*,
    const libvlc_media_player_cbs* callbacks,
    void* callbacks_opaque) {
    auto* player = new (std::nothrow) libvlc_media_player_t();
    if (player != nullptr) {
        player->callbacks = callbacks;
        player->callbacks_opaque = callbacks_opaque;
    }
    return player;
}

void libvlc_media_player_release(libvlc_media_player_t* player) {
    cleanup_output(player);
    cleanup_vmem(player);
    delete player;
}

void libvlc_media_player_set_media(libvlc_media_player_t* player, libvlc_media_t* media) {
    if (player == nullptr) return;
    player->hdr = media != nullptr && media->hdr;
    player->eos_sequence = media != nullptr && media->eos_sequence;
}

int libvlc_media_player_play(libvlc_media_player_t* player) {
    const bool published = player != nullptr &&
        (player->output.engine == libvlc_video_engine_disable
             ? publish_cpu_test_frame(player)
             : publish_test_frame(player));
    if (!published) return -1;
    if (player->callbacks != nullptr && player->callbacks->on_state_changed != nullptr) {
        player->callbacks->on_state_changed(player->callbacks_opaque, libvlc_Playing);
    }
    if (player->eos_sequence && player->callbacks != nullptr) {
        if (player->callbacks->on_media_stopping != nullptr) {
            player->callbacks->on_media_stopping(
                player->callbacks_opaque,
                nullptr,
                libvlc_stopping_reason_eos);
        }
        if (player->callbacks->on_state_changed != nullptr) {
            player->callbacks->on_state_changed(player->callbacks_opaque, libvlc_Stopping);
            player->callbacks->on_state_changed(player->callbacks_opaque, libvlc_Stopped);
        }
    }
    return 0;
}

void libvlc_media_player_set_pause(libvlc_media_player_t*, int) {}

int libvlc_media_player_stop_async(libvlc_media_player_t*) { return 0; }

int libvlc_media_player_set_time(libvlc_media_player_t*, libvlc_time_t, bool) { return 0; }

libvlc_time_t libvlc_media_player_get_time(libvlc_media_player_t*) { return 0; }

libvlc_time_t libvlc_media_player_get_length(libvlc_media_player_t*) { return 1'000'000; }

bool libvlc_media_player_is_seekable(libvlc_media_player_t*) { return true; }

int libvlc_media_player_set_rate(libvlc_media_player_t*, float) { return 0; }

int libvlc_audio_set_volume(libvlc_media_player_t*, int) { return 0; }

libvlc_media_t* libvlc_media_new_location(const char* location) {
    auto* media = new (std::nothrow) libvlc_media_t();
    if (media != nullptr && location != nullptr) {
        const std::string_view value(location);
        media->hdr = value.find("hdr") != std::string_view::npos;
        media->eos_sequence = value.find("eos-terminal") != std::string_view::npos;
    }
    return media;
}

libvlc_media_t* libvlc_media_new_path(const char* path) {
    return libvlc_media_new_location(path);
}

void libvlc_media_add_option(libvlc_media_t*, const char*) {}

void libvlc_media_release(libvlc_media_t* media) { delete media; }

void libvlc_video_set_callbacks(
    libvlc_media_player_t* player,
    libvlc_video_lock_cb lock,
    libvlc_video_unlock_cb unlock,
    libvlc_video_display_cb display,
    void* opaque) {
    if (player == nullptr) return;
    cleanup_vmem(player);
    player->vmem.lock = lock;
    player->vmem.unlock = unlock;
    player->vmem.display = display;
    player->vmem.opaque = opaque;
}

void libvlc_video_set_format_callbacks(
    libvlc_media_player_t* player,
    libvlc_video_format_cb format,
    libvlc_video_cleanup_cb cleanup) {
    if (player == nullptr) return;
    cleanup_vmem(player);
    player->vmem.format = format;
    player->vmem.cleanup = cleanup;
}

bool libvlc_video_set_output_callbacks(
    libvlc_media_player_t* player,
    libvlc_video_engine_t engine,
    libvlc_video_output_setup_cb setup,
    libvlc_video_output_cleanup_cb cleanup,
    libvlc_video_output_set_window_cb window,
    libvlc_video_update_output_cb update_output,
    libvlc_video_swap_cb swap,
    libvlc_video_makeCurrent_cb make_current,
    libvlc_video_getProcAddress_cb get_proc_address,
    libvlc_video_frameMetadata_cb,
    libvlc_video_output_select_plane_cb,
    void* opaque) {
    if (player == nullptr) return false;
    cleanup_output(player);
    player->output = {};
    if (engine == libvlc_video_engine_disable) return true;
    if (engine != libvlc_video_engine_opengl || setup == nullptr || update_output == nullptr ||
        swap == nullptr || make_current == nullptr || get_proc_address == nullptr) {
        return false;
    }
    player->output.engine = engine;
    player->output.setup = setup;
    player->output.cleanup = cleanup;
    player->output.window = window;
    player->output.update_output = update_output;
    player->output.swap = swap;
    player->output.make_current = make_current;
    player->output.get_proc_address = get_proc_address;
    player->output.opaque = opaque;
    return true;
}

} // extern "C"
