<!-- SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary -->

# Architecture

The dependency graph is intentionally one-way:

```text
Suvio
  -> composemediaplayer-libvlc
       -> kmedia-vlc-runtime-desktop -> audited libVLC 4 + plugins + GPU bridge
       -> kmedia-vlc-runtime-android -> source-built libvlc.so + ANativeWindow bridge
```

Neither `composemediaplayer` nor `mediaplayer-core` depends on KMediaVlc.
KMediaPlayer owns player state and the Nucleus `TextureView` integration;
KMediaVlc owns runtime extraction, ABI normalization, frame ownership, and
release compliance.

The stable bridge hides the preview libVLC ABI. Updating the pinned VLC
revision requires rebuilding the bridge and publishing a new immutable
KMediaVlc runtime. Unknown libVLC 4 previews are not accepted as bundled
runtimes.

The initial native payload matrix contains Windows x86-64/ARM64 only. Its GPU
producer uses libVLC's D3D11 output callback with a bounded BT.2020/PQ
intermediate, followed by KMediaVlc's PQ-to-linear-sRGB FP16 shader. This is
necessary because the pinned stock libVLC D3D11 render-format table does not
accept `DXGI_FORMAT_R16G16B16A16_FLOAT` as the callback target. macOS and
Linux remain fail-closed until their GPU import and fence paths are complete.

Android is a distinct AAR boundary rather than an extraction variant of the
desktop JAR. It explicitly loads the source-built `libvlc.so` first so the VLC
core Android `JNI_OnLoad` records the JVM, then loads a narrow client bridge.
The bridge owns Surface-to-ANativeWindow reference transitions and gives each
VLC vout setup an independent binding. Android publication remains fail-closed
until the source-built static module graph and real device lifecycle evidence
are approved.

Supporting libVLC 3 remains an adapter concern. A process selects exactly one
major runtime and never loads libVLC 3 and 4 plugin graphs together.
