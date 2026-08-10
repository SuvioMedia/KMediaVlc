<!-- SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary -->

# Architecture

The dependency graph is intentionally one-way:

```text
Suvio
  -> composemediaplayer-libvlc
       -> kmedia-vlc-runtime-desktop
            -> audited libVLC 4 + plugins + bridge
```

Neither `composemediaplayer` nor `mediaplayer-core` depends on KMediaVlc.
KMediaPlayer owns player state and the Nucleus `TextureView` integration;
KMediaVlc owns runtime extraction, ABI normalization, frame ownership, and
release compliance.

The stable bridge hides the preview libVLC ABI. Updating the pinned VLC
revision requires rebuilding the bridge and publishing a new immutable
KMediaVlc runtime. Unknown libVLC 4 previews are not accepted as bundled
runtimes.

The published native payload matrix contains Windows x86-64/ARM64 only. Its GPU
producer uses libVLC's D3D11 output callback with a bounded BT.2020/PQ
intermediate, followed by KMediaVlc's PQ-to-linear-sRGB FP16 shader. This is
necessary because the pinned stock libVLC D3D11 render-format table does not
accept `DXGI_FORMAT_R16G16B16A16_FLOAT` as the callback target.

The Apple-silicon macOS producer is the next staged platform. It gives pinned
libVLC 4 a private CGL 3.2 core context and renders into one of four
IOSurface-backed rectangle textures. The published frame retains the selected
surface and its context until the consumer releases it, so VLC cannot overwrite
a buffer still referenced by a Metal command buffer. SDR uses BGRA8 storage
with sRGB transfer; requested HDR output uses RGBA16F linear-sRGB storage.
This producer, its hermetic callback/IOSurface test, and a relocatable
source-built libVLC candidate are implemented. Per-binary source/license
closure, real media/HDR coverage, and Metal host acceptance remain release
gates.

The first iOS producer uses the stable bridge's CPU-pull callbacks. Its arm64
device and Apple-silicon simulator slices keep libVLC, core, bridge, and each
allowlisted VLC plugin dynamically replaceable as flattened iOS frameworks.
Xcode embeds and signs the selected XCFramework slices; KMediaVlc never extracts
native code from Maven at runtime. iOS GPU push remains fail-closed. Linux still
uses the fail-closed platform stub.

Supporting libVLC 3 remains an adapter concern. A process selects exactly one
major runtime and never loads libVLC 3 and 4 plugin graphs together.
