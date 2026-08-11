<!-- SPDX-License-Identifier: LGPL-2.1-or-later -->

# KMediaVlc

KMediaVlc is the optional, auditable libVLC 4 runtime used by the
`composemediaplayer-libvlc` KMediaPlayer backend. It is deliberately separate
from KMediaPlayer so applications that do not select libVLC do not download
or package VLC binaries.

```kotlin
dependencies {
    implementation("io.github.shusek:kmedia-vlc-runtime-desktop:0.1.0-rc.1")
}
```

The Android coordinate is reserved as
`io.github.shusek:kmedia-vlc-runtime-android`; it is not published until the
Android gates below are complete.

The runtime artifact contains a pinned libVLC 4 build, its allowlisted
plugins and dependencies, and a stable KMediaVlc bridge. It never downloads
native code at runtime. Native payloads are release inputs and are never
committed to this repository.

## License and private-consumer boundary

KMediaVlc's project-authored runtime clients, native bridges, build recipes,
and packaging tools are licensed under LGPL-2.1-or-later. The stable
`native/include/kmediavlc_client.h` ABI is ISC so an independent application or
adapter can use the runtime without copying its implementation. VideoLAN and
all bundled dependencies retain their upstream licenses.

KMediaPlayer is a separate consumer. Making KMediaPlayer private neither
changes this repository's LGPL terms nor removes a recipient's rights to the
KMediaVlc source, notices, replacement mechanism, and release-bound relinking
materials. Published KMediaVlc artifacts therefore remain usable by the
private KMediaPlayer adapter without moving runtime implementation code into
that repository.

`scripts/build_vlc_windows.sh` wraps VideoLAN's own pinned Windows build in
release, UCRT, headless, GPL-disabled mode. It permits the reviewed LGPLv3
dependencies required for HTTPS/TLS and intentionally does not consume
prebuilt contrib archives. Its output is still not publishable until
every selected DLL and plugin passes the per-file inventory packager.
The Windows binaries are compiled by the pinned LLVM/MinGW UCRT cross-toolchain;
Wine is used only for Meson's tiny cross-executable sanity probe. The resulting
DLLs and the MSVC-built KMediaVlc bridge are then loaded and integration-tested
on a native `windows-2022` runner. A hosted runner without a physical HDR
display does not replace the required hardware HDR test.

## Frame transport

The bridge uses a push-notify/pull-acquire protocol:

1. VLC renders on its own thread and publishes a new serial/generation.
2. The bridge replaces the pending frame and releases any skipped buffer.
3. A non-owning notification wakes the single consumer.
4. KMediaPlayer pulls the latest frame and owns it until `release`.

GPU frames carry texture handles plus acquire/release synchronization. CPU
frames remain an explicit SDR compatibility path. The producer thread never
calls Compose and never waits for a UI render.

## Runtime policy

- ABI major is pinned to libVLC 4.
- The published bundled matrix remains Windows-only: VLC renders bounded BT.2020/PQ
  into a 16-bit D3D11 intermediate and the bridge converts it on-GPU to a
  shared `R16G16B16A16_FLOAT` linear-sRGB TextureView frame.
- The Apple-silicon macOS bridge now renders through libVLC 4 OpenGL callbacks
  into a bounded four-buffer IOSurface pool. Its runtime target and packaging
  contract plus a relocatable source-built VLC candidate are implemented, but
  publication remains fail-closed until the static component/license inventory,
  Metal-consumer integration, and hardware SDR/HDR evidence are audited.
- iOS 16.2 arm64 device and Apple-silicon simulator source-build recipes now
  produce shared libVLC 4 candidates. The stable C bridge excludes JNI, starts
  with fail-closed CPU pull, and packages an 84-plugin allowlist as 87 dynamic
  frameworks per slice. Both source-built slices pass relocation audit, and the
  repository now contains deterministic XCFramework/CocoaPods assembly plus an
  independent archive verifier. Real simulator playback through the packaged
  graph and paired archive verification pass; iOS remains unpublished until
  device playback passes and the full binary and license audits close.
- Linux x86-64/AArch64 now has a source-built 85-plugin candidate and a real
  GLES2/GBM producer backed by a bounded four-buffer DMA-BUF pool. It negotiates
  concrete ABGR8888 modifiers and exact acquire/release sync-file ownership.
  Both hosted architectures pass source build, closed ELF/cache staging, and
  real CPU-frame playback, but remain release-ineligible until physical
  render-node, fence, normal consumer, and VR-projection acceptance plus the
  binary/license audit pass.
- Android has a two-ABI AAR and direct libVLC 4 ANativeWindow bridge. Its
  pinned source recipe now completes real ARM64 and ARMv7 builds and emits an
  exact static-link audit plus a hash-bound legal-evidence bundle inside the
  candidate AAR. Exact NDK r29/LLVM source revisions are recorded and a
  deterministic Git-object-verified NDK source packager is exercised. A separate
  complete corresponding-source packager closes the exact KMediaVlc, VLC, and
  libvlcjni trees, all 55 contrib tarballs, the NDK supplement, and both ABI
  audits. Real ARM64/API 35 instrumented tests cover MediaCodec and software
  decode, subtitle composition, repeated Surface replacement, seek, stop, and
  destruction. The candidate remains deliberately release-ineligible until its
  fail-closed physical-device harness passes and its conservative SPDX sets and
  final release-bound source artifacts are reviewed and closed.
- CPU pull is available for controlled SDR and diagnostics.
- A payload is rejected if it includes GPL/nonfree or uninventoryed modules.

Official VideoLAN nightlies may be used for local API experiments only. They
are not release inputs because their complete plugin license graph is not the
KMediaVlc allowlisted graph.

## Checks

```shell
bash gradlew check complianceCheck
python scripts/verify_source_compliance.py --root .
```

Publishing additionally requires all of
`kmediaVlcNativeStagingDirectory`, `kmediaVlcNativeInventory`,
`kmediaVlcNativeTarget`, `kmediaVlcSourceOffer`, and the immutable
`recipeRevision`, plus `correspondingSourceArchive`. The recipe revision is
embedded in the native manifest and must match the checked-out KMediaVlc
commit. The Gradle publication consumes only the packager's verified output;
an arbitrary directory cannot bypass the component/license inventory.

Android publication additionally requires the deterministic NDK source archive, exact
`llvm-project` and `llvm_android` checkouts, and the same `recipeRevision`; Gradle independently
reconstructs and verifies every selected Git object before attaching that archive. It also
requires the independently verified Android corresponding-source archive, both upstream Git
checkouts, all audited contrib tarballs, the real legal manifest, and both ABI link audits.

The portable CPU-pull implementation and Android ANativeWindow API exist for
controlled integration work, but the published native payload matrix remains
Windows-only until each platform-specific publication gate is complete.

See `docs/ARCHITECTURE.md`, `docs/ANDROID.md`, `docs/FRAME-TRANSPORT.md`,
`docs/MACOS.md`, `docs/IOS.md`, `docs/LINUX.md`, and `docs/LICENSING.md`.
Release setup and the exact four GitHub secrets are
documented in `docs/RELEASING.md` and `docs/MAVEN-CENTRAL.md`.
