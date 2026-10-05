// SPDX-License-Identifier: LGPL-2.1-or-later
#include "../frame_timing.h"
#include <cassert>
#include <thread>

int main() {
    kmediavlc::FrameTiming timing;
    assert(timing.lookup(1, 1000) == kmediavlc::FrameTiming::unavailable);
    timing.record(1, -42'000, 10'000'000'000);
    assert(timing.lookup(1, 10'000'000'000) == -42'000);
    assert(timing.lookup(2, 10'000'000'000) == kmediavlc::FrameTiming::unavailable);
    // Same system key in another source must not return the prior source's PTS.
    timing.record(2, 22'000'000, 10'000'000'000);
    assert(timing.lookup(2, 10'000'000'000) == 22'000'000);
    assert(timing.lookup(1, 10'000'000'000) == -42'000);
    // Bounded retention: an acquired-but-retired buffer fails closed instead of borrowing newer PTS.
    for (int i = 0; i < 129; ++i) timing.record(2, i * 41'667, 20'000'000'000 + i);
    assert(timing.lookup(2, 10'000'000'000) == kmediavlc::FrameTiming::unavailable);
    assert(timing.lookup(2, 20'000'000'128) == 128 * 41'667);
    std::thread producer([&] { for (int i = 0; i < 10'000; ++i) timing.record(3, i, 30'000'000'000 + i); });
    for (int i = 0; i < 10'000; ++i) {
        const auto pts = timing.lookup(3, 30'000'000'000 + i);
        assert(pts == i || pts == kmediavlc::FrameTiming::unavailable);
    }
    producer.join();
}
