// SPDX-License-Identifier: LGPL-2.1-or-later

#include <jni.h>

#include <android/native_window.h>
#include <android/native_window_jni.h>

#include <vlc/vlc.h>

#include <algorithm>
#include <atomic>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <iterator>
#include <limits>
#include <memory>
#include <mutex>
#include <new>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#ifndef KMEDIAVLC_ANDROID_ABI
#  define KMEDIAVLC_ANDROID_ABI "unknown"
#endif
#ifndef KMEDIAVLC_VLC_REVISION
#  define KMEDIAVLC_VLC_REVISION "unknown"
#endif

namespace {

constexpr jint kJniVersion = JNI_VERSION_1_6;
constexpr jint kBridgeAbiVersion = 1;
constexpr std::size_t kMaximumUtf8Bytes = 65'536;
constexpr std::size_t kMaximumHeaderPairs = 64;
constexpr unsigned kMaximumDimension = 16'384;
constexpr const char* kBuildMarker = "kmediavlc-android-anw-abi1";

enum PlaybackState : std::int64_t {
    kStateIdle = 0,
    kStateOpening = 1,
    kStateBuffering = 2,
    kStatePlaying = 3,
    kStatePaused = 4,
    kStateStopped = 5,
    kStateEnded = 6,
    kStateError = 7,
};

struct AndroidPlayer;

struct SurfaceBinding final {
    AndroidPlayer* player = nullptr;
    ANativeWindow* video = nullptr;
    ANativeWindow* subtitles = nullptr;

    ~SurfaceBinding() {
        if (subtitles != nullptr) ANativeWindow_release(subtitles);
        if (video != nullptr) ANativeWindow_release(video);
    }
};

struct AndroidPlayer final {
    libvlc_instance_t* instance = nullptr;
    libvlc_media_player_t* media_player = nullptr;
    libvlc_media_t* current_media = nullptr;
    libvlc_media_player_cbs callbacks{};
    int decode_mode = 0;
    bool output_callbacks_installed = false;
    int volume_percent = 100;
    float playback_rate = 1.0F;
    bool volume_pending = false;

    std::mutex surface_mutex;
    ANativeWindow* video_surface = nullptr;
    ANativeWindow* subtitle_surface = nullptr;
    int surface_width = 0;
    int surface_height = 0;

    std::atomic<std::int64_t> state{kStateIdle};
    std::atomic<std::int64_t> state_before_buffering{kStateIdle};
    std::atomic<std::uint64_t> media_generation{0};
    std::atomic<std::int64_t> position_microseconds{0};
    std::atomic<std::int64_t> duration_microseconds{0};
    std::atomic<unsigned> video_width{0};
    std::atomic<unsigned> video_height{0};
    std::atomic<unsigned> buffered_permille{0};
    std::atomic<bool> seekable{false};
    std::atomic<bool> loop{false};

    std::mutex error_mutex;
    std::string last_error;
};

AndroidPlayer* player_from(jlong handle) {
    return reinterpret_cast<AndroidPlayer*>(static_cast<std::uintptr_t>(handle));
}

jlong handle_from(AndroidPlayer* player) {
    return static_cast<jlong>(reinterpret_cast<std::uintptr_t>(player));
}

void set_error(AndroidPlayer* player, std::string message) {
    if (player == nullptr) return;
    std::lock_guard lock(player->error_mutex);
    player->last_error = std::move(message);
}

bool read_bytes(JNIEnv* environment, jbyteArray value, std::string& output, std::size_t maximum) {
    if (environment == nullptr || value == nullptr) return false;
    const jsize size = environment->GetArrayLength(value);
    if (size <= 0 || static_cast<std::size_t>(size) > maximum) return false;
    try {
        output.resize(static_cast<std::size_t>(size));
    } catch (const std::bad_alloc&) {
        return false;
    }
    environment->GetByteArrayRegion(value, 0, size, reinterpret_cast<jbyte*>(output.data()));
    return !environment->ExceptionCheck() && output.find('\0') == std::string::npos;
}

jbyteArray make_bytes(JNIEnv* environment, std::string_view value, std::size_t maximum) {
    if (environment == nullptr || value.empty() || value.size() > maximum ||
        value.size() > static_cast<std::size_t>(std::numeric_limits<jsize>::max())) {
        return nullptr;
    }
    auto* result = environment->NewByteArray(static_cast<jsize>(value.size()));
    if (result == nullptr) return nullptr;
    environment->SetByteArrayRegion(
        result,
        0,
        static_cast<jsize>(value.size()),
        reinterpret_cast<const jbyte*>(value.data()));
    return environment->ExceptionCheck() ? nullptr : result;
}

bool equals_ascii_case(std::string_view left, std::string_view right) {
    if (left.size() != right.size()) return false;
    for (std::size_t index = 0; index < left.size(); ++index) {
        const auto a = static_cast<unsigned char>(left[index]);
        const auto b = static_cast<unsigned char>(right[index]);
        if (std::tolower(a) != std::tolower(b)) return false;
    }
    return true;
}

bool header_option(std::string_view name, std::string_view value, std::string& option) {
    if (name.empty() || value.empty() || name.find(':') != std::string_view::npos ||
        value.find('\r') != std::string_view::npos || value.find('\n') != std::string_view::npos) {
        return false;
    }
    if (equals_ascii_case(name, "User-Agent")) {
        option = ":http-user-agent=";
    } else if (equals_ascii_case(name, "Referer")) {
        option = ":http-referrer=";
    } else if (equals_ascii_case(name, "Cookie")) {
        option = ":http-cookie=";
    } else {
        return false;
    }
    option.append(value);
    return true;
}

bool read_headers(JNIEnv* environment, jobjectArray pairs, std::vector<std::string>& values) {
    if (pairs == nullptr) return true;
    const jsize count = environment->GetArrayLength(pairs);
    if (count < 0 || count % 2 != 0 || static_cast<std::size_t>(count) > kMaximumHeaderPairs) {
        return false;
    }
    try {
        values.reserve(static_cast<std::size_t>(count));
    } catch (const std::bad_alloc&) {
        return false;
    }
    std::size_t total = 0;
    for (jsize index = 0; index < count; ++index) {
        auto* bytes = static_cast<jbyteArray>(environment->GetObjectArrayElement(pairs, index));
        if (environment->ExceptionCheck() || bytes == nullptr) return false;
        std::string value;
        const bool valid = read_bytes(environment, bytes, value, kMaximumUtf8Bytes);
        environment->DeleteLocalRef(bytes);
        if (!valid || total > kMaximumUtf8Bytes - value.size()) return false;
        total += value.size();
        values.push_back(std::move(value));
    }
    return true;
}

bool probable_path(std::string_view value) {
    if (value.empty() || value.front() == '/' || value.front() == '.') return true;
    const auto colon = value.find(':');
    if (colon == std::string_view::npos || colon == 0) return true;
    return std::isalpha(static_cast<unsigned char>(value.front())) == 0;
}

std::int64_t map_state(libvlc_state_t state) {
    switch (state) {
        case libvlc_NothingSpecial: return kStateIdle;
        case libvlc_Opening: return kStateOpening;
        case libvlc_Playing: return kStatePlaying;
        case libvlc_Paused: return kStatePaused;
        case libvlc_Stopped:
        case libvlc_Stopping: return kStateStopped;
        case libvlc_Error: return kStateError;
    }
    return kStateError;
}

void on_state_changed(void* opaque, libvlc_state_t state) {
    auto* player = static_cast<AndroidPlayer*>(opaque);
    if (player == nullptr) return;
    const auto mapped = map_state(state);
    if (mapped == kStateStopped) {
        const auto current = player->state.load(std::memory_order_acquire);
        // Preserve the semantic stop reason from on_media_stopping when VLC
        // follows it with a generic Stopping/Stopped notification.
        if (current == kStateEnded || current == kStateError) return;
    }
    player->state_before_buffering.store(mapped, std::memory_order_release);
    player->state.store(mapped, std::memory_order_release);
}

void on_buffering_changed(void* opaque, float buffering) {
    auto* player = static_cast<AndroidPlayer*>(opaque);
    if (player == nullptr || !std::isfinite(buffering)) return;
    const auto current = player->state.load(std::memory_order_acquire);
    // VLC may deliver a final buffering notification after on_media_stopping.
    // Never let that late progress event replace the semantic terminal state.
    if (current == kStateStopped || current == kStateEnded || current == kStateError) return;
    const float bounded = std::clamp(buffering, 0.0F, 1.0F);
    player->buffered_permille.store(
        static_cast<unsigned>(std::lround(bounded * 1000.0F)),
        std::memory_order_release);
    if (bounded < 1.0F) {
        if (current != kStateBuffering) {
            player->state_before_buffering.store(current, std::memory_order_release);
        }
        player->state.store(kStateBuffering, std::memory_order_release);
    } else if (player->state.load(std::memory_order_acquire) == kStateBuffering) {
        const auto previous = player->state_before_buffering.load(std::memory_order_acquire);
        player->state.store(
            previous == kStateBuffering ? kStatePlaying : previous,
            std::memory_order_release);
    }
}

void on_position_changed(void* opaque, libvlc_time_t time, double) {
    auto* player = static_cast<AndroidPlayer*>(opaque);
    if (player != nullptr) {
        player->position_microseconds.store(std::max<libvlc_time_t>(0, time), std::memory_order_release);
    }
}

void on_length_changed(void* opaque, libvlc_time_t length) {
    auto* player = static_cast<AndroidPlayer*>(opaque);
    if (player != nullptr) {
        player->duration_microseconds.store(std::max<libvlc_time_t>(0, length), std::memory_order_release);
    }
}

void on_media_stopping(void* opaque, libvlc_media_t*, libvlc_stopping_reason_t reason) {
    auto* player = static_cast<AndroidPlayer*>(opaque);
    if (player == nullptr) return;
    switch (reason) {
        case libvlc_stopping_reason_eos:
            player->state.store(kStateEnded, std::memory_order_release);
            break;
        case libvlc_stopping_reason_error:
            player->state.store(kStateError, std::memory_order_release);
            break;
        case libvlc_stopping_reason_user:
            player->state.store(kStateStopped, std::memory_order_release);
            break;
    }
}

bool setup_anw(
    void** opaque,
    const libvlc_video_setup_device_cfg_t*,
    libvlc_video_setup_device_info_t* output) {
    if (opaque == nullptr || *opaque == nullptr || output == nullptr) return false;
    auto* player = static_cast<AndroidPlayer*>(*opaque);
    auto binding = std::unique_ptr<SurfaceBinding>(new (std::nothrow) SurfaceBinding());
    if (!binding) return false;
    binding->player = player;
    {
        std::lock_guard lock(player->surface_mutex);
        if (player->video_surface == nullptr) return false;
        binding->video = player->video_surface;
        ANativeWindow_acquire(binding->video);
        if (player->subtitle_surface != nullptr) {
            binding->subtitles = player->subtitle_surface;
            ANativeWindow_acquire(binding->subtitles);
        }
    }
    std::memset(output, 0, sizeof(*output));
    *opaque = binding.release();
    return true;
}

void cleanup_anw(void* opaque) {
    delete static_cast<SurfaceBinding*>(opaque);
}

bool update_anw(
    void* opaque,
    const libvlc_video_render_cfg_t* configuration,
    libvlc_video_output_cfg_t* output) {
    auto* binding = static_cast<SurfaceBinding*>(opaque);
    if (binding == nullptr || binding->player == nullptr || binding->video == nullptr ||
        configuration == nullptr || output == nullptr) {
        return false;
    }
    const unsigned width = configuration->width <= kMaximumDimension ? configuration->width : 0;
    const unsigned height = configuration->height <= kMaximumDimension ? configuration->height : 0;
    binding->player->video_width.store(width, std::memory_order_release);
    binding->player->video_height.store(height, std::memory_order_release);
    std::memset(output, 0, sizeof(*output));
    output->u.anw.video = binding->video;
    output->u.anw.subtitle = binding->subtitles;
    return true;
}

bool install_anw_callbacks(AndroidPlayer* player) {
    return player != nullptr && player->media_player != nullptr &&
        libvlc_video_set_anw_callbacks(
            player->media_player,
            setup_anw,
            cleanup_anw,
            update_anw,
            player);
}

bool create_media_player(AndroidPlayer* player) {
    if (player == nullptr || player->instance == nullptr || player->media_player != nullptr) {
        return false;
    }
    player->media_player = libvlc_media_player_new(
        player->instance,
        &player->callbacks,
        player);
    if (player->media_player == nullptr) return false;
    if (install_anw_callbacks(player)) {
        player->output_callbacks_installed = true;
        return true;
    }
    libvlc_media_player_release(player->media_player);
    player->media_player = nullptr;
    return false;
}

void disable_output_callbacks(AndroidPlayer* player) {
    if (player == nullptr || player->media_player == nullptr) return;
    (void)libvlc_video_set_output_callbacks(
        player->media_player,
        libvlc_video_engine_disable,
        nullptr,
        nullptr,
        nullptr,
        nullptr,
        nullptr,
        nullptr,
        nullptr,
        nullptr,
        nullptr,
        nullptr);
}

struct TrackSelection final {
    bool known = false;
    std::string ids;
};

TrackSelection capture_selected_tracks(
    libvlc_media_player_t* media_player,
    libvlc_track_type_t type) {
    TrackSelection selection;
    if (media_player == nullptr) return selection;
    libvlc_media_tracklist_t* tracks =
        libvlc_media_player_get_tracklist(media_player, type, true);
    if (tracks == nullptr) return selection;
    selection.known = true;
    const std::size_t count = libvlc_media_tracklist_count(tracks);
    for (std::size_t index = 0; index < count; ++index) {
        const libvlc_media_track_t* track = libvlc_media_tracklist_at(tracks, index);
        if (track == nullptr || track->psz_id == nullptr || std::strchr(track->psz_id, ',') != nullptr) {
            selection.known = false;
            selection.ids.clear();
            break;
        }
        if (!selection.ids.empty()) selection.ids.push_back(',');
        selection.ids.append(track->psz_id);
    }
    libvlc_media_tracklist_delete(tracks);
    return selection;
}

void restore_selected_tracks(
    libvlc_media_player_t* media_player,
    libvlc_track_type_t type,
    const TrackSelection& selection) {
    if (media_player == nullptr || !selection.known) return;
    libvlc_media_player_select_tracks_by_ids(media_player, type, selection.ids.c_str());
}

void reset_video_format(AndroidPlayer* player) {
    if (player == nullptr) return;
    player->video_width.store(0, std::memory_order_release);
    player->video_height.store(0, std::memory_order_release);
}

bool recreate_media_player(AndroidPlayer* player) {
    if (player == nullptr || player->media_player == nullptr || player->current_media == nullptr) {
        return false;
    }

    const auto state = player->state.load(std::memory_order_acquire);
    const auto state_before_buffering =
        player->state_before_buffering.load(std::memory_order_acquire);
    const bool resume_playback =
        state == kStateOpening || state == kStateBuffering || state == kStatePlaying ||
        state == kStatePaused;
    const bool resume_paused = state == kStatePaused ||
        (state == kStateBuffering && state_before_buffering == kStatePaused);
    const libvlc_time_t reported_time = libvlc_media_player_get_time(player->media_player);
    const libvlc_time_t resume_time = reported_time >= 0
        ? reported_time
        : player->position_microseconds.load(std::memory_order_acquire);
    const TrackSelection selected_audio =
        capture_selected_tracks(player->media_player, libvlc_track_audio);
    const TrackSelection selected_video =
        capture_selected_tracks(player->media_player, libvlc_track_video);
    const TrackSelection selected_text =
        capture_selected_tracks(player->media_player, libvlc_track_text);

    libvlc_media_player_t* previous = std::exchange(player->media_player, nullptr);
    player->output_callbacks_installed = false;
    libvlc_media_player_release(previous);

    if (!create_media_player(player)) {
        player->state.store(kStateError, std::memory_order_release);
        set_error(player, "libVLC could not recreate Android video output for a new Surface.");
        return false;
    }

    libvlc_media_player_set_media(player->media_player, player->current_media);
    restore_selected_tracks(player->media_player, libvlc_track_audio, selected_audio);
    restore_selected_tracks(player->media_player, libvlc_track_video, selected_video);
    restore_selected_tracks(player->media_player, libvlc_track_text, selected_text);
    (void)libvlc_media_player_set_rate(player->media_player, player->playback_rate);
    player->volume_pending = true;
    if (!resume_playback) {
        player->state.store(kStateIdle, std::memory_order_release);
        return true;
    }
    if (libvlc_media_player_play(player->media_player) != 0) {
        player->state.store(kStateError, std::memory_order_release);
        set_error(player, "libVLC could not resume playback after replacing Android surfaces.");
        return false;
    }
    if (resume_time > 0) {
        (void)libvlc_media_player_set_time(player->media_player, resume_time, false);
    }
    if (resume_paused) libvlc_media_player_set_pause(player->media_player, 1);
    if (libvlc_audio_set_volume(player->media_player, player->volume_percent) == 0) {
        player->volume_pending = false;
    }
    return true;
}

bool replace_surfaces(
    JNIEnv* environment,
    AndroidPlayer* player,
    jobject video,
    jobject subtitles,
    jint width,
    jint height) {
    if (player == nullptr || environment == nullptr) return false;
    if (video == nullptr && subtitles != nullptr) return false;
    if ((video == nullptr) != (width == 0 && height == 0)) return false;
    if (video != nullptr && (width <= 0 || height <= 0 || width > static_cast<jint>(kMaximumDimension) ||
        height > static_cast<jint>(kMaximumDimension))) {
        return false;
    }

    ANativeWindow* next_video =
        video == nullptr ? nullptr : ANativeWindow_fromSurface(environment, video);
    if (video != nullptr && (next_video == nullptr || environment->ExceptionCheck())) return false;
    ANativeWindow* next_subtitles =
        subtitles == nullptr ? nullptr : ANativeWindow_fromSurface(environment, subtitles);
    if (subtitles != nullptr && (next_subtitles == nullptr || environment->ExceptionCheck())) {
        if (next_video != nullptr) ANativeWindow_release(next_video);
        return false;
    }

    {
        std::lock_guard lock(player->surface_mutex);
        if (player->video_surface == next_video &&
            player->subtitle_surface == next_subtitles &&
            player->surface_width == width && player->surface_height == height) {
            if (next_subtitles != nullptr) ANativeWindow_release(next_subtitles);
            if (next_video != nullptr) ANativeWindow_release(next_video);
            return true;
        }
    }

    ANativeWindow* previous_video = nullptr;
    ANativeWindow* previous_subtitles = nullptr;
    int previous_width = 0;
    int previous_height = 0;
    {
        std::lock_guard lock(player->surface_mutex);
        previous_video = std::exchange(player->video_surface, next_video);
        previous_subtitles = std::exchange(player->subtitle_surface, next_subtitles);
        previous_width = player->surface_width;
        previous_height = player->surface_height;
        player->surface_width = width;
        player->surface_height = height;
    }
    reset_video_format(player);

    const bool recreating = next_video != nullptr && player->current_media != nullptr;
    const bool installed = recreating
        ? recreate_media_player(player)
        : player->output_callbacks_installed || install_anw_callbacks(player);
    if (installed) {
        player->output_callbacks_installed = true;
        if (previous_subtitles != nullptr) ANativeWindow_release(previous_subtitles);
        if (previous_video != nullptr) ANativeWindow_release(previous_video);
        return true;
    }

    ANativeWindow* rejected_video = nullptr;
    ANativeWindow* rejected_subtitles = nullptr;
    {
        std::lock_guard lock(player->surface_mutex);
        rejected_video = std::exchange(player->video_surface, previous_video);
        rejected_subtitles = std::exchange(player->subtitle_surface, previous_subtitles);
        player->surface_width = previous_width;
        player->surface_height = previous_height;
    }
    if (rejected_subtitles != nullptr) ANativeWindow_release(rejected_subtitles);
    if (rejected_video != nullptr) ANativeWindow_release(rejected_video);
    if (!recreating) {
        set_error(player, "The pinned libVLC runtime rejected ANativeWindow callbacks.");
    }
    return false;
}

bool valid_player(AndroidPlayer* player) {
    return player != nullptr && player->instance != nullptr && player->media_player != nullptr;
}

}  // namespace

extern "C" JNIEXPORT jint JNICALL JNI_OnLoad(JavaVM*, void*) {
    return kJniVersion;
}

extern "C" JNIEXPORT jlong JNICALL
Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_create(
    JNIEnv*,
    jclass,
    jint decode_mode) {
    if (decode_mode != 0 && decode_mode != 1) return 0;
    auto player = std::unique_ptr<AndroidPlayer>(new (std::nothrow) AndroidPlayer());
    if (!player) return 0;
    player->decode_mode = decode_mode;

    const char* arguments[] = {
        "--no-video-title-show",
        "--no-stats",
        "--keystore=memory",
        "--quiet",
    };
    player->instance = libvlc_new(static_cast<int>(std::size(arguments)), arguments);
    if (player->instance == nullptr) return 0;

    player->callbacks.version = 0;
    player->callbacks.on_media_stopping = on_media_stopping;
    player->callbacks.on_state_changed = on_state_changed;
    player->callbacks.on_buffering_changed = on_buffering_changed;
    player->callbacks.on_position_changed = on_position_changed;
    player->callbacks.on_length_changed = on_length_changed;
    if (!create_media_player(player.get())) {
        libvlc_release(player->instance);
        player->instance = nullptr;
        return 0;
    }
    return handle_from(player.release());
}

extern "C" JNIEXPORT void JNICALL
Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_destroy(
    JNIEnv*,
    jclass,
    jlong handle) {
    std::unique_ptr<AndroidPlayer> player(player_from(handle));
    if (!player) return;

    ANativeWindow* video = nullptr;
    ANativeWindow* subtitles = nullptr;
    {
        std::lock_guard lock(player->surface_mutex);
        video = std::exchange(player->video_surface, nullptr);
        subtitles = std::exchange(player->subtitle_surface, nullptr);
        player->surface_width = 0;
        player->surface_height = 0;
    }
    disable_output_callbacks(player.get());
    player->output_callbacks_installed = false;
    if (player->media_player != nullptr) {
        libvlc_media_player_release(player->media_player);
        player->media_player = nullptr;
    }
    if (player->current_media != nullptr) {
        libvlc_media_release(player->current_media);
        player->current_media = nullptr;
    }
    if (subtitles != nullptr) ANativeWindow_release(subtitles);
    if (video != nullptr) ANativeWindow_release(video);
    if (player->instance != nullptr) {
        libvlc_release(player->instance);
        player->instance = nullptr;
    }
}

extern "C" JNIEXPORT jboolean JNICALL
Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_setSurfaces(
    JNIEnv* environment,
    jclass,
    jlong handle,
    jobject video,
    jobject subtitles,
    jint width,
    jint height) {
    return replace_surfaces(
        environment,
        player_from(handle),
        video,
        subtitles,
        width,
        height) ? JNI_TRUE : JNI_FALSE;
}

extern "C" JNIEXPORT jboolean JNICALL
Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_open(
    JNIEnv* environment,
    jclass,
    jlong handle,
    jbyteArray location_bytes,
    jobjectArray header_pairs,
    jboolean autoplay) {
    auto* player = player_from(handle);
    if (!valid_player(player)) return JNI_FALSE;
    std::string location;
    std::vector<std::string> headers;
    if (!read_bytes(environment, location_bytes, location, kMaximumUtf8Bytes) ||
        !read_headers(environment, header_pairs, headers)) {
        set_error(player, "The media location or HTTP headers escaped the JNI boundary.");
        return JNI_FALSE;
    }

    libvlc_media_t* media = probable_path(location)
        ? libvlc_media_new_path(location.c_str())
        : libvlc_media_new_location(location.c_str());
    if (media == nullptr) {
        set_error(player, "libVLC could not create the requested media.");
        return JNI_FALSE;
    }

    bool valid = true;
    for (std::size_t index = 0; index < headers.size(); index += 2) {
        std::string option;
        if (!header_option(headers[index], headers[index + 1], option)) {
            valid = false;
            break;
        }
        libvlc_media_add_option(media, option.c_str());
    }
    if (valid && player->decode_mode == 1) libvlc_media_add_option(media, ":no-hw-dec");
    if (valid && player->loop.load(std::memory_order_acquire)) {
        libvlc_media_add_option(media, ":input-repeat=65535");
    }
    if (!valid) {
        libvlc_media_release(media);
        set_error(player, "An HTTP header is unsupported by the pinned libVLC API.");
        return JNI_FALSE;
    }

    libvlc_media_player_set_media(player->media_player, media);
    libvlc_media_t* previous_media = std::exchange(player->current_media, media);
    if (previous_media != nullptr) libvlc_media_release(previous_media);
    player->media_generation.fetch_add(1, std::memory_order_acq_rel);
    player->position_microseconds.store(0, std::memory_order_release);
    player->duration_microseconds.store(0, std::memory_order_release);
    reset_video_format(player);
    player->buffered_permille.store(0, std::memory_order_release);
    player->seekable.store(false, std::memory_order_release);
    player->state.store(kStateIdle, std::memory_order_release);
    if (autoplay == JNI_FALSE || libvlc_media_player_play(player->media_player) == 0) return JNI_TRUE;
    set_error(player, "libVLC rejected autoplay.");
    return JNI_FALSE;
}

extern "C" JNIEXPORT jboolean JNICALL
Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_play(
    JNIEnv*, jclass, jlong handle) {
    auto* player = player_from(handle);
    if (!valid_player(player)) return JNI_FALSE;
    if (libvlc_media_player_play(player->media_player) == 0) {
        if (player->volume_pending &&
            libvlc_audio_set_volume(player->media_player, player->volume_percent) == 0) {
            player->volume_pending = false;
        }
        return JNI_TRUE;
    }
    set_error(player, "libVLC rejected play.");
    return JNI_FALSE;
}

extern "C" JNIEXPORT jboolean JNICALL
Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_pause(
    JNIEnv*, jclass, jlong handle) {
    auto* player = player_from(handle);
    if (!valid_player(player)) return JNI_FALSE;
    libvlc_media_player_set_pause(player->media_player, 1);
    return JNI_TRUE;
}

extern "C" JNIEXPORT jboolean JNICALL
Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_stop(
    JNIEnv*, jclass, jlong handle) {
    auto* player = player_from(handle);
    if (!valid_player(player)) return JNI_FALSE;
    const auto current = player->state.load(std::memory_order_acquire);
    if (current == kStateEnded || current == kStateStopped) {
        player->state_before_buffering.store(kStateStopped, std::memory_order_release);
        player->state.store(kStateStopped, std::memory_order_release);
        return JNI_TRUE;
    }
    if (libvlc_media_player_stop_async(player->media_player) == 0) return JNI_TRUE;
    set_error(player, "libVLC rejected stop.");
    return JNI_FALSE;
}

extern "C" JNIEXPORT jboolean JNICALL
Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_seek(
    JNIEnv*, jclass, jlong handle, jlong time_microseconds, jboolean fast) {
    auto* player = player_from(handle);
    if (!valid_player(player) || time_microseconds < 0) return JNI_FALSE;
    if (libvlc_media_player_set_time(
            player->media_player,
            static_cast<libvlc_time_t>(time_microseconds),
            fast == JNI_TRUE) == 0) {
        return JNI_TRUE;
    }
    set_error(player, "libVLC rejected seek.");
    return JNI_FALSE;
}

extern "C" JNIEXPORT jboolean JNICALL
Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_setVolume(
    JNIEnv*, jclass, jlong handle, jfloat volume) {
    auto* player = player_from(handle);
    if (!valid_player(player) || !std::isfinite(volume)) return JNI_FALSE;
    const int percent = static_cast<int>(std::lround(std::clamp(volume, 0.0F, 1.0F) * 100.0F));
    if (libvlc_audio_set_volume(player->media_player, percent) == 0) {
        player->volume_percent = percent;
        player->volume_pending = false;
        return JNI_TRUE;
    }
    set_error(player, "libVLC rejected volume.");
    return JNI_FALSE;
}

extern "C" JNIEXPORT jboolean JNICALL
Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_setRate(
    JNIEnv*, jclass, jlong handle, jfloat rate) {
    auto* player = player_from(handle);
    if (!valid_player(player) || !std::isfinite(rate) || rate <= 0.0F) return JNI_FALSE;
    if (libvlc_media_player_set_rate(player->media_player, rate) == 0) {
        player->playback_rate = rate;
        return JNI_TRUE;
    }
    set_error(player, "libVLC rejected playback rate.");
    return JNI_FALSE;
}

extern "C" JNIEXPORT jboolean JNICALL
Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_setLoop(
    JNIEnv*, jclass, jlong handle, jboolean loop) {
    auto* player = player_from(handle);
    if (!valid_player(player)) return JNI_FALSE;
    player->loop.store(loop == JNI_TRUE, std::memory_order_release);
    return JNI_TRUE;
}

extern "C" JNIEXPORT jlongArray JNICALL
Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_snapshot(
    JNIEnv* environment, jclass, jlong handle) {
    auto* player = player_from(handle);
    if (!valid_player(player)) return nullptr;
    if (player->volume_pending &&
        libvlc_audio_set_volume(player->media_player, player->volume_percent) == 0) {
        player->volume_pending = false;
    }
    const auto position = libvlc_media_player_get_time(player->media_player);
    const auto duration = libvlc_media_player_get_length(player->media_player);
    if (position >= 0) player->position_microseconds.store(position, std::memory_order_release);
    if (duration >= 0) player->duration_microseconds.store(duration, std::memory_order_release);
    player->seekable.store(
        libvlc_media_player_is_seekable(player->media_player),
        std::memory_order_release);
    const jlong values[] = {
        static_cast<jlong>(player->state.load(std::memory_order_acquire)),
        static_cast<jlong>(player->media_generation.load(std::memory_order_acquire)),
        static_cast<jlong>(player->position_microseconds.load(std::memory_order_acquire)),
        static_cast<jlong>(player->duration_microseconds.load(std::memory_order_acquire)),
        static_cast<jlong>(player->video_width.load(std::memory_order_acquire)),
        static_cast<jlong>(player->video_height.load(std::memory_order_acquire)),
        static_cast<jlong>(player->buffered_permille.load(std::memory_order_acquire)),
        player->seekable.load(std::memory_order_acquire) ? 1 : 0,
    };
    auto* result = environment->NewLongArray(static_cast<jsize>(std::size(values)));
    if (result == nullptr) return nullptr;
    environment->SetLongArrayRegion(result, 0, static_cast<jsize>(std::size(values)), values);
    return environment->ExceptionCheck() ? nullptr : result;
}

extern "C" JNIEXPORT jbyteArray JNICALL
Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_lastErrorUtf8(
    JNIEnv* environment, jclass, jlong handle) {
    auto* player = player_from(handle);
    if (player == nullptr) return nullptr;
    std::string error;
    {
        std::lock_guard lock(player->error_mutex);
        error = player->last_error;
    }
    return make_bytes(environment, error, 4096);
}

extern "C" JNIEXPORT jint JNICALL
Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_bridgeAbiVersion(
    JNIEnv*, jclass) {
    return kBridgeAbiVersion;
}

extern "C" JNIEXPORT jbyteArray JNICALL
Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_nativeAbiUtf8(
    JNIEnv* environment, jclass) {
    return make_bytes(environment, KMEDIAVLC_ANDROID_ABI, 32);
}

extern "C" JNIEXPORT jbyteArray JNICALL
Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_vlcVersionUtf8(
    JNIEnv* environment, jclass) {
    const char* value = libvlc_get_version();
    return value == nullptr ? nullptr : make_bytes(environment, value, 128);
}

extern "C" JNIEXPORT jbyteArray JNICALL
Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_vlcChangesetUtf8(
    JNIEnv* environment, jclass) {
    const char* value = libvlc_get_changeset();
    return value == nullptr ? nullptr : make_bytes(environment, value, 256);
}

extern "C" JNIEXPORT jbyteArray JNICALL
Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_vlcRevisionUtf8(
    JNIEnv* environment, jclass) {
    return make_bytes(environment, KMEDIAVLC_VLC_REVISION, 64);
}

extern "C" JNIEXPORT jbyteArray JNICALL
Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_buildMarkerUtf8(
    JNIEnv* environment, jclass) {
    return make_bytes(environment, kBuildMarker, 64);
}
