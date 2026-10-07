// SPDX-License-Identifier: LGPL-2.1-or-later
#include "kmediavlc_client.h"
#include <windows.h>
#include <cstdlib>
#include <filesystem>
#include <iostream>

static void require(bool condition, const char* message) {
    if (!condition) { std::cerr << message << '\n'; std::exit(1); }
}

int main(int argc, char** argv) {
    require(argc == 2, "Expected fake libVLC path");
    const auto path = std::filesystem::absolute(argv[1]);
    const auto library = LoadLibraryW(path.c_str());
    require(library != nullptr, "Unable to load fake libVLC");
    const auto observed_mode = reinterpret_cast<int (*)()>(
        GetProcAddress(library, "kmediavlc_fake_last_scaling_mode"));
    require(observed_mode != nullptr, "Missing option probe");
    const auto libvlc = path.string();
    const auto plugins = path.parent_path().string();
    kmediavlc_player_config config{};
    config.struct_size = sizeof(config);
    config.bridge_abi_version = KMEDIAVLC_BRIDGE_ABI_VERSION;
    config.libvlc_path_utf8 = libvlc.c_str();
    config.plugin_directory_utf8 = plugins.c_str();
    config.delivery_mode = KMEDIAVLC_GPU_PUSH;
    config.sdr_white_nits = config.display_peak_nits = 203.0f;
    // The fake captures libvlc_new arguments, not per-media options. A renderer
    // outside the input object's hierarchy would otherwise silently use linear.
    for (int mode = 0; mode <= 4; ++mode) {
        auto* player = kmediavlc_player_create_with_video_scaling(&config, mode);
        require(player != nullptr, "Creation failed");
        require(observed_mode() == mode, "Resampler did not reach libVLC instance");
        require(kmediavlc_player_set_video_scaling_mode(player, mode), "Created mode not acknowledged");
        require(!kmediavlc_player_set_video_scaling_mode(player, (mode + 1) % 5),
            "Changed mode was falsely acknowledged");
        kmediavlc_player_destroy(player);
    }
    require(kmediavlc_player_create_with_video_scaling(&config, 5) == nullptr, "Invalid mode accepted");
    auto* original = kmediavlc_player_create(&config);
    require(original != nullptr && observed_mode() == 0, "Original creation API changed");
    kmediavlc_player_destroy(original);
    FreeLibrary(library);
    std::cout << "Windows instance scaling options PASS\n";
}
