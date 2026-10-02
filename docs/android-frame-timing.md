# Android decoded-frame timing candidate

The optional `VlcAndroidPlayer.framePresentationTimeUs(generation, producerTimestampNs)` query
associates an acquired `SurfaceTexture` buffer with its decoded video PTS. Android's producer
timestamp may be the scheduled **system** time passed to MediaCodec; it must not be used as media
PTS, or estimated from the UI playback position.

The source patch adds the private, versioned `libvlc_kmedia_set_anw_frame_callback_v1` symbol.
The MediaCodec picture context retains the decoded PTS (microseconds, without `VLC_TICK_0`).
The thread atomically consuming the decoder buffer index records it with the exact timestamp
passed to timed MediaCodec release, before making that buffer available.
These are decoder timestamps; no approximation or normalization against the player UI clock occurs.
The native bridge installs its callback before opening media and associates it with an immutable
media generation. Processing source replacement also replaces the decoder Surface/output.

The callback does not call back into libVLC. A mutex protects a fixed 128-entry ring; the render
consumer performs an exact generation/timestamp lookup. Unknown, stale and retired buffers return
`Long.MIN_VALUE`. A runtime without the private source hook also returns this sentinel. The public
Java and native methods remain serialized against disposal. Existing consumer keep rules retain
the public query and its JNI entry point for applications detecting this optional API reflectively.

This implementation covers the direct, timed MediaCodec ANativeWindow route. Untimed/software
output supplies no timing proof and must bypass asynchronous processing until it gains a corresponding
transport. Ordinary native playback and synchronous GPU filters do not require this timing capability.

Surface replacement now uses explicit Play/Pause/Stop intent. A successful asynchronous Stop may
still report the previous PAUSED/PLAYING state; that stale observation must not restart the old media
while the application is attaching the next input Surface.
The replacement input also defers Pause until `libvlc_media_player_can_pause` succeeds. Pinned
libVLC implements an early `set_pause` as Stop when this capability is not yet available, which
can leave a new Surface empty indefinitely. Regular snapshot polling applies the pending command;
explicit Play, Stop or new media cancels it. Deferral applies to replacing a previously paused
Surface; an explicit Pause on a genuinely unpausable source retains libVLC's existing behavior.
No callback reenters libVLC.

The first full-film diagnostic exposed an important distinction: the display's Prepare callback
can run repeatedly on a paused picture whose decoder buffer has already been released. Recording
each such redraw filled the 128-entry ring with producer timestamps that never reached the Surface,
retiring the real acquired buffer's mapping. Recording inside the actual decoder-index consumption
prevents these nonexistent frames from entering the lookup. The bounded diagnostic confirmed that
symbol discovery, callback registration, generation and exact Surface keys otherwise matched.

A separate paused-seek failure is reproducible without ONNX and survives serializing Pause/Seek
in the client. The native state is PAUSED and seekable, with the requested target still pending.
A matching symbolized native backtrace places the input and decoder threads in their idle waits.
Forcing the demux loop to re-enter after a successful seek did not fix it; that candidate is rejected
and removed from the working patch. A bounded native trace rules out a later Surface replacement
and confirms successful control enqueue, consume and demux seek. The decoder trace then shows the requested picture
arriving with first=true, waiting=false, paused=true after buffering completes. The first-picture
flag previously depended on waiting=true, so this asynchronous completion could use normal clocked
presentation while paused. The source correction forces the first paused picture even after
buffering completes. All diagnostic logging and the rejected wakeup change are removed; the same
repeated-video regression must pass on this candidate before acceptance. The retained traces contain
numeric state/PTS only, never media URIs, headers or payloads.

Validation is in progress. The host timing test covers signed PTS, exact lookup, source isolation,
bounded retirement and concurrent producer/consumer access. Both Android JNI ABI compilations and
the Java runtime check pass. Full source-built runtime playback, paused seek/source replacement,
matched AAR provenance and real-video ONNX acceptance are still required before publication.

`native/android/tests/transport_test.cpp` exercises the actual JNI bridge against a libVLC fixture
with delayed pause capability and delayed Stop notification. It runs as an Android executable
from a fake-library build (both ABIs compile). On the ARM64 emulator it checks paused Surface
replacement with retained position, Play/Stop cancellation, stale observed state and an unpausable source.
A separate guard-removal negative control aborts at the paused-replacement assertion; the guarded code
passes. The fixture does not replace the real-video acceptance test.
