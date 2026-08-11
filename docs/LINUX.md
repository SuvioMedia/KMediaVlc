<!-- SPDX-License-Identifier: LGPL-2.1-or-later -->

# Linux bundled runtime candidate

Linux x86-64 and AArch64 are implemented as source-built, unpublished
libVLC 4 candidates. Both targets use the exact VideoLAN revision
`b5536cdea24b313ba9215eacfbd7fa3295d7f3ee`, an 85-plugin closed playback
allowlist, and the reviewed 26-component contrib graph. No Linux native
payload is downloaded at runtime or retained by validation CI.

The candidate baseline is glibc 2.39. The six deliberately system-provided
build dependencies are EGL, GLES2, GBM, libdrm, fontconfig, and PulseAudio;
the stager independently closes all resulting ELF dependencies and symbol
version ceilings. Every other selected codec, demuxer, text renderer, and TLS
dependency comes from the pinned VLC contrib source graph.
The glibc dynamic loader is admitted only under its target-specific SONAME:
`ld-linux-x86-64.so.2` or `ld-linux-aarch64.so.1`; it is not a cross-target
wildcard.
The WebVTT CSS engine is enabled explicitly because the global Meson auto
feature policy is disabled; its Flex/Bison outputs add no runtime dependency.

Every staged shared object is private to the application runtime and links
with `-Bsymbolic`. Definitions inside each object therefore bind locally rather
than being interposed. This also makes the pinned static FFmpeg data references
valid in AArch64 VLC plugins; undefined plugin ABI symbols still resolve from
`libvlccore` normally.

The build also uses `--as-needed`. A self-contained plugin such as
`float_mixer` can therefore have no direct `DT_NEEDED` edge to `libvlccore`.
The ELF audit accepts zero or one exact private-core edge for a plugin, while
still requiring exactly one for `libvlc`; every other dependency remains on
the closed system allowlist.

The selected PulseAudio output also requires VideoLAN's
`libvlc_pulse.so` helper, built from the pinned LGPL-2.1-or-later
`modules/audio_output/vlcpulse.c`. It is explicitly inventoried, staged next
to the private core, relocated with its own SONAME and `$ORIGIN` RUNPATH, and
audited under the same ELF closure and symbol ceilings. It is not treated as a
system-library exception. The dependency is directional: the `pulse` plugin
must have exactly one edge to this helper, and no unrelated plugin is allowed
to acquire it.

The pinned prerelease Meson graph installs libVLC as the unversioned
`libvlc.so`. Staging deliberately renames that input to the application-private
`libvlc.so.12` contract and writes the matching SONAME and `$ORIGIN` RUNPATH;
the source install is never mistaken for an already versioned upstream ABI.
For libvlccore, the stager copies the real `libvlccore.so.9.0.0` input and
normalizes it to `libvlccore.so.9`; it verifies but never follows the upstream
`.9` symlink.
Meson likewise installs plugin binaries in one flat directory. The stager
selects their globally unique filenames there, while retaining each logical
module family in the inventory and audit report.

In this prerelease tree, Meson defines `vlc-cache-gen` only when the full VLC
application is enabled. The build wrapper compiles the exact upstream
`bin/cachegen.c` against the installed candidate solely to create
`plugins.dat`. That GPL build helper is not copied into the staged runtime;
the packaged graph remains the closed playback library and plugin set.

## GPU frame transport

`GPU_PUSH` uses libVLC 4's GLES2 output callbacks and a private EGL context on
the consumer-supplied DRM render node. The consumer must advertise concrete
DRM format/modifier pairs. The first bounded transport deliberately supports
only single-plane `DRM_FORMAT_ABGR8888` that is both importable by EGL and
renderable as a GLES2 framebuffer; implicit `DRM_FORMAT_MOD_INVALID` layouts
are rejected.

The producer owns four explicitly modified GBM buffer objects. A published
frame retains its buffer object and one exported DMA-BUF descriptor until
`frame_release`. The descriptor, `stride`, `offset`, FourCC, and 64-bit
modifier are authoritative for import. A buffer referenced by an acquired
frame cannot be selected for another VLC render.

When acquire fences are negotiated, the producer inserts an
`EGL_SYNC_NATIVE_FENCE_ANDROID`, flushes GLES2, and transfers the duplicated
sync-file descriptor with the frame. Without acquire fences it completes the
producer work with `glFinish` before publication. A supplied consumer release
fence transfers back to EGL before the buffer is reused. If the consumer
promised release fences but releases an acquired frame without one, that GBM
buffer is retired and replaced rather than reused unsafely. Skipped frames
were never imported by the consumer and return directly to the pool.

The `-PkmediaVlcTestLinuxRenderNode=/dev/dri/renderD128` opt-in physical probe
creates a second, independent GBM/EGL context on the requested render node.
It enumerates concrete consumer modifiers, waits on each acquire sync-file,
imports the published DMA-BUF, reads back a pixel, and returns a new release
sync-file. Seven sequential imports deliberately omit one release fence and
then require four more successful frames, covering fail-closed retirement and
replacement. Hosted runners compile this probe but do not run it or claim
hardware evidence.

The manually dispatched `Linux DMA-BUF hardware probe` runs only from the
default branch and accepts an exact KMediaVlc commit, an
x64/ARM64 choice, and a `/dev/dri/renderD*` node. It targets a dedicated
self-hosted runner carrying all of the `self-hosted`, `linux`, architecture,
and `kmediavlc-linux-gpu` labels. The runner must be pre-provisioned with the
same Linux build dependencies as source validation. The job uploads no binary
and removes its unpublished candidate after every outcome.

The current Linux transport is SDR RGBA8/sRGB. PQ and HLG source identity is
still reported in frame metadata, but libVLC tone-maps those sources to the
BT.709/sRGB output. `requestHdr=true` therefore does not claim a native Linux
HDR surface yet.

## CPU pull and VR

`CPU_PULL` remains the deterministic compatibility and diagnostic path. The
validation workflow builds both native architectures, stages exactly the
allowlisted graph, and must decode a real CPU frame against the staged
runtime.

KMediaPlayer's current “VR” scope is projection of a decoded video frame, not
a separate Linux operating-system target. It consumes the same DMA-BUF frame
contract as the normal Linux GPU path. Release eligibility still requires a
real KMediaPlayer projection consumer to import frames on representative DRM
hardware and return working release fences. A future Quest, visionOS, or other
standalone platform would need its own target and is not implied by this
Linux candidate.

## Hosted candidate evidence

At KMediaVlc commit `dcf8784b1728180dc7a46d3d2687f1bdc2019f51`,
[Linux source validation run 31394164852](https://github.com/SuvioMedia/KMediaVlc/actions/runs/31394164852)
completed successfully on 2026-08-10. Its native
[x86-64](https://github.com/SuvioMedia/KMediaVlc/actions/runs/31394164852/job/93473081464)
and
[AArch64](https://github.com/SuvioMedia/KMediaVlc/actions/runs/31394164852/job/93473081599)
jobs each built the pinned libVLC graph from source, validated the bounded
install, staged and audited the 85 plugins plus the private support graph,
generated `plugins.dat`, and decoded a real CPU frame against that staged
runtime. Both jobs also compiled the independent DMA-BUF/fence inspector, but
did not execute its opt-in physical-render-node test. CI retained no native
payload.

This closes only the hosted source-build, ELF/cache, and CPU-playback gate. It
does not provide physical render-node, DMA-BUF import, explicit-fence, VR
consumer, or final legal evidence.

## Publication gates still open

Linux remains fail-closed and is not release-eligible until all of these gates
are recorded:

- real render-node allocation and DMA-BUF import on representative Intel/AMD
  and ARM graphics drivers;
- acquire-fence and release-fence ownership tests, including missing-fence
  retirement and buffer reuse;
- KMediaPlayer/Nucleus normal and VR-projection consumer acceptance;
- final per-binary source and license review;
- approval of both Linux policy files for the exact candidate. The runtime
  selector and GLES2 manifest contract recognize both Linux architectures,
  while the release packager still rejects them until those reviews are
  explicitly `approved`.

The hosted source-validation runners are intentionally insufficient for the
physical GPU and VR gates. Candidate reports continue to record those fields
as pending, and the release packager rejects Linux.
