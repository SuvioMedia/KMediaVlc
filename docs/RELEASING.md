<!-- SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary -->

# Release procedure

KMediaVlc releases are deliberately two-stage:

1. build, test, audit, and publish an immutable GitHub release;
2. sign and submit that existing release to Maven Central.

The Central credentials are the only secret part of stage two. Stage one also
requires reviewed native evidence; tokens cannot replace that review.

## Native release inputs

For every Windows target, retain all of the following from the same source
build:

- a clean KMediaVlc checkout and its forty-character commit;
- VLC revision `b5536cdea24b313ba9215eacfbd7fa3295d7f3ee`;
- output of `scripts/build_vlc_windows.sh`, never a VideoLAN nightly;
- the KMediaVlc bridge built against headers from that exact VLC revision;
- an exact JSON inventory for every staged file, including component, SPDX
  license, dynamic linkage, source location, role, and target;
- complete corresponding source for VLC, the bridge, and every bundled native
  dependency, plus the build recipe and relinking instructions;
- successful GPU push, CPU pull, HDR, lifecycle, and clean-machine smoke tests.

The release inventory is a reviewed input, not a generated license guess. A
new or renamed DLL/plugin stops publication until its provenance and license
are reviewed. The current stock-nightly test fixture is never release input.

## Stage the Maven repository

With the audited paths available, publish into a new empty directory. The
source-offer URL must identify the corresponding-source asset of the same
future `v<version>` release.

```text
./gradlew --no-daemon \
  -PpublicationVersion=<version> \
  -PkmediaVlcNativeStagingDirectory=<audited-runtime-directory> \
  -PkmediaVlcNativeInventory=<reviewed-inventory.json> \
  -PkmediaVlcNativeTarget=windows-x86_64 \
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

The normalized repository must contain exactly five files for
`io.github.shusek:kmedia-vlc-runtime-desktop:<version>`: primary JAR, POM,
sources JAR, Javadoc JAR, and corresponding-source archive. Create the
deterministic, safely re-openable release asset:

```text
python3 scripts/package_release_repository.py \
  --staging <maven-directory> --version <version> \
  --epoch <tested-commit-unix-time> \
  --output kmedia-vlc-<version>-maven-repository.tar.gz
```

The command prints the SHA-256 entry for the asset. The archive always has one
`maven/` root and contains only the closed five-file coordinate.

## Create the GitHub release

Collect at least:

- `kmedia-vlc-<version>-maven-repository.tar.gz`;
- `kmedia-vlc-<version>-corresponding-source.tar.gz`;
- the reviewed component inventory and build/test evidence;
- `NOTICE`, `THIRD_PARTY_NOTICES.md`, `docs/LICENSING.md`, and
  `docs/RELINKING.md`.

Generate `SHA256SUMS` over every release asset, create tag `v<version>` at the
tested KMediaVlc commit, and create a non-draft public GitHub release. RC
versions are prereleases. Do not replace an asset after publishing the release;
issue another version instead.

Before enabling a fully automatic stage-one release workflow, the first
source-built VLC output and its per-file inventory must be reviewed and added
to the release policy. This repository intentionally does not contain a
workflow that guesses licenses from DLL names.

## Publish to Central and verify

Follow `docs/MAVEN-CENTRAL.md`. For the first release choose `USER_MANAGED`,
inspect the validated deployment in Central Portal, and publish it there. Once
Central reports `PUBLISHED`, resolve the POM, JAR, sources, Javadoc, and
corresponding-source classifier from Maven Central and run a clean KMediaPlayer
consumer smoke using only public repositories.

There is no overwrite or rollback. If consumer verification fails, leave the
published version intact, fix the code/evidence, and release the next version.
