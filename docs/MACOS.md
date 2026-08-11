<!-- SPDX-License-Identifier: LGPL-2.1-or-later -->

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
- a hermetic Java/JNI/callback/OpenGL/IOSurface integration test;
- a source build of pinned VLC commit `b5536cde` using the upstream Apple
  shared-library entry point and a `--disable-all` contrib profile;
- a 22-package resolved contrib graph with stream-output encoders, GPL, and
  GNUv3 packages disabled;
- a closed 89-plugin playback candidate selected from 285 built modules;
- application-private `@loader_path` relocation, arm64/macOS 14 validation,
  system-only Mach-O dependency validation, ad-hoc re-signing, and plugin-cache
  generation after relocation;
- real pinned-libVLC CPU_PULL playback and OpenGL-to-IOSurface playback after
  copying the 47 MiB candidate to a different extraction path;
- two consecutive IOSurface generations and sizes, explicit stop, frame
  release, and clean player teardown.

The hermetic test runs on the standard Apple-silicon `macos-15` GitHub-hosted
runner as part of normal CI; it covers both SDR and HDR surface allocation.

The bridge depends only on Apple system frameworks (`CoreFoundation`,
`CoreVideo`, `IOSurface`, and `OpenGL`) plus `libc++` and `libSystem`. libVLC is
still loaded explicitly from the verified extracted runtime.

The source-built test currently proves SDR transport with a generated PNG. It
does not prove HDR playback: the pinned upstream `vgl` implementation requests
an 8-bit BT.709/sRGB render configuration. The fake-libVLC HDR fixture verifies
KMediaVlc's RGBA16F IOSurface ownership contract only, not end-to-end VLC HDR
rendering.

## Publication gates still open

1. Complete source/license and static-link review for the 89 selected modules
   and every contrib actually folded into them; produce the per-binary legal
   inventory, notices, corresponding-source archive, and relinking material.
2. Run the manually dispatched `macOS libVLC source audit` for the exact
   candidate commit and retain its path-free relocation report, contrib list,
   bound autotools-macro hashes, and two-test JUnit evidence. The build binds
   Homebrew gettext/iconv and pkgconf M4 providers into VLC's bootstrapped
   aclocal path, rebuilds from clean inputs, and never uploads the
   still-unapproved native payload.
3. Run real pinned-VLC MKV/MP4, audio, subtitles, VideoToolbox, HTTPS, seek,
   long lifecycle, and device replacement tests on macOS hardware.
4. Resolve and test the pinned `vgl` HDR limitation before claiming HDR10 or
   HLG support from the real runtime.
5. Import the IOSurface in KMediaPlayer's Metal TextureView path and retain the
   frame until command-buffer completion.
6. Accept SDR and HDR output on physical displays before adding macOS to the
   release workflow and publication matrix.

The ad-hoc signature used to validate the relocated candidate is not a release
signature. The consuming application must sign every nested Mach-O as part of
its normal hardened-runtime signing flow.

Until all gates pass, no `macos-aarch64` resource is placed in the Maven
artifact. `VlcDesktopRuntime` recognizes the target but returns a fail-closed
missing-payload result.
