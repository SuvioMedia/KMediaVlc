// SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

#pragma once

#include "kmediavlc_client.h"

#include <vlc/vlc.h>

#include <atomic>
#include <cstdint>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace kmediavlc {

class LibVlcApi final {
public:
    static std::shared_ptr<LibVlcApi> load(const char* path, std::string& error);
    ~LibVlcApi();

    LibVlcApi(const LibVlcApi&) = delete;
    LibVlcApi& operator=(const LibVlcApi&) = delete;

    decltype(&libvlc_new) new_instance = nullptr;
    decltype(&libvlc_release) release_instance = nullptr;
    decltype(&libvlc_errmsg) error_message = nullptr;
    decltype(&libvlc_log_set) log_set = nullptr;
    decltype(&libvlc_media_player_new) media_player_new = nullptr;
    decltype(&libvlc_media_player_release) media_player_release = nullptr;
    decltype(&libvlc_media_player_set_media) media_player_set_media = nullptr;
    decltype(&libvlc_media_player_play) media_player_play = nullptr;
    decltype(&libvlc_media_player_set_pause) media_player_set_pause = nullptr;
    decltype(&libvlc_media_player_stop_async) media_player_stop = nullptr;
    decltype(&libvlc_media_player_set_time) media_player_set_time = nullptr;
    decltype(&libvlc_media_player_get_time) media_player_get_time = nullptr;
    decltype(&libvlc_media_player_get_length) media_player_get_length = nullptr;
    decltype(&libvlc_media_player_is_seekable) media_player_is_seekable = nullptr;
    decltype(&libvlc_media_player_set_rate) media_player_set_rate = nullptr;
    decltype(&libvlc_audio_set_volume) audio_set_volume = nullptr;
    decltype(&libvlc_media_new_location) media_new_location = nullptr;
    decltype(&libvlc_media_new_path) media_new_path = nullptr;
    decltype(&libvlc_media_add_option) media_add_option = nullptr;
    decltype(&libvlc_media_release) media_release = nullptr;
    decltype(&libvlc_video_set_callbacks) video_set_callbacks = nullptr;
    decltype(&libvlc_video_set_format_callbacks) video_set_format_callbacks = nullptr;
    decltype(&libvlc_video_set_output_callbacks) video_set_output_callbacks = nullptr;

private:
    explicit LibVlcApi(void* module) : module_(module) {}
    void* module_ = nullptr;
};

struct OutputTargetSnapshot final {
    kmediavlc_output_target_type type = KMEDIAVLC_OUTPUT_UNAVAILABLE;
    std::uint64_t generation = 0;
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    bool request_hdr = false;
    float sdr_white_nits = 203.0F;
    float display_peak_nits = 203.0F;
    std::uint64_t adapter_luid = 0;
    std::uintptr_t metal_device = 0;
    std::uintptr_t metal_command_queue = 0;
    std::string render_node;
    std::vector<kmediavlc_drm_format_modifier> drm_formats;
    bool acquire_fences = false;
    bool release_fences = false;
};

class PlatformRenderer {
public:
    virtual ~PlatformRenderer() = default;
    virtual bool install(libvlc_media_player_t* media_player, std::string& error) = 0;
    virtual void uninstall(libvlc_media_player_t* media_player) noexcept = 0;
    virtual bool output_target_changed(const OutputTargetSnapshot& target, std::string& error) = 0;
    virtual bool resize(std::uint32_t width, std::uint32_t height) = 0;
};

std::unique_ptr<PlatformRenderer> create_platform_renderer(::kmediavlc_player* player);

void set_error(::kmediavlc_player* player, std::string message);
OutputTargetSnapshot copy_output_target(::kmediavlc_player* player);
std::int64_t current_position_microseconds(::kmediavlc_player* player) noexcept;
void publish_frame(::kmediavlc_player* player, std::unique_ptr<::kmediavlc_frame> frame);

} // namespace kmediavlc

struct kmediavlc_frame final {
    using PlatformRelease = void (*)(void*, std::intptr_t, bool) noexcept;

    ~kmediavlc_frame();

    kmediavlc_frame_info info{};
    std::shared_ptr<void> platform_owner;
    PlatformRelease platform_release = nullptr;
    bool acquired = false;
    std::vector<std::uint8_t> cpu_pixels;
};

struct kmediavlc_player final {
    std::shared_ptr<kmediavlc::LibVlcApi> api;
    libvlc_instance_t* instance = nullptr;
    libvlc_media_player_cbs media_player_callbacks{};
    libvlc_media_player_t* media_player = nullptr;
    std::unique_ptr<kmediavlc::PlatformRenderer> renderer;

    kmediavlc_delivery_mode delivery_mode = KMEDIAVLC_CPU_PULL;
    bool request_hdr = false;
    float initial_sdr_white_nits = 203.0F;
    float initial_display_peak_nits = 203.0F;
    kmediavlc_frame_available_cb frame_available = nullptr;
    kmediavlc_playback_state_cb playback_state_changed = nullptr;
    void* callback_opaque = nullptr;

    std::atomic<bool> callbacks_enabled{true};
    std::atomic<bool> loop{false};
    std::atomic<kmediavlc_playback_state> state{KMEDIAVLC_STATE_IDLE};
    std::atomic<kmediavlc_playback_state> state_before_buffering{KMEDIAVLC_STATE_IDLE};
    std::atomic<std::uint64_t> media_generation{0};
    std::atomic<std::int64_t> position_microseconds{0};
    std::atomic<std::int64_t> duration_microseconds{0};
    std::atomic<std::uint32_t> video_width{0};
    std::atomic<std::uint32_t> video_height{0};
    std::atomic<std::uint32_t> buffered_permille{0};
    std::atomic<bool> seekable{false};
    std::atomic<std::uint64_t> next_serial{1};

    std::mutex output_mutex;
    kmediavlc::OutputTargetSnapshot output_target;
    libvlc_video_output_resize_cb report_resize = nullptr;
    void* report_resize_opaque = nullptr;

    std::mutex frame_mutex;
    std::unique_ptr<kmediavlc_frame> pending_frame;

    std::mutex error_mutex;
    std::string last_error;

    struct CpuPicture final {
        std::vector<std::uint8_t> pixels;
        std::uint32_t width = 0;
        std::uint32_t height = 0;
        std::uint32_t stride = 0;
    };
    std::mutex cpu_mutex;
    std::unique_ptr<CpuPicture> cpu_picture;
};
