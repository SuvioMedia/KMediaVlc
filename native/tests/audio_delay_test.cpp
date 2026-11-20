// SPDX-License-Identifier: LGPL-2.1-or-later

#include "kmediavlc_client.h"

#include <dlfcn.h>

#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <string>

namespace {
void require(bool condition, const char* message) {
    if (!condition) {
        std::cerr << message << '\n';
        std::exit(1);
    }
}
}

int main(int argc, char** argv) {
    require(argc == 2, "The fake libVLC path is required.");
    const auto path = std::filesystem::absolute(argv[1]);
    void* library = dlopen(path.c_str(), RTLD_NOW | RTLD_LOCAL);
    require(library != nullptr, "Cannot load the fake libVLC fixture.");
    const auto applied_delay =
        reinterpret_cast<std::int64_t (*)()>(dlsym(library, "kmediavlc_fake_audio_delay"));
    const auto requests =
        reinterpret_cast<unsigned (*)()>(dlsym(library, "kmediavlc_fake_audio_delay_set_calls"));
    require(applied_delay != nullptr && requests != nullptr, "The fake audio delay probes are missing.");

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

    // libVLC has no input yet and drops the request; the bridge must keep it.
    require(kmediavlc_player_set_audio_delay(player, 250'000), "A delay before the first media was rejected.");
    require(applied_delay() == 0, "The fixture accepted a delay without an input.");

    // An idle player gets its input in open, before playback starts.
    require(kmediavlc_player_open(player, "file:///first.mkv", nullptr, 0, false), "Open failed.");
    require(applied_delay() == 250'000, "The first media did not receive the retained delay.");
    require(kmediavlc_player_play(player), "Play failed.");
    require(applied_delay() == 250'000, "Play lost the delay.");

    // A state callback for an input that already has the delay must not resend it.
    const unsigned settled = requests();
    require(kmediavlc_player_pause(player) && kmediavlc_player_play(player), "Pause and resume failed.");
    kmediavlc_player_snapshot snapshot{};
    snapshot.struct_size = sizeof(snapshot);
    snapshot.bridge_abi_version = KMEDIAVLC_BRIDGE_ABI_VERSION;
    require(kmediavlc_player_get_snapshot(player, &snapshot), "Snapshot failed.");
    require(requests() == settled, "An applied delay was sent to libVLC again.");

    // Replacing a running media resets libVLC; its new input appears only when it starts.
    require(kmediavlc_player_open(player, "file:///second.mkv", nullptr, 0, true), "Replacement failed.");
    require(applied_delay() == 250'000, "The replacement media did not receive the retained delay.");

    // Sound earlier than the picture, changed while playing.
    require(kmediavlc_player_set_audio_delay(player, -100'000), "A negative delay was rejected.");
    require(applied_delay() == -100'000, "The running input did not receive the new delay.");

    // Stop releases the input; replay creates another one.
    require(kmediavlc_player_stop(player), "Stop failed.");
    require(applied_delay() == 0, "The fixture kept a delay without an input.");
    require(kmediavlc_player_play(player), "Replay failed.");
    require(applied_delay() == -100'000, "Replay after stop lost the delay.");

    // A media replaced without autoplay gets its input when it is started.
    require(kmediavlc_player_open(player, "file:///third.mkv", nullptr, 0, false), "Paused replacement failed.");
    require(kmediavlc_player_play(player), "Play after a paused replacement failed.");
    require(applied_delay() == -100'000, "A paused replacement lost the delay.");

    require(!kmediavlc_player_set_audio_delay(player, KMEDIAVLC_MAX_AUDIO_DELAY_MICROSECONDS + 1),
        "A delay above the limit was accepted.");
    require(!kmediavlc_player_set_audio_delay(player, -KMEDIAVLC_MAX_AUDIO_DELAY_MICROSECONDS - 1),
        "An advance above the limit was accepted.");
    require(!kmediavlc_player_set_audio_delay(nullptr, 0), "A missing player was accepted.");
    require(applied_delay() == -100'000, "A rejected request changed the delay.");
    require(kmediavlc_player_set_audio_delay(player, KMEDIAVLC_MAX_AUDIO_DELAY_MICROSECONDS),
        "The largest delay was rejected.");
    require(applied_delay() == KMEDIAVLC_MAX_AUDIO_DELAY_MICROSECONDS, "The largest delay was not applied.");

    require(kmediavlc_player_set_audio_delay(player, 0), "Clearing the delay was rejected.");
    require(applied_delay() == 0, "The delay was not cleared.");

    kmediavlc_player_destroy(player);
    std::cout << "Audio delay across media PASS\n";
}
