<!-- SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary -->

# Android bundled libVLC 4

KMediaVlc has a narrow Android AAR boundary for the exact VLC revision
`b5536cdea24b313ba9215eacfbd7fa3295d7f3ee`. The source-build tooling is pinned
separately to VideoLAN `libvlcjni` revision
`a8d53a9151d7e4a9a5dfd0a5eb1cd92669afdc21`.

This milestone is **not a published native payload yet**. The Java API, JNI bridge, two-ABI
payload contract, hermetic NDK ABI fixture, and real pinned source builds are implemented. A
candidate remains `releaseEligible=false` until the source-mapped contrib graph receives its
linked-member SPDX/notice review and device playback evidence is closed.

## AAR contract

The only accepted native inventory is:

```text
jni/arm64-v8a/libvlc.so
jni/arm64-v8a/libkmediavlc_android.so
jni/armeabi-v7a/libvlc.so
jni/armeabi-v7a/libkmediavlc_android.so
```

A source-built candidate also carries `legal/android-static-legal.json` plus exactly 83 contrib
license/patent/source-notice files and three NDK evidence files. These are build evidence, not
additional native libraries.

The AAR has `minSdk=28`. Both libraries use 16 KiB ELF load alignment. C++ is linked
statically, so neither `libc++_shared.so` nor VideoLAN's broad `libvlcjni.so` wrapper is packaged.
The official `libvlcjni` repository is used only as the pinned source-build machinery that folds
the selected VLC modules into `libvlc.so`.

Java deliberately loads `libvlc.so` before `libkmediavlc_android.so`. Android therefore invokes
the VLC core `JNI_OnLoad`, which installs the process JVM used by VLC's Android modules, before
the client bridge calls `libvlc_new`.

## Surface ownership

The bridge selects libVLC 4's `libvlc_video_engine_anw` callback API. It does not depend on
`org.videolan.libvlc.AWindow`.

1. `ANativeWindow_fromSurface` gives the player one owned reference for each attached Surface.
2. Every VLC vout setup creates a private binding and acquires its own references while holding
   the player's surface mutex.
3. The update callback returns those stable video/subtitle windows to VLC. VLC acquires its own
   references in `AWindowHandler_newFromANWs`.
4. VLC's cleanup callback destroys the binding and releases its references.
5. Attach, replace, and detach reinstall the ANativeWindow callbacks, forcing only the vout to be
   recreated; audio playback can continue through a Surface lifecycle transition.

The optional second Surface is the transparent subtitle plane required when MediaCodec renders
opaque video directly into the first Surface. Software-only decoding is an explicit closed mode
implemented with VLC 4's `:no-hw-dec` media option.

The bridge does not change process-wide `HOME` or any other environment variable. VLC credential
storage is forced to the non-persistent `memory` keystore.

## Source build

The candidate recipe is `build-recipes/android.json`. It uses NDK `29.0.14206865`, VideoLAN's
`--license a` contrib profile, `--static-cpp`, and `--no-jni`; it never consumes a published AAR,
stock nightly, or prebuilt contrib archive.

The build uses a transient copy of the pinned `libvlcjni` build machinery and applies the
committed `patches/libvlcjni/0001-kmediavlc-android-static-module-policy.patch`. The patch sorts
the selected module archives deterministically and excludes the explicit VLC modules that
declare GPL-2.0-or-later. It also disables the Blu-ray contrib and VLC module: the Android API
does not expose optical-disc playback, and a host-only BD-J Java payload must not enter the
mobile source build. File, HTTP, adaptive, MP4, Matroska, MediaCodec, and software-decoding
modules remain explicit required inputs. Neither upstream checkout is modified.

```shell
bash scripts/build_vlc_android.sh \
  /path/to/vlc \
  /path/to/libvlcjni \
  /path/to/android-ndk-r29 \
  /path/to/cmake \
  /path/to/audit-work \
  /path/to/empty-candidate-output
```

For each ABI the build asks the NDK linker for a map of the archive members that actually enter
`libvlc.so`. `scripts/create_android_link_audit.py` cross-checks that map against VLC's generated
module array and installed module archives, hashes every linked archive, and classifies each one
as VLC core, VLC module, contrib, or NDK toolchain input. It rejects unknown roots, missing
playback modules, forbidden `DT_NEEDED` entries, wrong load alignment, missing exports, a GPL
module marker, or a module without VLC's exact LGPL marker. The final `libvlc.so` must contain
no GPL module marker; the report records whether its LGPL metadata marker survives section
garbage collection. Eligibility comes from the verified linked module archives and exact link
graph, not from assuming that unused metadata strings survive the final link. Path-free candidate
reports, including the policy-patch hash, are written under `/path/to/audit-work/link-audits`;
linker maps remain local build evidence and are not placed in the AAR. The report records VLC's
declared LGPL license but deliberately leaves the final effective SPDX expression unset until
every contributing static archive is reviewed. The closed policy in
`compliance/policy/android-static-components.json` maps every permitted contrib archive to its
exact pinned source tarball and maps the four ABI-specific NDK runtime archives to the NDK
distribution evidence. The audit rejects missing or extra entries, hashes all 55 contributing
source tarballs, 83 exact in-archive license/patent/source-notice records, and the NDK
notices/identity file. It also records a conservative candidate SPDX set for each component;
those candidates remain explicitly pending linked-member review and are not an eligibility
decision.

After both ABI reports agree byte-for-byte on their component evidence,
`scripts/stage_android_legal_evidence.py` copies the 86 hash-matched records into the candidate
payload without extracting either source tree. Its path-free manifest binds both ABI report
hashes, both `libvlc.so` hashes, the component-policy hash, every staged file hash, and the null
effective-license field. Gradle rehashes the complete bundle and packages it under
`assets/kmediavlc/legal/ANDROID_STATIC/`. Publication requires that manifest and every component
to be explicitly promoted to `approved`; editing `releaseEligible=true` alone is insufficient.

### Verified source-build evidence

On 2026-08-10 the exact pinned recipe completed on macOS with NDK `29.0.14206865` and CMake
`4.1.2` for both publication ABIs. The ARM64 audit recorded 307 VLC modules, 62 contrib
archives, four NDK toolchain archives, and three VLC core archives. ARMv7 recorded 305, 62,
four, and three respectively. Both final libraries expose the required core/JNI symbols, have
only the closed Android system `DT_NEEDED` set, and use 16 KiB `LOAD` alignment. The stripped
two-ABI payload also passed the actual Gradle AAR inventory and lint/test gate.

These counts now match the exact source allowlist: 62 archive paths resolve to 54 contrib source
components and 55 source tarballs (TagLib also consumes the header-only utfcpp source). The
path-free reports are therefore promoted only to
`candidate-source-mapped-license-review-pending`. This state proves archive-to-source and raw
license-evidence closure, but not the final linked-member SPDX conclusion, packaged notice
completeness, NDK corresponding-source map, or release eligibility. Native binaries remain
external release inputs and are not committed to this repository.

An audited payload can be supplied to Gradle with
`-PkmediaVlcAndroidNativePayloadDirectory=/path/to/payload`. Publication additionally requires
the manifest to say `releaseEligible=true` and requires the exact corresponding-source archive.

## Publication gates still open

- review and approve the exact module lists emitted by the completed fail-closed
  compiled-license and linker-map audits;
- review the linked members of every source-mapped contrib and toolchain archive, then bind the
  approved SPDX expressions and complete notices to the already recorded source/archive hashes;
- retain reviewed evidence for the real source-built `libvlc.so` DT_NEEDED/export surface for
  both ABIs;
- run an Android device/emulator fixture through create/open/play, MediaCodec and software decode,
  video plus subtitle surfaces, repeated detach/reattach, seek, stop, and destruction;
- publish complete corresponding source and reproducible relinking instructions.

Until all five gates pass, the module is useful for API and ABI integration work but cannot be
published as a bundled runtime.
