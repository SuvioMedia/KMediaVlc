<!-- SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary -->

# Android bundled libVLC 4

KMediaVlc has a narrow Android AAR boundary for the exact VLC revision
`b5536cdea24b313ba9215eacfbd7fa3295d7f3ee`. The source-build tooling is pinned
separately to VideoLAN `libvlcjni` revision
`a8d53a9151d7e4a9a5dfd0a5eb1cd92669afdc21`.

This milestone is **not a published native payload yet**. The Java API, JNI bridge, two-ABI
payload contract, hermetic NDK ABI fixture, real pinned source builds, and ARM64 emulator playback
evidence are implemented. A candidate remains `releaseEligible=false` until the source-mapped
contrib graph receives its linked-member SPDX/notice review and final release-bound source
artifacts are retained.

## AAR contract

The only accepted native inventory is:

```text
jni/arm64-v8a/libvlc.so
jni/arm64-v8a/libkmediavlc_android.so
jni/armeabi-v7a/libvlc.so
jni/armeabi-v7a/libkmediavlc_android.so
```

A source-built candidate also carries `legal/android-static-legal.json` plus exactly 83 contrib
license/patent/source-notice files and five NDK evidence/provenance files. These are build evidence, not
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
5. Detach drops the player's Surface references while the active VLC binding keeps its own stable
   references until VLC tears it down.
6. Attaching different Surfaces while media is open recreates `libvlc_media_player_t`, reuses the
   bridge-owned media, restores position/rate/volume and playing/paused state, and installs the
   ANativeWindow callbacks exactly once on the new player. Playback can continue during the
   detached interval; reattachment incurs a brief native-player restart.

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
distribution plus exact upstream source revisions. `libclang_rt.builtins` maps to `compiler-rt`,
`libunwind.a` to `libunwind`, `libc++_static.a` to `libcxx`, and `libc++abi.a` to `libcxxabi`.
All four use LLVM commit `386af4a5c64ab75eaee2448dc38f2e34a40bfed0` with Android build and
patch revision `1dab3288f660d43a6cb2479107e2b54b3ab0a2a1`. The audit rejects missing or
extra entries, hashes all 55 contributing
source tarballs, 83 exact in-archive license/patent/source-notice records, and the NDK
notices, identity, `AndroidVersion.txt`, and `clang_source_info.md`. It also records a
conservative candidate SPDX set for each component;
those candidates remain explicitly pending linked-member review and are not an eligibility
decision.

After both ABI reports agree byte-for-byte on their component evidence,
`scripts/stage_android_legal_evidence.py` copies the 88 hash-matched records into the candidate
payload without extracting either source tree. Its path-free manifest binds both ABI report
hashes, both `libvlc.so` hashes, the component-policy hash, every staged file hash, both exact
source Git trees, the selected host-prebuilt tag/commit, and the null effective-license field.
Gradle rehashes the complete bundle and packages it under
`assets/kmediavlc/legal/ANDROID_STATIC/`. Publication requires that manifest and every component
to be explicitly promoted to `approved`, and separately requires the NDK source status to become
`corresponding-source-mapped`; editing `releaseEligible=true` alone is insufficient.

### NDK runtime source package

`scripts/package_android_ndk_source.py` creates a deterministic, version-bound archive directly
from the two exact Git checkouts recorded by the static-component policy. It packages the complete
tracked `llvm_android` build/patch tree and a closed LLVM source/build closure containing `cmake`,
`compiler-rt`, `libcxx`, `libcxxabi`, `libunwind`, the required LLVM CMake/include/lit support,
`runtimes`, and `third-party`. Untracked files are ignored; tracked modifications, a different
commit/tree, symlinks, special files, missing required paths, and bytes that do not hash to the
recorded Git blob are rejected.

```text
python3 scripts/package_android_ndk_source.py \
  --root . \
  --llvm-project /path/to/llvm-project-386af4a5 \
  --llvm-android /path/to/llvm_android-1dab3288 \
  --tested-commit <exact-kmediavlc-commit> \
  --version <immutable-version> \
  --epoch <tested-commit-unix-time> \
  --output kmedia-vlc-<version>-android-ndk-source.tar.gz

python3 scripts/verify_android_ndk_source_archive.py \
  --root . \
  --archive kmedia-vlc-<version>-android-ndk-source.tar.gz \
  --llvm-project /path/to/llvm-project-386af4a5 \
  --llvm-android /path/to/llvm_android-1dab3288 \
  --version <immutable-version> \
  --tested-commit <exact-kmediavlc-commit>
```

The verifier independently reconstructs the expected file inventory from both exact Git trees and
compares every archive member's Git blob ID, SHA-256, mode, size, path, and deterministic metadata.
The archive manifest also binds the current component-policy hash, NDK release provenance, and
archive-to-source map. Maven publication runs the same verifier and requires
`kmediaVlcAndroidNdkSourceArchive`, `kmediaVlcAndroidLlvmProjectSourceDirectory`,
`kmediaVlcAndroidLlvmAndroidSourceDirectory`, and `recipeRevision` together.
The archive is attached with classifier `android-ndk-source`; it supplements, rather than replaces,
the complete Android corresponding-source archive.

### Complete corresponding source

`scripts/package_android_corresponding_source.py` creates the complete Android source/relinking
artifact directly from clean Git checkouts and the real hash-bound build evidence. It includes the
full tracked KMediaVlc, VLC, and libvlcjni trees; exactly the 55 contrib tarballs selected by the
legal manifest; the independently verified NDK runtime source archive; that legal manifest; both
path-free ABI link audits; and generated rebuild/checksum instructions. The KMediaVlc tree and all
generated metadata bind the final tested commit and its Unix timestamp.

```text
python3 scripts/package_android_corresponding_source.py \
  --root . \
  --vlc /path/to/vlc-b5536cde \
  --libvlcjni /path/to/libvlcjni-a8d53a91 \
  --contrib-tarballs /path/to/vlc-b5536cde/contrib/tarballs \
  --ndk-source-archive kmedia-vlc-<version>-android-ndk-source.tar.gz \
  --llvm-project /path/to/llvm-project-386af4a5 \
  --llvm-android /path/to/llvm_android-1dab3288 \
  --legal-manifest /path/to/payload/legal/android-static-legal.json \
  --arm64-audit /path/to/android-arm64-v8a.json \
  --armv7-audit /path/to/android-armeabi-v7a.json \
  --tested-commit <exact-kmediavlc-commit> \
  --version <immutable-version> \
  --epoch <tested-commit-unix-time> \
  --output kmedia-vlc-<version>-android-corresponding-source.tar.gz

python3 scripts/verify_android_corresponding_source_archive.py \
  --root . \
  --archive kmedia-vlc-<version>-android-corresponding-source.tar.gz \
  --vlc /path/to/vlc-b5536cde \
  --libvlcjni /path/to/libvlcjni-a8d53a91 \
  --contrib-tarballs /path/to/vlc-b5536cde/contrib/tarballs \
  --ndk-source-archive kmedia-vlc-<version>-android-ndk-source.tar.gz \
  --llvm-project /path/to/llvm-project-386af4a5 \
  --llvm-android /path/to/llvm_android-1dab3288 \
  --legal-manifest /path/to/payload/legal/android-static-legal.json \
  --arm64-audit /path/to/android-arm64-v8a.json \
  --armv7-audit /path/to/android-armeabi-v7a.json \
  --version <immutable-version> \
  --tested-commit <exact-kmediavlc-commit>
```

The verifier does not import the corresponding-source packager. It independently reconstructs all
three Git inventories, compares every Git blob/mode and external SHA-256, repeats the nested NDK
verification, validates deterministic gzip/tar metadata and exact member order, and rejects links,
special files, missing/extra paths, modified checkouts, a different release identity, or tampering.
Gradle runs it through `verifyAndroidCorrespondingSourceArchive` and attaches the result with
classifier `corresponding-source` only when every source/evidence property is configured together.

### Physical-device acceptance harness

The emulator evidence above is reproducible, but it is not physical-device
acceptance. Run the checked-in harness against an unlocked, USB-debugging
authorized ARM device using the exact candidate payload and KMediaVlc commit:

```shell
bash scripts/run_android_device_smoke.sh \
  /absolute/android-native-payload \
  /absolute/new-device-smoke-work \
  /absolute/android-sdk/platform-tools/adb \
  DEVICE_SERIAL \
  TESTED_KMEDIAVLC_COMMIT
```

The runner requires a clean checkout at the supplied forty-character commit,
binds every ADB command and Gradle instrumentation invocation to the exact
serial, and accepts only API 28+ `arm64-v8a` or `armeabi-v7a`. It rejects QEMU,
Ranchu, Goldfish, Cuttlefish, and VirtualBox hardware markers before installing
anything. A pre-existing KMediaVlc test package also stops the run, so cleanup
cannot remove an unrelated installation.

Gradle independently verifies the complete payload and legal-evidence graph,
then executes only `VlcAndroidPlaybackInstrumentedTest`. Both automatic
MediaCodec and software-only cases must render moving video and subtitles,
survive two Surface replacements, seek to a distinct frame, preserve EOS,
stop, and release their decoder state. The result verifier rejects skipped,
extra, emulator-labelled, failed, or errored cases and writes
`acceptance.json`, binding the exact commit, upstream revisions, device build,
four runtime-library hashes, complete payload-tree hash, and JUnit hash. Only
that JSON and the JUnit XML are retained; full device logs are not copied. The
temporary test package is removed after every outcome.

Building the APK or passing on an emulator does not satisfy this gate. Keep the
physical device awake and visible for the screenshot-based video/subtitle
checks, and retain the generated evidence with the release review.

### Verified source-build evidence

On 2026-08-10 the exact pinned recipe completed on macOS with NDK `29.0.14206865` and CMake
`4.1.2` for both publication ABIs. The ARM64 audit recorded 307 VLC modules, 62 contrib
archives, four NDK toolchain archives, and three VLC core archives. ARMv7 recorded 305, 62,
four, and three respectively. Both final libraries expose the required core/JNI symbols, have
only the closed Android system `DT_NEEDED` set, and use 16 KiB `LOAD` alignment. The stripped
two-ABI payload also passed the actual Gradle AAR inventory and lint/test gate.

On 2026-08-10 the real stripped ARM64 payload also passed two instrumented playback cases on an
Android API 35 ARM64 emulator. The automatic case observed VLC's MediaCodec decoder thread; the
software-only case verified that thread remained absent. Both cases rendered moving video and the
separate subtitle plane into real `SurfaceView`/ANativeWindow outputs, preserved playback position
across two detach/replace/reattach cycles, sought to a distinct frame, stopped, closed, and left no
MediaCodec decoder thread behind. The fixture is deterministic and hash-checked in the test APK.

The NDK source packager and independent verifier were also exercised against those exact upstream
Git identities. The deterministic candidate contained 19,839 tracked files: the complete 195-file
`llvm_android` tree plus 19,644 selected LLVM files (135,808,087 uncompressed source bytes). The
resulting gzip was about 20 MiB and its standalone and Gradle verifiers agreed on one SHA-256. This
candidate is retained outside Git as build evidence; the final release archive must be regenerated
for the final tested KMediaVlc commit.

These counts now match the exact source allowlist: 62 archive paths resolve to 54 contrib source
components and 55 source tarballs (TagLib also consumes the header-only utfcpp source). The
path-free reports are therefore promoted only to
`candidate-source-mapped-license-review-pending`. This state proves archive-to-source and raw
license-evidence closure, but not the final linked-member SPDX conclusion, packaged notice
completeness, promotion of the final release-bound NDK package to the legal manifest, or release
eligibility. Native binaries remain
external release inputs and are not committed to this repository.

An audited payload can be supplied to Gradle with
`-PkmediaVlcAndroidNativePayloadDirectory=/path/to/payload`. Publication additionally requires
the manifest to say `releaseEligible=true`, the exact complete corresponding-source archive and its
VLC/libvlcjni/contrib/audit inputs, the independently verified NDK archive, both exact NDK source
checkouts, and the matching `recipeRevision`.

## Publication gates still open

- pass the fail-closed harness on representative physical ARM hardware and retain its hash-bound
  `acceptance.json` with the final tested commit;
- review and approve the exact module lists and linked members emitted by the fail-closed audits,
  then bind approved SPDX expressions and complete notices to the recorded source/archive hashes;
- promote the NDK component in the final legal manifest to `corresponding-source-mapped` only after
  retaining the independently verified archive for that exact release commit;
- regenerate, independently verify, and retain both source archives for the final tested commit.

Until all three gates pass, the module is useful for API and ABI integration work but cannot be
published as a bundled runtime.
