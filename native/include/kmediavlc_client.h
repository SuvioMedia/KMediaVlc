/* SPDX-License-Identifier: ISC */

#ifndef KMEDIAVLC_CLIENT_H
#define KMEDIAVLC_CLIENT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#if defined(_WIN32)
#  if defined(KMEDIAVLC_BUILDING_BRIDGE)
#    define KMEDIAVLC_API __declspec(dllexport)
#  else
#    define KMEDIAVLC_API __declspec(dllimport)
#  endif
#else
#  define KMEDIAVLC_API __attribute__((visibility("default")))
#endif

#define KMEDIAVLC_BRIDGE_ABI_VERSION 2u
#define KMEDIAVLC_MAX_AUDIO_DELAY_MICROSECONDS INT64_C(60000000)
#define KMEDIAVLC_MAX_SUBTITLE_DELAY_MICROSECONDS INT64_C(600000000)

typedef struct kmediavlc_player kmediavlc_player;
typedef struct kmediavlc_frame kmediavlc_frame;

typedef enum kmediavlc_delivery_mode {
    KMEDIAVLC_GPU_PUSH = 1,
    KMEDIAVLC_CPU_PULL = 2,
} kmediavlc_delivery_mode;

typedef enum kmediavlc_pixel_format {
    KMEDIAVLC_RGBA8_SRGB = 1,
    KMEDIAVLC_RGBA16F_LINEAR_SRGB = 2,
} kmediavlc_pixel_format;

typedef enum kmediavlc_source_dynamic_range {
    KMEDIAVLC_SOURCE_DYNAMIC_RANGE_UNKNOWN = 0,
    KMEDIAVLC_SOURCE_DYNAMIC_RANGE_SDR = 1,
    KMEDIAVLC_SOURCE_DYNAMIC_RANGE_HDR10 = 2,
    KMEDIAVLC_SOURCE_DYNAMIC_RANGE_HLG = 3,
} kmediavlc_source_dynamic_range;

typedef enum kmediavlc_platform_handle_type {
    KMEDIAVLC_CPU_ADDRESS = 1,
    KMEDIAVLC_D3D11_SHARED_HANDLE = 2,
    KMEDIAVLC_IOSURFACE_ID = 3,
    KMEDIAVLC_DMABUF = 4,
} kmediavlc_platform_handle_type;

typedef enum kmediavlc_playback_state {
    KMEDIAVLC_STATE_IDLE = 0,
    KMEDIAVLC_STATE_OPENING = 1,
    KMEDIAVLC_STATE_BUFFERING = 2,
    KMEDIAVLC_STATE_PLAYING = 3,
    KMEDIAVLC_STATE_PAUSED = 4,
    KMEDIAVLC_STATE_STOPPED = 5,
    KMEDIAVLC_STATE_ENDED = 6,
    KMEDIAVLC_STATE_ERROR = 7,
} kmediavlc_playback_state;

typedef enum kmediavlc_output_target_type {
    KMEDIAVLC_OUTPUT_UNAVAILABLE = 0,
    KMEDIAVLC_OUTPUT_WINDOWS_D3D11 = 1,
    KMEDIAVLC_OUTPUT_MACOS_IOSURFACE = 2,
    KMEDIAVLC_OUTPUT_LINUX_DMABUF = 3,
} kmediavlc_output_target_type;

typedef struct kmediavlc_drm_format_modifier {
    uint32_t format;
    uint64_t modifier;
} kmediavlc_drm_format_modifier;

typedef struct kmediavlc_output_target {
    uint32_t struct_size;
    uint32_t bridge_abi_version;
    kmediavlc_output_target_type type;
    uint64_t generation;
    uint32_t width;
    uint32_t height;
    bool request_hdr;
    float sdr_white_nits;
    float display_peak_nits;
    uint64_t adapter_luid;
    uintptr_t metal_device;
    uintptr_t metal_command_queue;
    const char *render_node_utf8;
    const kmediavlc_drm_format_modifier *drm_formats;
    size_t drm_format_count;
    bool acquire_fences;
    bool release_fences;
} kmediavlc_output_target;

typedef struct kmediavlc_frame_info {
    uint32_t struct_size;
    uint32_t bridge_abi_version;
    uint64_t serial;
    uint64_t output_generation;
    int64_t pts_microseconds;
    uint32_t width;
    uint32_t height;
    kmediavlc_pixel_format pixel_format;
    kmediavlc_source_dynamic_range source_dynamic_range;
    kmediavlc_platform_handle_type handle_type;
    /* The frame retains a DMA-BUF platform_handle until frame_release(). */
    uintptr_t platform_handle;
    /* Ownership of a non-negative sync-file descriptor transfers to the caller. */
    intptr_t acquire_fence;
    uint32_t stride;
    uint32_t fourcc;
    uint32_t offset;
    uint64_t modifier;
    uint64_t cpu_byte_count;
    float sdr_white_nits;
    float content_peak_nits;
    bool premultiplied_alpha;
} kmediavlc_frame_info;

typedef void (*kmediavlc_frame_available_cb)(
    void *opaque,
    uint64_t serial,
    uint64_t output_generation);

typedef void (*kmediavlc_playback_state_cb)(
    void *opaque,
    kmediavlc_playback_state state,
    uint64_t media_generation);

typedef struct kmediavlc_player_snapshot {
    uint32_t struct_size;
    uint32_t bridge_abi_version;
    kmediavlc_playback_state state;
    uint64_t media_generation;
    int64_t position_microseconds;
    int64_t duration_microseconds;
    /* Selected source display geometry after sample-aspect ratio and orientation. */
    uint32_t video_width;
    uint32_t video_height;
    uint32_t video_frame_rate_num;
    uint32_t video_frame_rate_den;
    uint32_t buffered_permille;
    bool seekable;
} kmediavlc_player_snapshot;

typedef enum kmediavlc_track_type {
    KMEDIAVLC_TRACK_AUDIO = 1,
    KMEDIAVLC_TRACK_SUBTITLE = 2,
} kmediavlc_track_type;

/* One audio or subtitle track of the current input. Strings are UTF-8 and owned by the bridge. */
typedef struct kmediavlc_track_info {
    uint32_t struct_size;
    uint32_t bridge_abi_version;
    kmediavlc_track_type type;
    /* libVLC's string id, for example "audio/1" or "spu/3". Never NULL. */
    const char *id_utf8;
    /* Display name chosen by libVLC, language code and description; NULL when absent. */
    const char *name_utf8;
    const char *language_utf8;
    const char *description_utf8;
    /* VLC fourcc of the codec (vlc_fourcc_t byte order), 0 when unknown. */
    uint32_t codec_fourcc;
    /* Audio only; 0 for subtitles or when unknown. */
    uint32_t audio_channels;
    uint32_t audio_sample_rate;
    uint32_t bitrate;
    bool selected;
    /* True when the id names the same track in another playback of the same media. */
    bool id_stable;
} kmediavlc_track_info;

typedef struct kmediavlc_player_config {
    uint32_t struct_size;
    uint32_t bridge_abi_version;
    const char *libvlc_path_utf8;
    const char *plugin_directory_utf8;
    kmediavlc_delivery_mode delivery_mode;
    bool request_hdr;
    float sdr_white_nits;
    float display_peak_nits;
    kmediavlc_frame_available_cb frame_available;
    kmediavlc_playback_state_cb playback_state_changed;
    void *callback_opaque;
} kmediavlc_player_config;

/* Creates a player without loading a child NSView, HWND, GTK widget, or subsurface. */
KMEDIAVLC_API kmediavlc_player *kmediavlc_player_create(const kmediavlc_player_config *config);

/* Windows GPU resampler, fixed for this player's lifetime: 0 default, 1 linear,
 * 2 point, 3 video processor, 4 vendor super resolution. Other platforms accept 0.
 * Separate entry point preserves the original config structure and bridge ABI. */
KMEDIAVLC_API kmediavlc_player *kmediavlc_player_create_with_video_scaling(
    const kmediavlc_player_config *config, int mode);

/* All strings are UTF-8. Headers are alternating name/value entries. */
KMEDIAVLC_API bool kmediavlc_player_open(
    kmediavlc_player *player,
    const char *uri_utf8,
    const char *const *headers_utf8,
    size_t header_entry_count,
    bool autoplay);

KMEDIAVLC_API bool kmediavlc_player_play(kmediavlc_player *player);
KMEDIAVLC_API bool kmediavlc_player_pause(kmediavlc_player *player);
KMEDIAVLC_API bool kmediavlc_player_stop(kmediavlc_player *player);
KMEDIAVLC_API bool kmediavlc_player_seek(kmediavlc_player *player, int64_t time_microseconds, bool fast);
/* Software volume as a linear gain: 0.0 = mute, 1.0 = nominal (100%), up to 2.0
 * (200%, boost). Values outside 0.0..2.0 are clamped; NaN/infinity returns false.
 * Values above 1.0 amplify the signal and the output may clip. Ignored by
 * passthrough/bitstream output, where the stream is not decoded to PCM. */
KMEDIAVLC_API bool kmediavlc_player_set_volume(kmediavlc_player *player, float volume);
KMEDIAVLC_API bool kmediavlc_player_set_rate(kmediavlc_player *player, float rate);
/*
 * Moves the sound against the picture: a positive delay plays the sound later, a negative one
 * earlier. The bridge keeps the value for this player and gives it to every media it opens,
 * because libVLC itself returns to zero whenever the media changes. Returns false outside
 * -KMEDIAVLC_MAX_AUDIO_DELAY_MICROSECONDS..KMEDIAVLC_MAX_AUDIO_DELAY_MICROSECONDS.
 * Additive entry point: it keeps the bridge ABI version.
 */
KMEDIAVLC_API bool kmediavlc_player_set_audio_delay(
    kmediavlc_player *player,
    int64_t delay_microseconds);
/*
 * Moves the subtitles against the picture: a positive delay shows them later, a negative one
 * earlier (libVLC's sign). Kept and re-applied for every media like the audio delay. Returns false
 * outside -KMEDIAVLC_MAX_SUBTITLE_DELAY_MICROSECONDS..KMEDIAVLC_MAX_SUBTITLE_DELAY_MICROSECONDS.
 * Additive entry point: it keeps the bridge ABI version.
 */
KMEDIAVLC_API bool kmediavlc_player_set_subtitle_delay(
    kmediavlc_player *player,
    int64_t delay_microseconds);
/*
 * Changes whenever libVLC adds, removes, updates, selects or unselects a track, and when a media
 * is opened. Clients poll it and read the tracks again only after it changed.
 */
KMEDIAVLC_API uint64_t kmediavlc_player_tracks_generation(kmediavlc_player *player);
/*
 * Captures the tracks of one type of the current input and returns how many there are; 0 without
 * an input. The capture replaces the previous one of the same type, so strings returned by
 * kmediavlc_player_get_track for that type stay valid only until the next count call for the
 * type, or until the player is destroyed.
 */
KMEDIAVLC_API size_t kmediavlc_player_track_count(kmediavlc_player *player, kmediavlc_track_type type);
/* Reads one track of the last capture. The caller sets struct_size and bridge_abi_version. */
KMEDIAVLC_API bool kmediavlc_player_get_track(
    kmediavlc_player *player,
    kmediavlc_track_type type,
    size_t index,
    kmediavlc_track_info *out_track);
/*
 * Selects one track of the current input by its id; NULL or "" turns every track of the type off
 * (subtitles off). libVLC keeps the choice for this media, also across stop and replay; opening
 * another media starts with libVLC's own choice. Returns false for an id the current input does
 * not have, leaving the selection unchanged.
 */
KMEDIAVLC_API bool kmediavlc_player_select_track(
    kmediavlc_player *player,
    kmediavlc_track_type type,
    const char *id_utf8);
KMEDIAVLC_API bool kmediavlc_player_set_loop(kmediavlc_player *player, bool loop);
/* Windows D3D11 only. Confirms the mode chosen at creation; returns false for
 * a changed mode, which requires create_with_video_scaling(). */
KMEDIAVLC_API bool kmediavlc_player_set_video_scaling_mode(kmediavlc_player *player, int mode);
/* Low byte: available option bits. High byte: vendor (0 unknown, 1 NVIDIA, 2 Intel, 3 AMD). */
KMEDIAVLC_API int kmediavlc_player_video_scaling_capabilities(kmediavlc_player *player);

/* Updates the producer target and the output luminance negotiation. */
KMEDIAVLC_API bool kmediavlc_player_resize(kmediavlc_player *player, uint32_t width, uint32_t height);
KMEDIAVLC_API bool kmediavlc_player_update_output(
    kmediavlc_player *player,
    const kmediavlc_output_target *target);

KMEDIAVLC_API bool kmediavlc_player_get_snapshot(
    kmediavlc_player *player,
    kmediavlc_player_snapshot *out_snapshot);

/* Returns bridge-owned diagnostics. The pointer stays valid until the next call on this player. */
KMEDIAVLC_API const char *kmediavlc_player_last_error(kmediavlc_player *player);

/*
 * Pulls and transfers ownership of the newest frame; skipped frames are released internally.
 * A returned acquire_fence is caller-owned and must be consumed or closed exactly once.
 */
KMEDIAVLC_API kmediavlc_frame *kmediavlc_player_acquire_latest_frame(
    kmediavlc_player *player,
    kmediavlc_frame_info *out_info);

/*
 * Releases one acquired frame and transfers ownership of the consumer completion fence when
 * available. The bridge always closes or retains a supplied non-negative descriptor.
 */
KMEDIAVLC_API void kmediavlc_frame_release(kmediavlc_frame *frame, intptr_t release_fence);

/* Returns the CPU buffer only for KMEDIAVLC_CPU_ADDRESS frames. */
KMEDIAVLC_API const void *kmediavlc_frame_cpu_pixels(kmediavlc_frame *frame, size_t *out_byte_count);

/* Invalidates callbacks, releases pending frames, then destroys the player. */
KMEDIAVLC_API void kmediavlc_player_destroy(kmediavlc_player *player);

#ifdef __cplusplus
}
#endif

#endif
