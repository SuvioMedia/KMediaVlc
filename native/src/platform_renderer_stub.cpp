// SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

#include "bridge_internal.hpp"

namespace kmediavlc {
namespace {

class UnsupportedPlatformRenderer final : public PlatformRenderer {
public:
    bool install(libvlc_media_player_t*, std::string& error) override {
        error = "GPU push is not implemented by this KMediaVlc platform payload.";
        return false;
    }
    void uninstall(libvlc_media_player_t*) noexcept override {}
    bool output_target_changed(const OutputTargetSnapshot&, std::string& error) override {
        error = "GPU push is not implemented by this KMediaVlc platform payload.";
        return false;
    }
    bool resize(std::uint32_t, std::uint32_t) override { return false; }
};

} // namespace

std::unique_ptr<PlatformRenderer> create_platform_renderer(kmediavlc_player*) {
    return std::make_unique<UnsupportedPlatformRenderer>();
}

} // namespace kmediavlc
