<!-- SPDX-License-Identifier: LGPL-2.1-or-later -->

# Native client ABI

`include/kmediavlc_client.h` is the stable boundary consumed by KMediaPlayer.
Platform implementations are built against the exact pinned libVLC 4 header
and included only in verified release payloads.

The ABI deliberately exposes notification plus latest-frame acquisition. It
does not expose libVLC preview structs to Kotlin/JVM and does not accept a
native child window.

## Platform producers

- Windows uses libVLC's D3D11 callback and shared texture handles.
- Apple-silicon macOS uses a private CGL context and a four-entry IOSurface
  pool. The host imports the published IOSurface ID with Metal and releases the
  acquired frame after its command buffer completes.
- Other desktop targets currently compile the fail-closed renderer stub.

Every native build requires a checkout at the exact revision recorded in
`CMakeLists.txt`; headers from another libVLC preview are rejected. The macOS
ABI/IOSurface fixture is test-only and can be built with:

```shell
./gradlew buildNativeBridge \
  -PkmediaVlcVlcSourceDir=/path/to/pinned/vlc \
  -PkmediaVlcBuildNativeTestFixtures=true
```

`kmediavlc_fake_libvlc` is never installed or included in a runtime payload.
