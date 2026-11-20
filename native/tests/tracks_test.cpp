// SPDX-License-Identifier: LGPL-2.1-or-later

#include "kmediavlc_client.h"

#include <dlfcn.h>

#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <iostream>
#include <string>
#include <string_view>

namespace {
void require(bool condition, const char* message) {
    if (!condition) {
        std::cerr << message << '\n';
        std::exit(1);
    }
}

kmediavlc_track_info track_at(kmediavlc_player* player, kmediavlc_track_type type, size_t index) {
    kmediavlc_track_info info{};
    info.struct_size = sizeof(info);
    info.bridge_abi_version = KMEDIAVLC_BRIDGE_ABI_VERSION;
    require(kmediavlc_player_get_track(player, type, index, &info), "A captured track could not be read.");
    return info;
}

std::string selected_id(kmediavlc_player* player, kmediavlc_track_type type) {
    std::string selected;
    const size_t count = kmediavlc_player_track_count(player, type);
    for (size_t index = 0; index < count; ++index) {
        const auto info = track_at(player, type, index);
        if (info.selected) {
            require(selected.empty(), "Two tracks of one type were selected.");
            selected = info.id_utf8;
        }
    }
    return selected;
}
}

int main(int argc, char** argv) {
    require(argc == 2, "The fake libVLC path is required.");
    const auto path = std::filesystem::absolute(argv[1]);
    void* library = dlopen(path.c_str(), RTLD_NOW | RTLD_LOCAL);
    require(library != nullptr, "Cannot load the fake libVLC fixture.");
    const auto applied_subtitle_delay =
        reinterpret_cast<std::int64_t (*)()>(dlsym(library, "kmediavlc_fake_subtitle_delay"));
    require(applied_subtitle_delay != nullptr, "The fake subtitle delay probe is missing.");

    const std::string libvlc = path.string();
    const std::string plugins = path.parent_path().string();
    kmediavlc_player_config config{};
    config.struct_size = sizeof(config);
    config.bridge_abi_version = KMEDIAVLC_BRIDGE_ABI_VERSION;
    config.libvlc_path_utf8 = libvlc.c_str();
    config.plugin_directory_utf8 = plugins.c_str();
    config.delivery_mode = KMEDIAVLC_CPU_PULL;
    config.sdr_white_nits = config.display_peak_nits = 203.0F;
    auto* player = kmediavlc_player_create(&config);
    require(player != nullptr, "Player creation failed.");

    // Without an input there are no tracks and nothing to select.
    require(kmediavlc_player_track_count(player, KMEDIAVLC_TRACK_SUBTITLE) == 0, "Tracks without an input.");
    require(!kmediavlc_player_select_track(player, KMEDIAVLC_TRACK_SUBTITLE, "spu/0"),
        "A track without an input was selected.");

    // The subtitle delay is retained like the audio delay.
    require(kmediavlc_player_set_subtitle_delay(player, -2'500'000), "A subtitle delay before media was rejected.");
    require(applied_subtitle_delay() == 0, "The fixture accepted a delay without an input.");

    const auto opened_generation = kmediavlc_player_tracks_generation(player);
    require(kmediavlc_player_open(player, "file:///first.mkv", nullptr, 0, true), "Open failed.");
    require(kmediavlc_player_tracks_generation(player) != opened_generation, "Open kept the tracks generation.");
    require(applied_subtitle_delay() == -2'500'000, "The first media lost the subtitle delay.");

    // Listing.
    require(kmediavlc_player_track_count(player, KMEDIAVLC_TRACK_AUDIO) == 2, "Wrong audio track count.");
    const auto audio = track_at(player, KMEDIAVLC_TRACK_AUDIO, 0);
    require(audio.type == KMEDIAVLC_TRACK_AUDIO && std::string_view(audio.id_utf8) == "audio/0" &&
            std::string_view(audio.name_utf8) == "English 5.1" &&
            std::string_view(audio.language_utf8) == "en" && audio.description_utf8 == nullptr &&
            audio.audio_channels == 6 && audio.audio_sample_rate == 48'000 && audio.bitrate == 384'000 &&
            audio.codec_fourcc == 0x6134706DU && audio.selected && audio.id_stable,
        "The first audio track was copied wrongly.");
    require(kmediavlc_player_track_count(player, KMEDIAVLC_TRACK_SUBTITLE) == 3, "Wrong subtitle track count.");
    const auto unnamed = track_at(player, KMEDIAVLC_TRACK_SUBTITLE, 2);
    require(unnamed.name_utf8 == nullptr && unnamed.language_utf8 == nullptr && unnamed.audio_channels == 0,
        "Absent texts must stay NULL.");
    require(std::string_view(track_at(player, KMEDIAVLC_TRACK_SUBTITLE, 1).name_utf8) ==
            "Polski \xF0\x9F\x8E\xAC",
        "A UTF-8 name was not kept byte for byte.");
    kmediavlc_track_info out_of_range{};
    out_of_range.struct_size = sizeof(out_of_range);
    out_of_range.bridge_abi_version = KMEDIAVLC_BRIDGE_ABI_VERSION;
    require(!kmediavlc_player_get_track(player, KMEDIAVLC_TRACK_SUBTITLE, 3, &out_of_range),
        "An index past the capture was read.");
    kmediavlc_track_info wrong_size{};
    wrong_size.struct_size = sizeof(wrong_size) - 1U;
    wrong_size.bridge_abi_version = KMEDIAVLC_BRIDGE_ABI_VERSION;
    require(!kmediavlc_player_get_track(player, KMEDIAVLC_TRACK_SUBTITLE, 0, &wrong_size),
        "A track was written into a structure of another size.");
    require(kmediavlc_player_track_count(player, static_cast<kmediavlc_track_type>(9)) == 0,
        "An unknown track type was listed.");

    // Switching.
    const auto before_selection = kmediavlc_player_tracks_generation(player);
    require(kmediavlc_player_select_track(player, KMEDIAVLC_TRACK_SUBTITLE, "spu/1"), "Selecting spu/1 failed.");
    require(kmediavlc_player_tracks_generation(player) != before_selection, "Selection kept the tracks generation.");
    require(selected_id(player, KMEDIAVLC_TRACK_SUBTITLE) == "spu/1", "spu/1 is not selected.");
    require(kmediavlc_player_select_track(player, KMEDIAVLC_TRACK_AUDIO, "audio/1"), "Selecting audio/1 failed.");
    require(selected_id(player, KMEDIAVLC_TRACK_AUDIO) == "audio/1", "audio/1 is not selected.");
    require(!kmediavlc_player_select_track(player, KMEDIAVLC_TRACK_SUBTITLE, "spu/9"), "An unknown id was accepted.");
    require(!kmediavlc_player_select_track(player, KMEDIAVLC_TRACK_SUBTITLE, "spu/0,spu/1"), "A list was accepted.");
    require(!kmediavlc_player_select_track(player, KMEDIAVLC_TRACK_SUBTITLE, "audio/0"),
        "An id of another type was accepted.");
    require(selected_id(player, KMEDIAVLC_TRACK_SUBTITLE) == "spu/1", "A rejected id changed the selection.");

    // Subtitles off, kept across stop and replay of the same media.
    require(kmediavlc_player_select_track(player, KMEDIAVLC_TRACK_SUBTITLE, nullptr), "Subtitles off failed.");
    require(selected_id(player, KMEDIAVLC_TRACK_SUBTITLE).empty(), "Subtitles stayed on.");
    require(kmediavlc_player_stop(player) && kmediavlc_player_play(player), "Stop and replay failed.");
    require(selected_id(player, KMEDIAVLC_TRACK_SUBTITLE).empty(), "Replay turned subtitles back on.");
    require(applied_subtitle_delay() == -2'500'000, "Replay lost the subtitle delay.");

    // Changed while playing; a new media starts with libVLC's own choice but keeps the delay.
    require(kmediavlc_player_set_subtitle_delay(player, 1'000'000), "Changing the subtitle delay failed.");
    require(applied_subtitle_delay() == 1'000'000, "The running input did not get the new subtitle delay.");
    require(kmediavlc_player_open(player, "file:///second.mkv", nullptr, 0, true), "Replacement failed.");
    require(selected_id(player, KMEDIAVLC_TRACK_SUBTITLE) == "spu/0", "A new media kept the old selection.");
    require(applied_subtitle_delay() == 1'000'000, "The replacement lost the subtitle delay.");

    require(!kmediavlc_player_set_subtitle_delay(player, KMEDIAVLC_MAX_SUBTITLE_DELAY_MICROSECONDS + 1),
        "A subtitle delay above the limit was accepted.");
    require(!kmediavlc_player_set_subtitle_delay(player, -KMEDIAVLC_MAX_SUBTITLE_DELAY_MICROSECONDS - 1),
        "A subtitle advance above the limit was accepted.");
    require(applied_subtitle_delay() == 1'000'000, "A rejected request changed the subtitle delay.");
    require(kmediavlc_player_set_subtitle_delay(player, 0), "Clearing the subtitle delay failed.");
    require(applied_subtitle_delay() == 0, "The subtitle delay was not cleared.");

    require(kmediavlc_player_tracks_generation(nullptr) == 0, "A missing player reported a generation.");
    require(kmediavlc_player_track_count(nullptr, KMEDIAVLC_TRACK_AUDIO) == 0, "A missing player listed tracks.");
    require(!kmediavlc_player_select_track(nullptr, KMEDIAVLC_TRACK_AUDIO, nullptr), "A missing player selected.");

    kmediavlc_player_destroy(player);
    std::cout << "Tracks and subtitle delay PASS\n";
}
