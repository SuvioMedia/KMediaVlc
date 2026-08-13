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
- a source build of pinned VLC commit `e4396920` using the upstream Apple
  shared-library entry point and a `--disable-all` contrib profile;
- a 27-package resolved contrib graph with stream-output encoders, GPL, and
  GNUv3 packages disabled;
- a closed 89-plugin playback candidate selected from 289 built modules;
- application-private `@loader_path` relocation, arm64/macOS 14 validation,
  system-only Mach-O dependency validation, ad-hoc re-signing, and plugin-cache
  generation after relocation;
- real pinned-libVLC CPU_PULL playback and OpenGL-to-IOSurface playback after
  copying the closed candidate to a different extraction path and hiding the
  original VLC install tree;
- two consecutive IOSurface generations and sizes, explicit stop, frame
  release, and clean player teardown;
- real HEVC Main 10 HDR10 playback from the pinned libVLC source build, with
  source-aware BT.2020/PQ metadata and 10-bit depth reaching an
  RGBA16F/linear-sRGB IOSurface;
- KMediaPlayer Metal consumption through Nucleus Tao
  `2.4.0-kmp-hdr.2`, including retention of the same-process IOSurface until
  command-buffer completion;
- physical presentation on an HDR-capable AW3423DWF display: Nucleus reported
  `actual=HDR`, `RGBA16_FLOAT_SCRGB`, headroom `2.327`, and `PRESENTED`; the
  45.017-second libVLC run rendered 1078 frames with zero drops at 23.947 fps
  for a 23.929 fps source.

The hermetic test runs on the standard Apple-silicon `macos-15` GitHub-hosted
runner as part of normal CI; it covers both SDR and HDR surface allocation.
That runner exposes `Apple Software Renderer`, so the real-libVLC source audit
explicitly enables the committed VLC patch's software-OpenGL sampler fallback.
The option is disabled by default. The hosted run still exercises the pinned
`vgl` compositor and real IOSurface generations, but it is not treated as
hardware-renderer or physical-display evidence.

The bridge depends only on Apple system frameworks (`CoreFoundation`,
`CoreVideo`, `IOSurface`, and `OpenGL`) plus `libc++` and `libSystem`. libVLC is
still loaded explicitly from the verified extracted runtime.

The source patch extends the pinned upstream `vgl` callback path with the
decoded source's bit depth, range, primaries, transfer function, and color
space. HDR10 and HLG therefore request the FP16 callback output instead of the
upstream 8-bit BT.709/sRGB default. The real HDR10 integration test is retained
alongside the hermetic fake-libVLC ownership test so a metadata-only or
allocation-only regression cannot satisfy the gate.

## Publication gates still open

1. Complete source/license and static-link review for the 89 selected modules
   and every contrib actually folded into them; produce the per-binary legal
   inventory, notices, corresponding-source archive, and relinking material.
2. Run the manually dispatched `macOS libVLC source audit` for the exact
   candidate commit and retain its path-free relocation report, contrib list,
   bound autotools-macro hashes, and three-test JUnit evidence. The build binds
   Homebrew gettext/iconv and pkgconf M4 providers into VLC's bootstrapped
   aclocal path, rebuilds from clean inputs, and never uploads the
   still-unapproved native payload.
3. Run the remaining real pinned-VLC MKV/MP4, audio, subtitles, VideoToolbox,
   HTTPS, seek, long-lifecycle, and display-replacement regressions on macOS
   hardware.
4. Retain commit-bound SDR and HDR physical-display evidence from the final
   release candidate and promote the two reviewed policy states to `approved`.
5. Rerun the source audit from that approved commit before adding its exact
   runtime and inventory to the publication matrix.

The ad-hoc signature used to validate the relocated candidate is not a release
signature. The consuming application must sign every nested Mach-O as part of
its normal hardened-runtime signing flow.

Until all gates pass, no `macos-aarch64` resource is placed in the Maven
artifact. `VlcDesktopRuntime` recognizes the target but returns a fail-closed
missing-payload result.
