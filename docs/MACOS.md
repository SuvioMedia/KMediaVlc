<!-- SPDX-License-Identifier: LGPL-2.1-or-later -->

# macOS bundled-runtime status

The Apple-silicon desktop runtime is included in multiplatform releases.
Its private libVLC package is built from pinned sources and checked for
relocation, dependency closure, and real IOSurface playback.

The current recipe pins `04d555a9d391f009d4f510f508fef58c39bdf810`.
Fresh automatic approval is retained in
`compliance/evidence/desktop-04d555a/acceptance.json`: 90 selected plugins,
three hosted native playback tests, and the same three tests on a local Mac.
An additional local HLS regression delays one segment by 25 seconds, verifies
midstream buffering with a paused media timestamp, and requires playback to
resume and reach the end. Its result is retained as `macos-aarch64/hls-rebuffer.json`.
Earlier evidence below remains scoped to its original source revision.

## Implemented

- exact pinned libVLC 4 OpenGL callback ABI;
- hardware CGL 3.2 core producer context;
- four IOSurface-backed render targets with bounded ownership;
- BGRA8/sRGB SDR and RGBA16F/linear-sRGB HDR storage contracts;
- producer flush before notification and generation-safe latest-frame delivery;
- `macos-aarch64` runtime selection and exact `OPENGL` manifest policy;
- a hermetic Java/JNI/callback/OpenGL/IOSurface integration test;
- a source-build recipe for pinned VLC commit `04d555a9` using the upstream Apple
  shared-library entry point and a `--disable-all` contrib profile;
- a 27-package resolved contrib graph with stream-output encoders, GPL, and
  GNUv3 packages disabled;
- a closed 90-plugin playback candidate selected from 289 built modules, including the
  adaptive demux required for direct HLS playback;
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

## Publication checks

1. Complete source/license and static-link review for the 90 selected modules
   and every contrib actually folded into them; produce the per-binary legal
   inventory, notices, corresponding-source archive, and relinking material.
2. Run the manually dispatched `macOS libVLC source audit` for the exact
   candidate commit and retain its path-free relocation report, contrib list,
   bound autotools-macro hashes, and three-test JUnit evidence. The build binds
   Homebrew gettext/iconv and pkgconf M4 providers into VLC's bootstrapped
   aclocal path and rebuilds from clean inputs. Pending inputs remain explicitly
   marked as audit candidates.
3. Retain the automatic source-license and hosted native playback evidence,
   bind its hashes to the exact policies and source recipe, and promote both
   desktop policy states to `approved`.
4. Rerun the source audit from that approved commit before adding its exact
   runtime and inventory to the publication matrix.

Physical-display, VideoToolbox, long-lifecycle, and application-specific Metal
acceptance remain separate hardware/product regressions. The automatic desktop
approval records its actual test scope and does not claim those results.

The ad-hoc signature used to validate the relocated candidate is not a release
signature. The consuming application must sign every nested Mach-O as part of
its normal hardened-runtime signing flow.

Until all publication checks pass, no new `macos-aarch64` resource is placed in the Maven
artifact. If a payload is missing, `VlcDesktopRuntime` returns its fail-closed
missing-payload result.
