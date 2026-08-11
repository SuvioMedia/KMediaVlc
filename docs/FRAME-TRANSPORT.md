<!-- SPDX-License-Identifier: LGPL-2.1-or-later -->

# Push notification and pull ownership

libVLC renders on an internal thread and explicitly requires the host to
provide synchronization. KMediaVlc therefore separates notification from
ownership.

`publish(frame)` atomically replaces the pending frame. If the consumer did
not acquire the previous pending frame, that frame is released immediately.
The producer then emits a lightweight notification containing only serial and
generation. The notification never transfers a native handle.

Each acquired frame also carries the decoded source dynamic range separately
from its output pixel format. An HDR-capable host therefore does not turn an
SDR source into a false HDR frame; PQ and HLG sources may use linear FP16,
while SDR remains sRGB unless the consumer explicitly requests another route.

`acquireLatest()` removes and returns the newest pending frame. There is one
consumer, and it must release the frame after Nucleus has imported it or after
an acquire fence fails. Closing the transport releases the pending frame and
rejects later publications.

This arrangement supports:

- GPU push without calling Compose from VLC's render thread;
- pull-based thumbnails and deterministic tests;
- bounded memory regardless of decoder cadence;
- generation-safe monitor/device changes;
- guaranteed release of skipped frames.

## macOS IOSurface hand-off

The macOS producer owns four IOSurfaces. libVLC renders into a surface through
its OpenGL output callbacks, the bridge flushes the producer context, and only
then publishes that surface's global ID. The consumer imports the ID as a Metal
texture after the notification and retains the acquired KMediaVlc frame until
the Metal command buffer has completed. Releasing the frame returns that
surface to the producer pool; there is no file-descriptor fence on macOS.

SDR surfaces use `kCVPixelFormatType_32BGRA` storage and must be imported as an
sRGB BGRA texture. HDR/HLG sources use `kCVPixelFormatType_64RGBAHalf` only when
HDR output was requested; their values are linear-sRGB FP16. `fourcc` and
`stride` are authoritative for the IOSurface storage layout, while
`pixelFormat` describes the color interpretation exposed to KMediaPlayer.

The opt-in native integration test uses a test-only library that implements the
exact pinned libVLC ABI and drives the real callback sequence. It creates a CGL
context and IOSurface and reopens the published ID through JNI. It proves ABI,
ownership, and allocation behavior without making an unaudited VLC binary a
release input. Real playback, Metal import, and hardware color acceptance are
separate publication gates.

## Linux DMA-BUF hand-off

The Linux producer owns four GBM buffer objects negotiated as concrete
single-plane ABGR8888 format/modifier pairs. libVLC renders into their EGLImage
GLES2 framebuffers. Publishing duplicates the selected buffer's DMA-BUF fd;
the native frame retains that fd and the buffer object until release, while
the consumer owns any acquire sync-file returned by `acquireLatest()`.

With explicit synchronization enabled, a producer native-fence sync is
inserted after rendering and its duplicated fd accompanies the frame. The
consumer transfers its completion fence back through `release`. EGL waits on
that fence before the producer reuses the buffer. An acquired frame released
without a fence after the consumer advertised release-fence support permanently
retires that buffer allocation. A superseded, unacquired frame needs no
consumer fence and is immediately reusable.

The first Linux transport is RGBA8/sRGB even for an HDR source; the decoded
source range remains separate metadata, and libVLC tone-maps PQ/HLG to SDR.
Real DRM import and fence behavior are physical-hardware publication gates.
