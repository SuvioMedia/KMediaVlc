<!-- SPDX-License-Identifier: LGPL-2.1-or-later -->

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
native code from Maven at runtime. iOS GPU push remains fail-closed.

The Linux x86-64/AArch64 producer gives pinned libVLC 4 a private GLES2 context
on the consumer's DRM render node. It renders into four explicitly modified
ABGR8888 GBM buffers, publishes one DMA-BUF fd per acquired frame, and transfers
native sync-file ownership in both directions. Missing promised release fences
retire buffers instead of allowing unsafe reuse. HDR sources are currently
tone-mapped into the declared SDR sRGB transport. The source build, renderer,
and fail-closed stager are implemented; physical GPU/fence evidence,
KMediaPlayer normal and VR-projection acceptance, and legal review remain
publication gates.

Android is a distinct AAR boundary rather than an extraction variant of the
desktop JAR. It explicitly loads the source-built `libvlc.so` first so the VLC
core Android `JNI_OnLoad` records the JVM, then loads a narrow client bridge.
The bridge owns Surface-to-ANativeWindow reference transitions and gives each
VLC vout setup an independent binding. Android publication remains fail-closed
until the source-built static module graph and real device lifecycle evidence
are approved.

## Tracks and delays

Desktop, Android and the iOS C ABI share one contract for audio and subtitle
tracks. `kmediavlc_player_track_count` / `kmediavlc_player_get_track` (Java:
`tracks(type)`) list the tracks of the current input with libVLC's string ids;
`kmediavlc_player_select_track` (Java: `selectTrack`, `disableTracks`) chooses
one id or none. `kmediavlc_player_tracks_generation` changes on every track
list or selection change and on open, so clients re-read only after it moved.

libVLC keeps the audio delay, the subtitle delay and the track choice on one
input and forgets them when that input stops: on stop and replay, at the end,
and when a running media is replaced. A replaced media also gets no state
callback, because libVLC's global state stays Playing. The bridges therefore
keep the client's values and give them to every new input: the delays for
every media, the track choice for the current media until the next open. The
desktop and iOS bridge applies them from libVLC's track callbacks and from
snapshot polling; the Android bridge from its serialized commands and snapshot
polling. Signs follow libVLC: a positive delay plays the sound or shows the
subtitles later.

Supporting libVLC 3 remains an adapter concern. A process selects exactly one
major runtime and never loads libVLC 3 and 4 plugin graphs together.
