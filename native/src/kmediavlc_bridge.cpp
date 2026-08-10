// SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

#include "bridge_internal.hpp"

#include <algorithm>
#include <array>
#include <cctype>
#include <cmath>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <limits>
#include <new>
#include <utility>

#if defined(_WIN32)
#  include <windows.h>
#else
#  include <dlfcn.h>
#  include <unistd.h>
#endif

namespace {

constexpr std::uint32_t kBridgeAbi = KMEDIAVLC_BRIDGE_ABI_VERSION;
constexpr std::size_t kCpuAlignment = 64;
constexpr std::uint32_t kMaximumCpuDimension = 16'384;
constexpr std::size_t kMaximumCpuFrameBytes = 512U * 1024U * 1024U;

std::mutex g_api_mutex;
std::mutex g_debug_log_mutex;
std::shared_ptr<kmediavlc::LibVlcApi> g_api;
std::filesystem::path g_api_path;
std::filesystem::path g_plugin_path;
#if defined(_WIN32)
std::filesystem::path g_runtime_dll_directory_path;
PVOID g_runtime_dll_directory_cookie = nullptr;
#endif

bool diagnostic_logging_enabled() {
    const char* value = std::getenv("KMEDIAVLC_DEBUG_CALLBACKS");
    return value != nullptr && value[0] == '1' && value[1] == '\0';
}

void diagnostic_log(void*, int level, const libvlc_log_t*, const char* format, va_list arguments) {
    if (!diagnostic_logging_enabled() || format == nullptr) return;
    std::lock_guard lock(g_debug_log_mutex);
    std::fprintf(stderr, "[KMediaVlc libVLC level=%d] ", level);
    std::vfprintf(stderr, format, arguments);
    std::fputc('\n', stderr);
    std::fflush(stderr);
}

std::filesystem::path path_from_utf8(const char* value) {
    if (value == nullptr || *value == '\0') return {};
#if defined(_WIN32)
    const int required = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value, -1, nullptr, 0);
    if (required <= 1) return {};
    std::wstring wide(static_cast<std::size_t>(required), L'\0');
    if (MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value, -1, wide.data(), required) == 0) return {};
    wide.pop_back();
    return std::filesystem::path(wide);
#else
    return std::filesystem::path(value);
#endif
}

std::filesystem::path normalized_path(const char* value) {
    std::error_code error;
    auto path = std::filesystem::absolute(path_from_utf8(value), error);
    if (error) return {};
    return path.lexically_normal();
}

bool configure_plugin_directory(const char* value, std::string& error) {
    const auto path = normalized_path(value);
    if (path.empty() || !std::filesystem::is_directory(path)) {
        error = "The verified libVLC plugin directory is invalid.";
        return false;
    }
    std::lock_guard lock(g_api_mutex);
    if (!g_plugin_path.empty() && g_plugin_path != path) {
        error = "One process cannot use plugin directories from two different VLC runtimes.";
        return false;
    }
#if defined(_WIN32)
    // libVLC reads VLC_PLUGIN_PATH through the UCRT environment. Updating only
    // the Win32 environment block with SetEnvironmentVariableW leaves the CRT
    // copy stale when the host process was already running (for example a JVM).
    if (_wputenv_s(L"VLC_PLUGIN_PATH", path.c_str()) != 0) {
        error = "The verified VLC_PLUGIN_PATH could not be configured.";
        return false;
    }
#else
    if (setenv("VLC_PLUGIN_PATH", path.c_str(), 1) != 0) {
        error = "The verified VLC_PLUGIN_PATH could not be configured.";
        return false;
    }
#endif
    g_plugin_path = path;
    return true;
}

#if defined(_WIN32)
bool configure_runtime_dll_directory(const std::filesystem::path& library_path, std::string& error) {
    const auto directory = library_path.parent_path();
    if (directory.empty() || !std::filesystem::is_directory(directory)) {
        error = "The verified libVLC runtime directory is invalid.";
        return false;
    }
    if (g_runtime_dll_directory_cookie != nullptr) {
        if (g_runtime_dll_directory_path != directory) {
            error = "One process cannot use DLL directories from two different VLC runtimes.";
            return false;
        }
        return true;
    }

    const HMODULE kernel32 = GetModuleHandleW(L"kernel32.dll");
    if (kernel32 == nullptr) {
        error = "The Windows DLL loader could not be resolved.";
        return false;
    }
    using SetDefaultDllDirectoriesFunction = BOOL(WINAPI*)(DWORD);
    using AddDllDirectoryFunction = PVOID(WINAPI*)(PCWSTR);
    const auto set_default_dll_directories = reinterpret_cast<SetDefaultDllDirectoriesFunction>(
        GetProcAddress(kernel32, "SetDefaultDllDirectories"));
    const auto add_dll_directory = reinterpret_cast<AddDllDirectoryFunction>(
        GetProcAddress(kernel32, "AddDllDirectory"));
    if (set_default_dll_directories == nullptr || add_dll_directory == nullptr) {
        error = "The Windows host lacks secure DLL directory APIs required by KMediaVlc.";
        return false;
    }
    if (!set_default_dll_directories(LOAD_LIBRARY_SEARCH_DEFAULT_DIRS)) {
        error = "The secure Windows DLL search policy could not be configured.";
        return false;
    }
    const PVOID cookie = add_dll_directory(directory.c_str());
    if (cookie == nullptr) {
        error = "The verified libVLC runtime directory could not be registered.";
        return false;
    }
    // libVLC loads plugins on worker threads. Keep its one verified directory in
    // the process search path for the same lifetime as the deliberately retained
    // libVLC module; removing it while a player is stopping would race plugin loads.
    g_runtime_dll_directory_path = directory;
    g_runtime_dll_directory_cookie = cookie;
    return true;
}

void* open_module(const std::filesystem::path& path, std::string& error) {
    HMODULE module = LoadLibraryExW(
        path.c_str(),
        nullptr,
        LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
    if (module == nullptr) error = "LoadLibraryExW failed for the verified libVLC runtime.";
    return module;
}

void* load_symbol(void* module, const char* name) {
    return reinterpret_cast<void*>(GetProcAddress(static_cast<HMODULE>(module), name));
}
#else
void* open_module(const std::filesystem::path& path, std::string& error) {
    void* module = dlopen(path.c_str(), RTLD_NOW | RTLD_LOCAL);
    if (module == nullptr) {
        const char* detail = dlerror();
        error = detail == nullptr ? "dlopen failed for the verified libVLC runtime." : detail;
    }
    return module;
}

void* load_symbol(void* module, const char* name) {
    return dlsym(module, name);
}
#endif

template <typename Function>
bool bind_symbol(void* module, Function& destination, const char* name, std::string& error) {
    destination = reinterpret_cast<Function>(load_symbol(module, name));
    if (destination != nullptr) return true;
    error = std::string("The pinned libVLC 4 runtime is missing symbol ") + name + '.';
    return false;
}

void notify_state(kmediavlc_player* player, kmediavlc_playback_state state) {
    if (player == nullptr) return;
    player->state.store(state, std::memory_order_release);
    if (!player->callbacks_enabled.load(std::memory_order_acquire)) return;
    auto callback = player->playback_state_changed;
    if (callback != nullptr) {
        callback(
            player->callback_opaque,
            state,
            player->media_generation.load(std::memory_order_acquire));
    }
}

kmediavlc_playback_state map_state(libvlc_state_t state) {
    switch (state) {
        case libvlc_NothingSpecial: return KMEDIAVLC_STATE_IDLE;
        case libvlc_Opening: return KMEDIAVLC_STATE_OPENING;
        case libvlc_Playing: return KMEDIAVLC_STATE_PLAYING;
        case libvlc_Paused: return KMEDIAVLC_STATE_PAUSED;
        case libvlc_Stopped: return KMEDIAVLC_STATE_STOPPED;
        case libvlc_Stopping: return KMEDIAVLC_STATE_STOPPED;
        case libvlc_Error: return KMEDIAVLC_STATE_ERROR;
    }
    return KMEDIAVLC_STATE_ERROR;
}

void on_state_changed(void* opaque, libvlc_state_t state) {
    auto* player = static_cast<kmediavlc_player*>(opaque);
    if (player == nullptr) return;
    const auto mapped = map_state(state);
    if (mapped == KMEDIAVLC_STATE_STOPPED) {
        const auto current = player->state.load(std::memory_order_acquire);
        // VLC reports the semantic stop reason separately, then may emit a
        // generic Stopping/Stopped state. Keep the richer terminal result.
        if (current == KMEDIAVLC_STATE_ENDED || current == KMEDIAVLC_STATE_ERROR) return;
    }
    player->state_before_buffering.store(mapped, std::memory_order_release);
    notify_state(player, mapped);
}

void on_buffering_changed(void* opaque, float buffering) {
    auto* player = static_cast<kmediavlc_player*>(opaque);
    if (player == nullptr) return;
    const float bounded = std::clamp(buffering, 0.0F, 1.0F);
    player->buffered_permille.store(
        static_cast<std::uint32_t>(std::lround(bounded * 1000.0F)),
        std::memory_order_release);
    if (bounded < 1.0F) {
        const auto current = player->state.load(std::memory_order_acquire);
        if (current != KMEDIAVLC_STATE_BUFFERING) {
            player->state_before_buffering.store(current, std::memory_order_release);
        }
        notify_state(player, KMEDIAVLC_STATE_BUFFERING);
    } else if (player->state.load(std::memory_order_acquire) == KMEDIAVLC_STATE_BUFFERING) {
        const auto previous = player->state_before_buffering.load(std::memory_order_acquire);
        notify_state(
            player,
            previous == KMEDIAVLC_STATE_BUFFERING ? KMEDIAVLC_STATE_PLAYING : previous);
    }
}

void on_position_changed(void* opaque, libvlc_time_t time, double) {
    auto* player = static_cast<kmediavlc_player*>(opaque);
    if (player != nullptr) {
        player->position_microseconds.store(std::max<libvlc_time_t>(0, time), std::memory_order_release);
    }
}

void on_length_changed(void* opaque, libvlc_time_t length) {
    auto* player = static_cast<kmediavlc_player*>(opaque);
    if (player != nullptr) {
        player->duration_microseconds.store(std::max<libvlc_time_t>(0, length), std::memory_order_release);
    }
}

void on_media_stopping(void* opaque, libvlc_media_t*, libvlc_stopping_reason_t reason) {
    auto* player = static_cast<kmediavlc_player*>(opaque);
    if (player == nullptr) return;
    switch (reason) {
        case libvlc_stopping_reason_eos:
            notify_state(player, KMEDIAVLC_STATE_ENDED);
            break;
        case libvlc_stopping_reason_error:
            notify_state(player, KMEDIAVLC_STATE_ERROR);
            break;
        case libvlc_stopping_reason_user:
            notify_state(player, KMEDIAVLC_STATE_STOPPED);
            break;
    }
}

unsigned cpu_format(
    void** opaque,
    char* chroma,
    unsigned* width,
    unsigned* height,
    unsigned* pitches,
    unsigned* lines) {
    if (opaque == nullptr || *opaque == nullptr || chroma == nullptr || width == nullptr ||
        height == nullptr || pitches == nullptr || lines == nullptr) {
        return 0;
    }
    auto* player = static_cast<kmediavlc_player*>(*opaque);
    // VLC 4 passes coded and visible dimensions as adjacent entries. vmem later
    // adopts entry zero as the output geometry, so explicitly request the
    // visible rectangle instead of exposing decoder padding through our ABI.
    constexpr std::size_t coded_dimension = 0U;
    constexpr std::size_t visible_dimension = 1U;
    const unsigned coded_width = width[coded_dimension];
    const unsigned coded_height = height[coded_dimension];
    const unsigned visible_width = width[visible_dimension];
    const unsigned visible_height = height[visible_dimension];
    if (coded_width == 0 || coded_height == 0 || visible_width == 0 || visible_height == 0 ||
        coded_width > kMaximumCpuDimension || coded_height > kMaximumCpuDimension ||
        visible_width > coded_width || visible_height > coded_height) {
        kmediavlc::set_error(player, "The decoded CPU frame geometry is invalid.");
        return 0;
    }
    width[coded_dimension] = visible_width;
    height[coded_dimension] = visible_height;
    constexpr std::size_t maximum = std::numeric_limits<std::size_t>::max();
    const std::size_t width_value = static_cast<std::size_t>(visible_width);
    if (width_value > (maximum - (kCpuAlignment - 1U)) / 4U) {
        kmediavlc::set_error(player, "The decoded CPU frame dimensions are unsafe.");
        return 0;
    }
    const std::size_t raw_stride = width_value * 4U;
    const std::size_t stride = (raw_stride + kCpuAlignment - 1U) & ~(kCpuAlignment - 1U);
    const std::size_t height_value = static_cast<std::size_t>(visible_height);
    if (stride > std::numeric_limits<unsigned>::max() || height_value > maximum / stride) {
        kmediavlc::set_error(player, "The decoded CPU frame dimensions are unsafe.");
        return 0;
    }
    const std::size_t bytes = stride * height_value;
    if (bytes > kMaximumCpuFrameBytes) {
        kmediavlc::set_error(player, "The decoded CPU frame exceeds the bounded pull-buffer size.");
        return 0;
    }
    auto picture = std::make_unique<kmediavlc_player::CpuPicture>();
    try {
        picture->pixels.resize(bytes);
    } catch (const std::bad_alloc&) {
        kmediavlc::set_error(player, "The decoded CPU frame buffer could not be allocated.");
        return 0;
    }
    picture->width = visible_width;
    picture->height = visible_height;
    picture->stride = static_cast<std::uint32_t>(stride);
    {
        std::lock_guard lock(player->cpu_mutex);
        player->cpu_picture = std::move(picture);
    }
    std::memcpy(chroma, "RGBA", 4);
    pitches[0] = static_cast<unsigned>(stride);
    lines[0] = visible_height;
    player->video_width.store(visible_width, std::memory_order_release);
    player->video_height.store(visible_height, std::memory_order_release);
    return 1;
}

void cpu_cleanup(void* opaque) {
    auto* player = static_cast<kmediavlc_player*>(opaque);
    if (player == nullptr) return;
    std::lock_guard lock(player->cpu_mutex);
    player->cpu_picture.reset();
}

void* cpu_lock(void* opaque, void** planes) {
    auto* player = static_cast<kmediavlc_player*>(opaque);
    if (player == nullptr || planes == nullptr) return nullptr;
    std::lock_guard lock(player->cpu_mutex);
    if (!player->cpu_picture) return nullptr;
    planes[0] = player->cpu_picture->pixels.data();
    return player->cpu_picture.get();
}

void cpu_unlock(void*, void*, void* const*) {}

void cpu_display(void* opaque, void* picture_value) {
    auto* player = static_cast<kmediavlc_player*>(opaque);
    auto* picture = static_cast<kmediavlc_player::CpuPicture*>(picture_value);
    if (player == nullptr || picture == nullptr) return;

    auto frame = std::make_unique<kmediavlc_frame>();
    {
        std::lock_guard lock(player->cpu_mutex);
        if (!player->cpu_picture || player->cpu_picture.get() != picture) return;
        try {
            frame->cpu_pixels = picture->pixels;
        } catch (const std::bad_alloc&) {
            kmediavlc::set_error(player, "The latest CPU frame could not be copied.");
            return;
        }
    }
    const auto target = kmediavlc::copy_output_target(player);
    const std::uint64_t media_generation =
        std::max<std::uint64_t>(1, player->media_generation.load(std::memory_order_acquire));
    frame->info.struct_size = sizeof(kmediavlc_frame_info);
    frame->info.bridge_abi_version = kBridgeAbi;
    frame->info.output_generation = target.generation == 0 ? media_generation : target.generation;
    frame->info.pts_microseconds = player->position_microseconds.load(std::memory_order_acquire);
    frame->info.width = picture->width;
    frame->info.height = picture->height;
    frame->info.pixel_format = KMEDIAVLC_RGBA8_SRGB;
    frame->info.source_dynamic_range = KMEDIAVLC_SOURCE_DYNAMIC_RANGE_UNKNOWN;
    frame->info.handle_type = KMEDIAVLC_CPU_ADDRESS;
    frame->info.platform_handle = reinterpret_cast<std::uintptr_t>(frame->cpu_pixels.data());
    frame->info.acquire_fence = -1;
    frame->info.stride = picture->stride;
    frame->info.cpu_byte_count = frame->cpu_pixels.size();
    frame->info.sdr_white_nits = target.sdr_white_nits;
    frame->info.content_peak_nits = target.sdr_white_nits;
    frame->info.premultiplied_alpha = true;
    kmediavlc::publish_frame(player, std::move(frame));
}

bool is_probable_path(std::string_view value) {
#if defined(_WIN32)
    if (value.size() >= 3U && std::isalpha(static_cast<unsigned char>(value[0])) != 0 &&
        value[1] == ':' && (value[2] == '\\' || value[2] == '/')) return true;
#endif
    if (!value.empty() && (value.front() == '/' || value.front() == '.')) return true;
    const auto colon = value.find(':');
    if (colon == std::string_view::npos) return true;
    if (colon == 0 || std::isalpha(static_cast<unsigned char>(value[0])) == 0) return true;
    return false;
}

std::optional<std::string> media_option_for_header(std::string_view name, std::string_view value) {
    auto equals_ascii_case = [](std::string_view left, std::string_view right) {
        if (left.size() != right.size()) return false;
        for (std::size_t index = 0; index < left.size(); ++index) {
            const auto a = static_cast<unsigned char>(left[index]);
            const auto b = static_cast<unsigned char>(right[index]);
            if (std::tolower(a) != std::tolower(b)) return false;
        }
        return true;
    };
    if (equals_ascii_case(name, "User-Agent")) return ":http-user-agent=" + std::string(value);
    if (equals_ascii_case(name, "Referer")) return ":http-referrer=" + std::string(value);
    if (equals_ascii_case(name, "Cookie")) return ":http-cookie=" + std::string(value);
    return std::nullopt;
}

bool valid_player(const kmediavlc_player* player) {
    return player != nullptr && player->media_player != nullptr && player->api != nullptr;
}

void capture_vlc_error(kmediavlc_player* player, std::string fallback) {
    if (player != nullptr && player->api && player->api->error_message) {
        const char* detail = player->api->error_message();
        if (detail != nullptr && *detail != '\0') fallback = detail;
    }
    kmediavlc::set_error(player, std::move(fallback));
}

} // namespace

namespace kmediavlc {

LibVlcApi::~LibVlcApi() {
    // libVLC owns worker threads and process-wide plugin state. Deliberately keep
    // the one verified module loaded until the OS tears down the process; an
    // explicit FreeLibrary/dlclose during static destruction can race late TLS
    // and plugin cleanup.
    module_ = nullptr;
}

std::shared_ptr<LibVlcApi> LibVlcApi::load(const char* path_value, std::string& error) {
    const auto path = normalized_path(path_value);
    if (path.empty() || !std::filesystem::is_regular_file(path)) {
        error = "The verified libVLC dynamic library path is invalid.";
        return {};
    }
    std::lock_guard lock(g_api_mutex);
    if (g_api) {
        if (g_api_path != path) {
            error = "One process cannot load two different libVLC runtimes.";
            return {};
        }
        return g_api;
    }
#if defined(_WIN32)
    if (!configure_runtime_dll_directory(path, error)) return {};
#endif
    void* module = open_module(path, error);
    if (module == nullptr) return {};
    auto api = std::shared_ptr<LibVlcApi>(new LibVlcApi(module));
#define KMEDIAVLC_BIND(member, symbol) \
    if (!bind_symbol(module, api->member, symbol, error)) return {}
    KMEDIAVLC_BIND(new_instance, "libvlc_new");
    KMEDIAVLC_BIND(release_instance, "libvlc_release");
    KMEDIAVLC_BIND(error_message, "libvlc_errmsg");
    KMEDIAVLC_BIND(log_set, "libvlc_log_set");
    KMEDIAVLC_BIND(media_player_new, "libvlc_media_player_new");
    KMEDIAVLC_BIND(media_player_release, "libvlc_media_player_release");
    KMEDIAVLC_BIND(media_player_set_media, "libvlc_media_player_set_media");
    KMEDIAVLC_BIND(media_player_play, "libvlc_media_player_play");
    KMEDIAVLC_BIND(media_player_set_pause, "libvlc_media_player_set_pause");
    KMEDIAVLC_BIND(media_player_stop, "libvlc_media_player_stop_async");
    KMEDIAVLC_BIND(media_player_set_time, "libvlc_media_player_set_time");
    KMEDIAVLC_BIND(media_player_get_time, "libvlc_media_player_get_time");
    KMEDIAVLC_BIND(media_player_get_length, "libvlc_media_player_get_length");
    KMEDIAVLC_BIND(media_player_is_seekable, "libvlc_media_player_is_seekable");
    KMEDIAVLC_BIND(media_player_set_rate, "libvlc_media_player_set_rate");
    KMEDIAVLC_BIND(audio_set_volume, "libvlc_audio_set_volume");
    KMEDIAVLC_BIND(media_new_location, "libvlc_media_new_location");
    KMEDIAVLC_BIND(media_new_path, "libvlc_media_new_path");
    KMEDIAVLC_BIND(media_add_option, "libvlc_media_add_option");
    KMEDIAVLC_BIND(media_release, "libvlc_media_release");
    KMEDIAVLC_BIND(video_set_callbacks, "libvlc_video_set_callbacks");
    KMEDIAVLC_BIND(video_set_format_callbacks, "libvlc_video_set_format_callbacks");
    KMEDIAVLC_BIND(video_set_output_callbacks, "libvlc_video_set_output_callbacks");
#undef KMEDIAVLC_BIND
    g_api_path = path;
    g_api = api;
    return api;
}

void set_error(kmediavlc_player* player, std::string message) {
    if (player == nullptr) return;
    std::lock_guard lock(player->error_mutex);
    player->last_error = std::move(message);
}

OutputTargetSnapshot copy_output_target(kmediavlc_player* player) {
    if (player == nullptr) return {};
    std::lock_guard lock(player->output_mutex);
    return player->output_target;
}

std::int64_t current_position_microseconds(kmediavlc_player* player) noexcept {
    return player == nullptr ? 0 : player->position_microseconds.load(std::memory_order_acquire);
}

void publish_frame(kmediavlc_player* player, std::unique_ptr<kmediavlc_frame> frame) {
    if (player == nullptr || !frame) return;
    frame->info.struct_size = sizeof(kmediavlc_frame_info);
    frame->info.bridge_abi_version = kBridgeAbi;
    frame->info.serial = player->next_serial.fetch_add(1, std::memory_order_acq_rel);
    const auto serial = frame->info.serial;
    const auto generation = frame->info.output_generation;
    std::unique_ptr<kmediavlc_frame> superseded;
    {
        std::lock_guard lock(player->frame_mutex);
        superseded = std::move(player->pending_frame);
        player->pending_frame = std::move(frame);
    }
    if (!player->callbacks_enabled.load(std::memory_order_acquire)) return;
    if (player->frame_available != nullptr) {
        player->frame_available(player->callback_opaque, serial, generation);
    }
}

} // namespace kmediavlc

kmediavlc_frame::~kmediavlc_frame() {
#if !defined(_WIN32)
    if (info.acquire_fence >= 0) close(static_cast<int>(info.acquire_fence));
    if (info.handle_type == KMEDIAVLC_DMABUF &&
        info.platform_handle <= static_cast<std::uintptr_t>(std::numeric_limits<int>::max())) {
        close(static_cast<int>(info.platform_handle));
    }
#endif
    if (platform_release != nullptr) {
        platform_release(platform_owner.get(), -1, acquired);
    }
}

extern "C" {

kmediavlc_player* kmediavlc_player_create(const kmediavlc_player_config* config) {
    if (config == nullptr || config->struct_size != sizeof(kmediavlc_player_config) ||
        config->bridge_abi_version != kBridgeAbi || config->libvlc_path_utf8 == nullptr ||
        config->plugin_directory_utf8 == nullptr ||
        (config->delivery_mode != KMEDIAVLC_GPU_PUSH && config->delivery_mode != KMEDIAVLC_CPU_PULL) ||
        !std::isfinite(config->sdr_white_nits) || config->sdr_white_nits <= 0.0F ||
        !std::isfinite(config->display_peak_nits) ||
        config->display_peak_nits < config->sdr_white_nits) {
        return nullptr;
    }
    auto player = std::make_unique<kmediavlc_player>();
    player->delivery_mode = config->delivery_mode;
    player->request_hdr = config->request_hdr;
    player->initial_sdr_white_nits = config->sdr_white_nits;
    player->initial_display_peak_nits = config->display_peak_nits;
    player->frame_available = config->frame_available;
    player->playback_state_changed = config->playback_state_changed;
    player->callback_opaque = config->callback_opaque;
    player->output_target.sdr_white_nits = config->sdr_white_nits;
    player->output_target.display_peak_nits = config->display_peak_nits;

    std::string error;
    player->api = kmediavlc::LibVlcApi::load(config->libvlc_path_utf8, error);
    if (!player->api) return nullptr;

    if (!configure_plugin_directory(config->plugin_directory_utf8, error)) return nullptr;
    std::vector<const char*> arguments{
        "--no-video-title-show",
        "--no-osd",
        "--no-stats",
    };
    const char* debug_callbacks = std::getenv("KMEDIAVLC_DEBUG_CALLBACKS");
    arguments.push_back(
        debug_callbacks != nullptr && std::strcmp(debug_callbacks, "1") == 0
            ? "--verbose=2"
            : "--quiet");
    player->instance = player->api->new_instance(static_cast<int>(arguments.size()), arguments.data());
    if (player->instance == nullptr) return nullptr;
    if (diagnostic_logging_enabled()) {
        player->api->log_set(player->instance, diagnostic_log, player.get());
    }

    player->media_player_callbacks.version = 0;
    player->media_player_callbacks.on_media_stopping = on_media_stopping;
    player->media_player_callbacks.on_state_changed = on_state_changed;
    player->media_player_callbacks.on_buffering_changed = on_buffering_changed;
    player->media_player_callbacks.on_position_changed = on_position_changed;
    player->media_player_callbacks.on_length_changed = on_length_changed;
    player->media_player = player->api->media_player_new(
        player->instance,
        &player->media_player_callbacks,
        player.get());
    if (player->media_player == nullptr) {
        player->api->release_instance(player->instance);
        player->instance = nullptr;
        return nullptr;
    }

    bool installed = false;
    if (player->delivery_mode == KMEDIAVLC_CPU_PULL) {
        player->api->video_set_callbacks(player->media_player, cpu_lock, cpu_unlock, cpu_display, player.get());
        player->api->video_set_format_callbacks(player->media_player, cpu_format, cpu_cleanup);
        installed = true;
    } else {
        player->renderer = kmediavlc::create_platform_renderer(player.get());
        installed = player->renderer && player->renderer->install(player->media_player, error);
    }
    if (!installed) {
        player->callbacks_enabled.store(false, std::memory_order_release);
        player->api->media_player_release(player->media_player);
        player->api->release_instance(player->instance);
        return nullptr;
    }
    return player.release();
}

bool kmediavlc_player_open(
    kmediavlc_player* player,
    const char* uri_utf8,
    const char* const* headers_utf8,
    size_t header_entry_count,
    bool autoplay) {
    if (!valid_player(player) || uri_utf8 == nullptr || *uri_utf8 == '\0' ||
        header_entry_count % 2U != 0 || (header_entry_count != 0 && headers_utf8 == nullptr)) {
        return false;
    }
    libvlc_media_t* media = is_probable_path(uri_utf8)
        ? player->api->media_new_path(uri_utf8)
        : player->api->media_new_location(uri_utf8);
    if (media == nullptr) {
        capture_vlc_error(player, "libVLC could not create the requested media.");
        return false;
    }
    bool options_valid = true;
    for (std::size_t index = 0; index < header_entry_count; index += 2U) {
        if (headers_utf8[index] == nullptr || headers_utf8[index + 1U] == nullptr) {
            options_valid = false;
            break;
        }
        auto option = media_option_for_header(headers_utf8[index], headers_utf8[index + 1U]);
        if (!option) {
            kmediavlc::set_error(player, "The requested HTTP header is not supported by the pinned libVLC API.");
            options_valid = false;
            break;
        }
        player->api->media_add_option(media, option->c_str());
    }
    if (options_valid && player->loop.load(std::memory_order_acquire)) {
        player->api->media_add_option(media, ":input-repeat=65535");
    }
    if (!options_valid) {
        player->api->media_release(media);
        return false;
    }
    player->api->media_player_set_media(player->media_player, media);
    player->api->media_release(media);
    const auto generation = player->media_generation.fetch_add(1, std::memory_order_acq_rel) + 1U;
    (void)generation;
    player->position_microseconds.store(0, std::memory_order_release);
    player->duration_microseconds.store(0, std::memory_order_release);
    player->buffered_permille.store(0, std::memory_order_release);
    player->state_before_buffering.store(KMEDIAVLC_STATE_IDLE, std::memory_order_release);
    notify_state(player, KMEDIAVLC_STATE_IDLE);
    if (!autoplay) return true;
    if (player->api->media_player_play(player->media_player) == 0) return true;
    capture_vlc_error(player, "libVLC rejected autoplay.");
    return false;
}

bool kmediavlc_player_play(kmediavlc_player* player) {
    if (!valid_player(player)) return false;
    if (player->api->media_player_play(player->media_player) == 0) return true;
    capture_vlc_error(player, "libVLC rejected play.");
    return false;
}

bool kmediavlc_player_pause(kmediavlc_player* player) {
    if (!valid_player(player)) return false;
    player->api->media_player_set_pause(player->media_player, 1);
    return true;
}

bool kmediavlc_player_stop(kmediavlc_player* player) {
    if (!valid_player(player)) return false;
    if (player->api->media_player_stop(player->media_player) == 0) return true;
    capture_vlc_error(player, "libVLC rejected stop.");
    return false;
}

bool kmediavlc_player_seek(kmediavlc_player* player, int64_t time_microseconds, bool fast) {
    if (!valid_player(player) || time_microseconds < 0) return false;
    if (player->api->media_player_set_time(player->media_player, time_microseconds, fast) == 0) return true;
    capture_vlc_error(player, "libVLC rejected seek.");
    return false;
}

bool kmediavlc_player_set_volume(kmediavlc_player* player, float volume) {
    if (!valid_player(player) || !std::isfinite(volume)) return false;
    const int percent = static_cast<int>(std::lround(std::clamp(volume, 0.0F, 1.0F) * 100.0F));
    return player->api->audio_set_volume(player->media_player, percent) == 0;
}

bool kmediavlc_player_set_rate(kmediavlc_player* player, float rate) {
    if (!valid_player(player) || !std::isfinite(rate) || rate <= 0.0F) return false;
    return player->api->media_player_set_rate(player->media_player, rate) == 0;
}

bool kmediavlc_player_set_loop(kmediavlc_player* player, bool loop) {
    if (!valid_player(player)) return false;
    player->loop.store(loop, std::memory_order_release);
    return true;
}

bool kmediavlc_player_resize(kmediavlc_player* player, uint32_t width, uint32_t height) {
    if (!valid_player(player) || width == 0 || height == 0) return false;
    libvlc_video_output_resize_cb report = nullptr;
    void* report_opaque = nullptr;
    {
        std::lock_guard lock(player->output_mutex);
        player->output_target.width = width;
        player->output_target.height = height;
        report = player->report_resize;
        report_opaque = player->report_resize_opaque;
    }
    if (player->renderer && !player->renderer->resize(width, height)) return false;
    if (report != nullptr) report(report_opaque, width, height);
    return true;
}

bool kmediavlc_player_update_output(kmediavlc_player* player, const kmediavlc_output_target* target) {
    if (!valid_player(player) || target == nullptr || target->struct_size != sizeof(kmediavlc_output_target) ||
        target->bridge_abi_version != kBridgeAbi ||
        !std::isfinite(target->sdr_white_nits) || target->sdr_white_nits <= 0.0F ||
        !std::isfinite(target->display_peak_nits) || target->display_peak_nits < target->sdr_white_nits ||
        (target->drm_format_count != 0 && target->drm_formats == nullptr)) {
        return false;
    }
    kmediavlc::OutputTargetSnapshot next;
    next.type = target->type;
    next.generation = target->generation;
    next.width = target->width;
    next.height = target->height;
    next.request_hdr = target->request_hdr;
    next.sdr_white_nits = target->sdr_white_nits;
    next.display_peak_nits = target->display_peak_nits;
    next.adapter_luid = target->adapter_luid;
    next.metal_device = target->metal_device;
    next.metal_command_queue = target->metal_command_queue;
    if (target->render_node_utf8 != nullptr) next.render_node = target->render_node_utf8;
    if (target->drm_format_count != 0) {
        next.drm_formats.assign(target->drm_formats, target->drm_formats + target->drm_format_count);
    }
    next.acquire_fences = target->acquire_fences;
    next.release_fences = target->release_fences;

    kmediavlc::OutputTargetSnapshot previous;
    {
        std::lock_guard lock(player->output_mutex);
        previous = player->output_target;
        player->output_target = next;
    }
    if (player->renderer) {
        std::string error;
        if (!player->renderer->output_target_changed(next, error)) {
            std::lock_guard lock(player->output_mutex);
            player->output_target = std::move(previous);
            kmediavlc::set_error(player, error.empty() ? "The GPU output target is unavailable." : std::move(error));
            return false;
        }
    }
    if (next.width != 0 && next.height != 0 &&
        (next.width != previous.width || next.height != previous.height)) {
        libvlc_video_output_resize_cb report = nullptr;
        void* report_opaque = nullptr;
        {
            std::lock_guard lock(player->output_mutex);
            report = player->report_resize;
            report_opaque = player->report_resize_opaque;
        }
        if (report != nullptr) report(report_opaque, next.width, next.height);
    }
    return true;
}

bool kmediavlc_player_get_snapshot(kmediavlc_player* player, kmediavlc_player_snapshot* output) {
    if (!valid_player(player) || output == nullptr || output->struct_size != sizeof(kmediavlc_player_snapshot) ||
        output->bridge_abi_version != kBridgeAbi) return false;
    const auto position = player->api->media_player_get_time(player->media_player);
    const auto duration = player->api->media_player_get_length(player->media_player);
    if (position >= 0) player->position_microseconds.store(position, std::memory_order_release);
    if (duration >= 0) player->duration_microseconds.store(duration, std::memory_order_release);
    player->seekable.store(player->api->media_player_is_seekable(player->media_player), std::memory_order_release);
    output->state = player->state.load(std::memory_order_acquire);
    output->media_generation = player->media_generation.load(std::memory_order_acquire);
    output->position_microseconds = player->position_microseconds.load(std::memory_order_acquire);
    output->duration_microseconds = player->duration_microseconds.load(std::memory_order_acquire);
    output->video_width = player->video_width.load(std::memory_order_acquire);
    output->video_height = player->video_height.load(std::memory_order_acquire);
    output->buffered_permille = player->buffered_permille.load(std::memory_order_acquire);
    output->seekable = player->seekable.load(std::memory_order_acquire);
    return true;
}

const char* kmediavlc_player_last_error(kmediavlc_player* player) {
    if (player == nullptr) return nullptr;
    std::lock_guard lock(player->error_mutex);
    return player->last_error.empty() ? nullptr : player->last_error.c_str();
}

kmediavlc_frame* kmediavlc_player_acquire_latest_frame(
    kmediavlc_player* player,
    kmediavlc_frame_info* output) {
    if (player == nullptr || output == nullptr || output->struct_size != sizeof(kmediavlc_frame_info) ||
        output->bridge_abi_version != kBridgeAbi) return nullptr;
    std::unique_ptr<kmediavlc_frame> frame;
    {
        std::lock_guard lock(player->frame_mutex);
        frame = std::move(player->pending_frame);
    }
    if (!frame) return nullptr;
    *output = frame->info;
    frame->acquired = true;
    frame->info.acquire_fence = -1;
    return frame.release();
}

void kmediavlc_frame_release(kmediavlc_frame* frame, intptr_t release_fence) {
    if (frame != nullptr && frame->platform_release != nullptr) {
        frame->platform_release(
            frame->platform_owner.get(), release_fence, frame->acquired);
        frame->platform_release = nullptr;
        release_fence = -1;
    }
#if !defined(_WIN32)
    if (release_fence >= 0) close(static_cast<int>(release_fence));
#else
    (void)release_fence;
#endif
    delete frame;
}

const void* kmediavlc_frame_cpu_pixels(kmediavlc_frame* frame, size_t* byte_count) {
    if (byte_count != nullptr) *byte_count = 0;
    if (frame == nullptr || frame->info.handle_type != KMEDIAVLC_CPU_ADDRESS) return nullptr;
    if (byte_count != nullptr) *byte_count = frame->cpu_pixels.size();
    return frame->cpu_pixels.data();
}

void kmediavlc_player_destroy(kmediavlc_player* player) {
    if (player == nullptr) return;
    player->callbacks_enabled.store(false, std::memory_order_release);
    std::unique_ptr<kmediavlc_frame> pending;
    {
        std::lock_guard lock(player->frame_mutex);
        pending = std::move(player->pending_frame);
    }
    if (player->renderer && player->media_player) player->renderer->uninstall(player->media_player);
    if (player->media_player) player->api->media_player_release(player->media_player);
    player->media_player = nullptr;
    player->renderer.reset();
    if (player->instance) player->api->release_instance(player->instance);
    player->instance = nullptr;
    delete player;
}

} // extern "C"
