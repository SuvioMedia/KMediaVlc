// SPDX-License-Identifier: LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.desktop;

import java.nio.file.Path;
import java.util.Objects;

/** Verified paths for one coherent libVLC 4 runtime and its stable bridge. */
public record VlcDesktopRuntimeResolution(
        Path bridgePath,
        Path libVlcPath,
        Path pluginDirectory,
        String runtimeId,
        VlcRuntimeCapabilities capabilities) {

    public VlcDesktopRuntimeResolution {
        bridgePath = requireAbsolute(bridgePath, "bridgePath");
        libVlcPath = requireAbsolute(libVlcPath, "libVlcPath");
        pluginDirectory = requireAbsolute(pluginDirectory, "pluginDirectory");
        if (runtimeId == null || runtimeId.isBlank()) {
            throw new IllegalArgumentException("runtimeId must not be blank.");
        }
        Objects.requireNonNull(capabilities, "capabilities");
    }

    private static Path requireAbsolute(Path path, String name) {
        Objects.requireNonNull(path, name);
        Path normalized = path.toAbsolutePath().normalize();
        if (!path.isAbsolute()) {
            throw new IllegalArgumentException(name + " must be absolute.");
        }
        return normalized;
    }

    @Override
    public String toString() {
        return "VlcDesktopRuntimeResolution[verified=true,runtimeId=" + runtimeId + "]";
    }
}
