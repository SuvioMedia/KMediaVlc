// SPDX-License-Identifier: LGPL-2.1-or-later

#include "bridge_internal.hpp"

#include <cstdlib>
#include <iostream>

namespace {
void require(bool condition, const char* message) {
    if (!condition) {
        std::cerr << message << '\n';
        std::exit(1);
    }
}

void begin_seek(kmediavlc_player& player) {
    player.play_when_ready.store(false);
    player.transport_generation.store(7);
    player.continuous_frame_delivery = false;
    player.paused_frame_budget = 1;
    player.paused_seek_target_microseconds = 70000000;
    player.paused_seek_start_microseconds = 10000000;
    player.paused_seek_repause_pending = true;
    player.paused_seek_transport_generation = 7;
    player.video_width.store(1920);
    player.paused_seek_candidate_frame.reset();
    player.pending_frame.reset();
}
}

int main(int argc, char** argv) {
    require(argc == 2, "The fake libVLC path is required.");
    std::string error;
    kmediavlc_player player;
    player.api = kmediavlc::LibVlcApi::load(argv[1], error);
    require(player.api != nullptr, "Cannot load the fake libVLC fixture.");
    player.instance = player.api->new_instance(0, nullptr);
    player.media_player = player.api->media_player_new(player.instance, nullptr, nullptr);
    begin_seek(player);

    // Reproduce a clock callback arriving before the first decoded frame.
    kmediavlc::publish_paused_seek_candidate_if_ready(&player, 70000000);
    require(player.paused_seek_repause_pending, "Clock-only completion must keep decoding.");
    require(player.paused_frame_budget == 1, "Clock-only completion consumed the frame budget.");
    require(!player.pending_frame, "Clock-only completion published a nonexistent frame.");

    auto frame = std::make_unique<kmediavlc_frame>();
    frame->info.acquire_fence = -1;
    frame->info.output_generation = 12;
    kmediavlc::publish_frame(&player, std::move(frame));
    require(!player.pending_frame, "Seek candidate must wait for the target clock.");
    kmediavlc::publish_paused_seek_candidate_if_ready(&player, 70000000);
    require(player.pending_frame != nullptr, "The target frame was not delivered.");
    require(player.pending_frame->info.pts_microseconds == 70000000, "Wrong target timestamp.");
    require(!player.paused_seek_repause_pending && player.paused_frame_budget == 0,
        "Completed seek must stop decoding.");

    // A newer public play command must win over an old seek completion.
    begin_seek(player);
    frame = std::make_unique<kmediavlc_frame>();
    frame->info.acquire_fence = -1;
    kmediavlc::publish_frame(&player, std::move(frame));
    player.transport_generation.store(8);
    kmediavlc::publish_paused_seek_candidate_if_ready(&player, 70000000);
    require(!player.pending_frame, "A superseded seek published a stale frame.");

    // An unavailable output cannot provide a frame, so stop at the target clock.
    begin_seek(player);
    player.delivery_mode = KMEDIAVLC_GPU_PUSH;
    kmediavlc::publish_paused_seek_candidate_if_ready(&player, 70000000);
    require(!player.paused_seek_repause_pending, "An unavailable output left decoding running.");
    require(!player.pending_frame, "An unavailable output published a frame.");
    player.api->media_player_release(player.media_player);
    player.api->release_instance(player.instance);
}
