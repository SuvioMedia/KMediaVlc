<!-- SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary -->

# macOS bundled-runtime status

The Apple-silicon desktop transport is implemented but is not a published
native payload yet. This distinction is deliberate: a working renderer does
not by itself prove that a complete libVLC distribution is reproducible,
license-closed, relocatable, and accepted by the real Metal consumer.

## Implemented

- exact pinned libVLC 4 OpenGL callback ABI;
- hardware CGL 3.2 core producer context;
- four IOSurface-backed render targets with bounded ownership;
- BGRA8/sRGB SDR and RGBA16F/linear-sRGB HDR storage contracts;
- producer flush before notification and generation-safe latest-frame delivery;
- `macos-aarch64` runtime selection and exact `OPENGL` manifest policy;
- a hermetic Java/JNI/callback/OpenGL/IOSurface integration test.

The hermetic test runs on the standard Apple-silicon `macos-15` GitHub-hosted
runner as part of normal CI; it covers both SDR and HDR surface allocation.

The bridge depends only on Apple system frameworks (`CoreFoundation`,
`CoreVideo`, `IOSurface`, and `OpenGL`) plus `libc++` and `libSystem`. libVLC is
still loaded explicitly from the verified extracted runtime.

## Publication gates still open

1. Build the pinned VLC commit from source for `macos-aarch64` with shared
   libVLC libraries and the reviewed playback-only plugin set.
2. Record every Mach-O dependency, install name, rpath, plugin, component,
   license, source archive, and corresponding-source input in a closed
   inventory.
3. Reject absolute/non-system dependencies and prove relocation from the
   extracted application-private runtime directory.
4. Run real pinned-VLC HTTPS, seek, lifecycle, SDR, HDR, resize, and device
   replacement tests on macOS hardware.
5. Import the IOSurface in KMediaPlayer's Metal TextureView path and retain the
   frame until command-buffer completion.
6. Accept SDR and HDR output on physical displays before adding macOS to the
   release workflow and publication matrix.

Until all gates pass, no `macos-aarch64` resource is placed in the Maven
artifact. `VlcDesktopRuntime` recognizes the target but returns a fail-closed
missing-payload result.
