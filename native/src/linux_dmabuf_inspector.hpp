// SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

#ifndef KMEDIAVLC_LINUX_DMABUF_INSPECTOR_HPP
#define KMEDIAVLC_LINUX_DMABUF_INSPECTOR_HPP

#include <array>
#include <cstdint>
#include <string>
#include <vector>

namespace kmediavlc {

struct LinuxDmaBufInspection final {
    int release_fence_fd = -1;
    std::array<std::uint8_t, 4> rgba{};
};

std::vector<std::uint64_t> linux_dmabuf_consumer_modifiers(
    const std::string& render_node);

// Always consumes acquire_fence_fd. On success, the caller owns the returned
// release fence and must transfer or close it exactly once.
bool inspect_linux_dmabuf_frame(
    const std::string& render_node,
    int dma_buf_fd,
    int acquire_fence_fd,
    std::uint32_t width,
    std::uint32_t height,
    std::uint32_t stride,
    std::uint32_t fourcc,
    std::uint32_t offset,
    std::uint64_t modifier,
    LinuxDmaBufInspection& output);

} // namespace kmediavlc

#endif
