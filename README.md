<!-- SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary -->

# KMediaVlc

KMediaVlc is the optional, auditable libVLC 4 runtime used by the
`composemediaplayer-libvlc` KMediaPlayer backend. It is deliberately separate
from KMediaPlayer so applications that do not select libVLC do not download
or package VLC binaries.

```kotlin
dependencies {
    implementation("io.github.shusek:kmedia-vlc-runtime-desktop:0.1.0")
}
```

The runtime artifact contains a pinned libVLC 4 build, its allowlisted
plugins and dependencies, and a stable KMediaVlc bridge. It never downloads
native code at runtime. Native payloads are release inputs and are never
committed to this repository.

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
- The first bundled release is Windows-only: VLC renders bounded BT.2020/PQ
  into a 16-bit D3D11 intermediate and the bridge converts it on-GPU to a
  shared `R16G16B16A16_FLOAT` linear-sRGB TextureView frame.
- macOS and Linux GPU producers are deliberately release-ineligible until
  IOSurface and DMA-BUF import, fences, and native integration tests exist.
- CPU pull is available for controlled SDR and diagnostics.
- A payload is rejected if it includes GPL/nonfree or uninventoryed modules.

Official VideoLAN nightlies may be used for local API experiments only. They
are not release inputs because their complete plugin license graph is not the
KMediaVlc allowlisted graph.

## Checks

```shell
./gradlew check complianceCheck
python scripts/verify_source_compliance.py --root .
```

Publishing additionally requires all of
`kmediaVlcNativeStagingDirectory`, `kmediaVlcNativeInventory`,
`kmediaVlcNativeTarget`, `kmediaVlcSourceOffer`, and the immutable
`recipeRevision`, plus `correspondingSourceArchive`. The recipe revision is
embedded in the native manifest and must match the checked-out KMediaVlc
commit. The Gradle publication consumes only the packager's verified output;
an arbitrary directory cannot bypass the component/license inventory.

The portable CPU-pull implementation exists for controlled tests, but the
published native payload matrix remains Windows-only in this version.

See `docs/ARCHITECTURE.md`, `docs/FRAME-TRANSPORT.md`, and
`docs/LICENSING.md`. Release setup and the exact four GitHub secrets are
documented in `docs/RELEASING.md` and `docs/MAVEN-CENTRAL.md`.
