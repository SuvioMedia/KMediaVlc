<!-- SPDX-License-Identifier: LGPL-2.1-or-later -->

# Release procedure

KMediaVlc releases are deliberately two-stage:

1. build, test, audit, and publish an immutable GitHub release;
2. sign and submit that existing release to Maven Central.

The Central credentials are the only secret part of stage two. Stage one also
requires reviewed native evidence; tokens cannot replace that review.

## Native release inputs

Every release is atomic across the supported runtime matrix:

- Windows x64;
- Linux x64 and Linux ARM64;
- macOS ARM64;
- Android ARM64 and ARMv7.

The desktop targets are packaged into one universal JAR; Android is a separate
AAR with the same version. The immutable release workflow requires one
successful, commit-bound audit run for Windows, Linux, macOS, and Android. A
missing target or audit artifact stops the whole release.

For Windows x64, retain all of the following from the same source build:

- a clean KMediaVlc checkout and its forty-character commit;
- VLC revision `b5536cdea24b313ba9215eacfbd7fa3295d7f3ee`;
- output of `scripts/build_vlc_windows.sh`, never a VideoLAN nightly;
- a decoder-only Windows graph with Meson `stream_outputs=false` and
  `videolan_manager=false`, matching the contrib `--disable-sout` profile;
- a stripped Meson `runtime`-tag install, without headers, import libraries, or
  other development-only files;
- the KMediaVlc bridge built against headers from that exact VLC revision;
- an exact JSON inventory for every staged file, including component, SPDX
  license, dynamic linkage, source location, role, and target;
- complete corresponding source for VLC, the bridge, and every bundled native
  dependency, plus the build recipe and relinking instructions;
- successful GPU push, CPU pull, HDR, lifecycle, and clean-machine smoke tests.

The release inventory is a reviewed input, not a generated license guess. A
new or renamed DLL/plugin stops publication until its provenance and license
are reviewed. Test media and the raw source-build audit candidate are never
release payloads.

For the first build of a revision, dispatch `Build source VLC Windows audit
candidate` with the exact tested KMediaVlc commit. It runs the pinned VideoLAN
LLVM/MinGW UCRT container, builds VLC and its contribs from source, then loads
the resulting DLLs and an MSVC-built bridge on a native `windows-2022` runner.
Wine is limited to Meson's cross-executable sanity probe during the upstream
cross-build; it is not the runtime test environment. The workflow retains
separate seven-day artifacts for the reviewed corresponding source, native
link metadata, exact tested runtime candidate, and native Windows test
results. This workflow cannot create a tag, release, or Maven deployment.
Standard hosted runners have no physical HDR display, so separate hardware
HDR evidence remains mandatory.
Review its complete DLL/plugin inventory and upstream licenses before using
any of those bytes as release inputs.

Dispatch `Linux libVLC source validation` for the same exact commit. Both
matrix jobs must complete on their native x64/ARM64 runners, retain the closed
runtime inventory and contrib source inputs, and play a real CPU-pull frame.
Policy approval additionally requires the separate physical render-node,
DMA-BUF/fence, normal consumer, and VR-projection acceptance described in
`docs/LINUX.md`.

Dispatch `macOS libVLC source audit` for that commit. It must retain the
relocated ARM64 runtime, its Mach-O inventory, contrib source inputs, real
CPU-pull playback, and real IOSurface-generation evidence. Policy approval also
requires KMediaPlayer Metal consumption and representative physical-display
acceptance described in `docs/MACOS.md`.

Dispatch `Android libVLC source and HDR release audit` from `main` on a
self-hosted Linux x64 runner labelled `kmediavlc-android-hdr` with exactly one
authorized physical HDR Android device. That workflow builds both ABIs from
the pinned sources, produces and independently reopens both source archives,
builds the AAR, and requires all three physical MediaCodec/software/lifecycle/
HDR tests. It cannot approve the SPDX review by itself.

## Android NDK source closure

The Android candidate has an additional immutable source artifact for the four statically linked
NDK runtime archives. Generate it with `scripts/package_android_ndk_source.py` from the exact
`llvm-project` and `llvm_android` commits recorded in
`compliance/policy/android-static-components.json`, then reopen it with
`scripts/verify_android_ndk_source_archive.py`. The final invocation must use the release version,
the final tested KMediaVlc commit, and that commit's Unix timestamp; an earlier audit candidate is
not a release input.

Android publication requires all four properties together:

- `kmediaVlcAndroidNdkSourceArchive`;
- `kmediaVlcAndroidLlvmProjectSourceDirectory`;
- `kmediaVlcAndroidLlvmAndroidSourceDirectory`;
- `recipeRevision`.

Gradle repeats the independent Git-tree/blob verification before publication and attaches the
archive with classifier `android-ndk-source`. This artifact supplements the complete Android
`kmediaVlcAndroidCorrespondingSourceArchive`; it does not replace VLC, libvlcjni, contrib,
KMediaVlc, and relinking sources. The legal manifest must still be explicitly approved and its NDK
component promoted to `corresponding-source-mapped` for the exact release inputs.

## Android complete corresponding source

After the NDK artifact is verified, run `scripts/package_android_corresponding_source.py` with the
clean final KMediaVlc, VLC, and libvlcjni checkouts; VLC's contrib tarball directory; the real legal
manifest; both ABI link-audit reports; and that NDK archive. Use the release version, final tested
KMediaVlc commit, and its Unix timestamp. Then reopen the result with
`scripts/verify_android_corresponding_source_archive.py` and the same original inputs.

The resulting archive contains all three complete tracked Git trees, exactly 55 audited contrib
source archives, the NDK source supplement, both ABI reports, the legal manifest, and deterministic
rebuild/checksum metadata. The independent verifier reconstructs the closure from Git and the
external source/evidence files; it does not trust the packager's manifest as an inventory oracle.

Android Gradle verification requires these six properties together:

- `kmediaVlcAndroidCorrespondingSourceArchive`;
- `kmediaVlcAndroidVlcSourceDirectory`;
- `kmediaVlcAndroidLibvlcjniSourceDirectory`;
- `kmediaVlcAndroidContribTarballsDirectory`;
- `kmediaVlcAndroidArm64LinkAudit`;
- `kmediaVlcAndroidArmv7LinkAudit`.

They additionally require the native payload, all four NDK-verification properties listed above,
and the same `recipeRevision`. Gradle runs both independent verifiers before checks or publication
and attaches the complete artifact with classifier `corresponding-source`. Source closure does not
override the separate approved-SPDX, legal-manifest, release-eligibility, or device-test gates.

## Stage the Maven repository

With all four audited desktop payloads available, write a schema-1 matrix JSON
whose sorted targets are `linux-aarch64`, `linux-x86_64`, `macos-aarch64`, and
`windows-x86_64`; each entry identifies its absolute staging directory and
inventory file. Publish into a new empty directory. The source-offer URL must
identify the union corresponding-source asset of the same future
`v<version>` release.

```text
./gradlew --no-daemon \
  -PpublicationVersion=<version> \
  -PkmediaVlcNativeMatrix=<complete-desktop-matrix.json> \
  -PkmediaVlcSourceOffer=https://github.com/SuvioMedia/KMediaVlc/releases/download/v<version>/kmedia-vlc-<version>-corresponding-source.tar.gz \
  -PrecipeRevision=<tested-kmediavlc-commit> \
  -PcorrespondingSourceArchive=<corresponding-source.tar.gz> \
  -PreleaseRepository=<empty-maven-directory> \
  :runtime-desktop:publishMavenPublicationToReleaseRepository
```

Then close and normalize the repository contract:

```text
python3 scripts/build_maven_central_bundle.py normalize \
  --staging <maven-directory> --version <version>
```

After the matching Android publication is staged into the same repository, the
normalized repository must contain exactly 11 base files: five for
`io.github.shusek:kmedia-vlc-runtime-desktop:<version>` and six for
`io.github.shusek:kmedia-vlc-runtime-android:<version>`. The Android coordinate
adds its AAR and NDK-source classifier to the usual POM, sources, Javadoc, and
complete corresponding source. Create the deterministic, safely re-openable
release asset:

```text
python3 scripts/package_release_repository.py \
  --staging <maven-directory> --version <version> \
  --epoch <tested-commit-unix-time> \
  --output kmedia-vlc-<version>-maven-repository.tar.gz
```

The command prints the SHA-256 entry for the asset. The archive always has one
`maven/` root and contains only the closed two-coordinate, 11-file contract.

## Create the GitHub release

Collect at least:

- `kmedia-vlc-<version>-maven-repository.tar.gz`;
- the desktop-union and Android corresponding-source archives plus the Android
  NDK source supplement;
- standalone Windows x64, Linux x64, Linux ARM64, and macOS ARM64 runtimes;
- the exact two-ABI Android AAR;
- the reviewed component inventory and build/test evidence;
- `NOTICE`, `THIRD_PARTY_NOTICES.md`, `docs/LICENSING.md`, and
  `docs/RELINKING.md`.

Generate `SHA256SUMS` over every release asset, create tag `v<version>` at the
tested KMediaVlc commit, and create a non-draft public GitHub release. RC
versions are prereleases. Do not replace an asset after publishing the release;
issue another version instead.

After every source-built graph and physical gate has been reviewed, set the
Windows, Linux, macOS, and Android policy review states to `approved`, commit
the exact policies and notices, and rerun all four audits for that approved
commit. Then dispatch **Create immutable multiplatform KMediaVlc release** with
the version, exact commit, and the four successful audit run IDs. The workflow
reopens every payload and source archive, assembles the complete desktop JAR,
requires byte equality with the physically tested Android AAR, closes both
Maven coordinates, and creates the immutable tag and prerelease. It never
guesses licenses or replaces an existing release.

## Publish to Central and verify

Follow `docs/MAVEN-CENTRAL.md`. For the first release choose `USER_MANAGED`,
inspect the validated deployment in Central Portal, and publish it there. Once
Central reports `PUBLISHED`, resolve the POM, JAR, sources, Javadoc, and
corresponding-source classifier from Maven Central and run a clean KMediaPlayer
consumer smoke using only public repositories.

There is no overwrite or rollback. If consumer verification fails, leave the
published version intact, fix the code/evidence, and release the next version.
