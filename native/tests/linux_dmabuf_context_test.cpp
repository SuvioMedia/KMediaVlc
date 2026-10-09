// SPDX-License-Identifier: LGPL-2.1-or-later

#include "linux_dmabuf_inspector.hpp"

#include <cstdlib>
#include <iostream>

int main() {
    const char* render_node = std::getenv("KMEDIA_TEST_RENDER_NODE");
    if (render_node == nullptr || render_node[0] == '\0') {
        std::cout << "Set KMEDIA_TEST_RENDER_NODE to run the physical GBM/EGL probe.\n";
        return 77;
    }
    const auto modifiers = kmediavlc::linux_dmabuf_consumer_modifiers(render_node);
    if (modifiers.empty()) {
        std::cerr << "The requested DRM node could not create a DMA-BUF consumer context.\n";
        return 1;
    }
    std::cout << "GBM/EGL consumer supports " << modifiers.size()
              << " concrete ABGR8888 modifiers on " << render_node << '\n';
    return 0;
}
