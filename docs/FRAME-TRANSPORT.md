<!-- SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary -->

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
