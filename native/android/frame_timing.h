// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once

#include <array>
#include <cstdint>
#include <limits>
#include <mutex>

namespace kmediavlc {

// The producer's scheduled system timestamp is only a lookup key, never media PTS.
class FrameTiming final {
public:
    static constexpr std::int64_t unavailable = std::numeric_limits<std::int64_t>::min();

    void record(std::uint64_t generation, std::int64_t pts_us, std::int64_t producer_ns) {
        if (generation == 0 || producer_ns <= 0 || pts_us == unavailable) return;
        std::lock_guard lock(mutex_);
        values_[next_] = {generation, producer_ns, pts_us};
        next_ = (next_ + 1) % values_.size();
    }

    std::int64_t lookup(std::uint64_t generation, std::int64_t producer_ns) {
        if (generation == 0 || producer_ns <= 0) return unavailable;
        std::lock_guard lock(mutex_);
        for (std::size_t offset = 0; offset < values_.size(); ++offset) {
            const auto& value = values_[(next_ + values_.size() - offset - 1) % values_.size()];
            if (value.generation == generation && value.producer_ns == producer_ns) return value.pts_us;
        }
        return unavailable;
    }

private:
    struct Value { std::uint64_t generation = 0; std::int64_t producer_ns = 0; std::int64_t pts_us = 0; };
    std::array<Value, 128> values_{};
    std::size_t next_ = 0;
    std::mutex mutex_;
};

} // namespace kmediavlc
