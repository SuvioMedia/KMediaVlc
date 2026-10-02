# Android decoded-frame timing candidate

The optional `VlcAndroidPlayer.framePresentationTimeUs(generation, producerTimestampNs)` query
associates an acquired `SurfaceTexture` buffer with its decoded video PTS. Android's producer
timestamp may be the scheduled **system** time passed to MediaCodec; it must not be used as media
PTS, or estimated from the UI playback position.

The source patch adds the private, versioned `libvlc_kmedia_set_anw_frame_callback_v1` symbol.
The Android display records the picture's decoded PTS (microseconds, without `VLC_TICK_0`) and
the exact timestamp passed to timed MediaCodec release, before making that buffer available.
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

Validation is in progress. The host timing test covers signed PTS, exact lookup, source isolation,
bounded retirement and concurrent producer/consumer access. Both Android JNI ABI compilations and
the Java runtime check pass. Full source-built runtime playback, paused seek/source replacement,
matched AAR provenance and real-video ONNX acceptance are still required before publication.
