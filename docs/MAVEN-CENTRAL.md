<!-- SPDX-License-Identifier: LGPL-2.1-or-later -->

# Maven Central publication

KMediaVlc uses the Central Portal Publisher API. Gradle never receives Central
credentials or a signing key. A protected GitHub Actions job downloads the
staged Maven repository from an existing public release, verifies its SHA-256,
removes only Gradle-generated metadata, signs the closed artifact inventory,
and submits one deterministic bundle.

The public GitHub release is always created first. Its tag, Maven repository,
corresponding source, native evidence, notices, and `SHA256SUMS` are the release
record. Maven Central is a second distribution channel for those exact bytes;
it is never allowed to build or substitute a native runtime.

## One-time Central Portal setup

1. Sign in to [Central Portal](https://central.sonatype.com/) and confirm that
   the `io.github.shusek` namespace
   is verified for the account that will publish KMediaVlc. Existing ownership
   used by other `io.github.shusek` artifacts can be reused.
2. [Generate a Central Portal user token](https://central.sonatype.org/publish/generate-portal-token/).
   The generated username and password are token values, not the interactive
   Central account password.
3. Create or select a password-protected OpenPGP signing key. Publish its public
   key to a [keyserver supported by Central](https://central.sonatype.org/publish/requirements/gpg/)
   and export the private key in ASCII-armored form. The workflow forces the
   primary signing key rather than an incompatible signing subkey.
4. In GitHub, create an environment named `maven-central`. Require a reviewer;
   for the first release, keep self-approval disabled if another maintainer is
   available.
5. Add these environment secrets, preserving newlines in the armored key:

   - `MAVEN_CENTRAL_USERNAME` — Central Portal token username;
   - `MAVEN_CENTRAL_PASSWORD` — Central Portal token password;
   - `MAVEN_SIGNING_KEY` — complete armored private PGP key;
   - `MAVEN_SIGNING_PASSWORD` — private-key passphrase.

No token belongs in `gradle.properties`, repository variables, release assets,
or local source files. Rotating the Central token does not change a published
artifact. Losing the PGP private key requires using a new published signing key
for future versions; existing versions remain immutable.

Useful local commands, where `<fingerprint>` is the forty-character primary
key fingerprint:

```shell
gpg --armor --export-secret-keys <fingerprint>
gpg --keyserver keyserver.ubuntu.com --send-keys <fingerprint>
```

The first command's complete output is the value of `MAVEN_SIGNING_KEY`; do not
save or paste it into the repository.

After creating the GitHub environment, the CLI can prompt for the three short
values and stream the private key without placing it in shell history:

```shell
gh secret set MAVEN_CENTRAL_USERNAME --env maven-central --repo SuvioMedia/KMediaVlc
gh secret set MAVEN_CENTRAL_PASSWORD --env maven-central --repo SuvioMedia/KMediaVlc
gh secret set MAVEN_SIGNING_PASSWORD --env maven-central --repo SuvioMedia/KMediaVlc
gpg --armor --export-secret-keys <fingerprint> | \
  gh secret set MAVEN_SIGNING_KEY --env maven-central --repo SuvioMedia/KMediaVlc
```

## Publishing an existing release

Run **Publish existing KMediaVlc release to Maven Central** manually and enter
the version without the `v` prefix. Use `USER_MANAGED` for the first release.
The workflow stops successfully in Central's `VALIDATED` state, where a human
can inspect the deployment in Central Portal. Its GitHub step summary includes
the deployment ID. Before clicking Publish, run the clean KMediaPlayer consumer
smoke against Central's authenticated
[manual-testing repository](https://central.sonatype.org/publish/publish-portal-api/#manually-testing-a-deployment-bundle).
After the first release is confirmed, `AUTOMATIC` may be used for subsequent
immutable versions.

The workflow refuses:

- a missing, draft, untagged, or `SNAPSHOT` release;
- a Maven repository whose archive does not match `SHA256SUMS`;
- symlinks, path traversal, extra coordinates, missing source/Javadoc, or
  missing corresponding source;
- any unsigned artifact or unexpected file in the Central bundle;
- resubstitution of stock VLC nightlies for the audited source-built payload.

A failed or abandoned Central deployment is not repaired by replacing release
assets or reusing the version. Fix the release pipeline and publish a new
SemVer/RC. Central and GitHub versions are immutable.

## Public coordinate

```kotlin
dependencies {
    implementation("io.github.shusek:kmedia-vlc-runtime-desktop:<version>")
    // Android source sets use the matching release version:
    implementation("io.github.shusek:kmedia-vlc-runtime-android:<version>")
}
```

The desktop coordinate is one universal JAR containing Windows x64, Linux x64,
Linux ARM64, and macOS ARM64 resources. The Android coordinate is a two-ABI AAR
for ARM64 and ARMv7. The Central bundle closes both coordinates together: 11
base files before signing, including both corresponding-source archives and the
Android NDK source supplement. A partial Windows-only, desktop-only, or
Android-only bundle is rejected.
