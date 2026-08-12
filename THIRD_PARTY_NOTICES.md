<!-- SPDX-License-Identifier: LGPL-2.1-or-later -->

# Third-party notices

KMediaVlc is an optional LGPL-2.1-or-later client and distribution boundary for
libVLC 4. The stable `native/include/kmediavlc_client.h` client API is ISC.
VideoLAN VLC, its plugins, native dependencies, and compiler runtime code keep
their upstream terms.

## VideoLAN VLC / libVLC

The Windows runtime and Apple audit candidates are built from VideoLAN VLC revision `b5536cdea24b313ba9215eacfbd7fa3295d7f3ee`. VLC/libVLC and the selected playback modules are distributed under LGPL-2.1-or-later except for the additional direct-source terms recorded in each closed module policy. The LGPL-2.1 text is included as `LICENSES/LGPL-2.1.txt`.

Official source: https://code.videolan.org/videolan/vlc

A release contains no stock VLC nightly. It is eligible only after the exact Meson target graph, linker commands, installed files, dependency source archives, toolchain runtime inputs, licenses, and hashes have all passed the repository policy. GPL, AGPL, nonfree, unknown-license, and uninventoried modules are rejected.

Each release publishes a version-bound complete corresponding-source archive alongside the binary and Maven artifacts. The native manifest identifies that immutable source asset and the exact KMediaVlc and VLC revisions used to build it.

## VideoLAN libvlcjni build system

The Android candidate uses the build machinery from VideoLAN `libvlcjni` revision
`a8d53a9151d7e4a9a5dfd0a5eb1cd92669afdc21`, under LGPL-2.1-or-later. Neither its
Java wrapper classes nor `libvlcjni.so` are distributed by KMediaVlc. The pinned
scripts and makefiles are corresponding-source build inputs used to combine the
selected VLC modules into `libvlc.so`.

Official source: https://code.videolan.org/videolan/libvlcjni

The Android source-build candidate uses the upstream `a` license profile
(LGPL-2.1-compatible contribs plus advertising-clause dependencies), static C++,
and no prebuilt contribs or published AAR. Its exact 62-archive link graph is now
closed to 54 contrib components and 55 pinned source tarballs; generated audits
hash those source inputs, 83 selected in-archive license/patent/source-notice
records, the NDK distribution notices, and its Clang source-provenance files for
both packaged ABIs. The NDK map pins Clang `r563880c`, LLVM commit
`386af4a5c64ab75eaee2448dc38f2e34a40bfed0`, and Android build/patch revision
`1dab3288f660d43a6cb2479107e2b54b3ab0a2a1`. Deterministic packaging tooling
now closes the selected source members to their exact Git blobs and rejects a
different checkout or modified archive. The complete Android corresponding-source
packager additionally retains the exact KMediaVlc, VLC, and libvlcjni Git trees,
all 55 audited contrib tarballs, the verified NDK source supplement, the legal
manifest, and both path-free ABI reports in one version- and commit-bound archive.
A separate verifier reconstructs that closure from the original Git objects and
external evidence. Conservative candidate SPDX sets remain marked
`pending-linked-member-review`. The runtime remains release-ineligible until that
review, packaged notice completion, final release-bound archive retention, and
device lifecycle evidence are approved.

Candidate AARs include the exact selected evidence inputs under
`assets/kmediavlc/legal/ANDROID_STATIC/` together with a manifest that binds both ABI audits and
all file hashes. Inclusion makes the raw evidence inspectable; it does not change the candidate
SPDX sets into an approved aggregate license conclusion.

## Windows x86_64 playback dependency inventory

The following closed inventory is derived from the pinned contrib inputs. Publication remains blocked until the matching native link audit changes both review states to `approved` for the exact commit.

| Component | Version | SPDX license | Reviewed source input | Included notice/terms |
| --- | --- | --- | --- | --- |
| ffmpeg | 8.1.2 | LGPL-2.1-or-later | `ffmpeg-8.1.2.tar.xz` | `LICENSES/FFmpeg-LICENSE.txt` |
| flac | 1.5.0 | BSD-3-Clause | `flac-1.5.0.tar.xz` | `LICENSES/FLAC-COPYING-XIPH.txt` |
| freetype | 2.13.1 | FTL | `freetype-2.13.1.tar.xz` | `LICENSES/FreeType-FTL.txt` |
| fribidi | 1.0.16 | LGPL-2.1-or-later | `fribidi-1.0.16.tar.xz` | `LICENSES/LGPL-2.1.txt` |
| gmp | 6.3.0 | LGPL-3.0-or-later | `gmp-6.3.0.tar.xz` | `LICENSES/LGPL-3.0.txt` |
| gnutls | 3.8.13 | LGPL-2.1-or-later | `gnutls-3.8.13.tar.xz` | `LICENSES/LGPL-2.1.txt` |
| gnutls-libtasn1 | bundled-3.8.13 | LGPL-2.1-or-later | `gnutls-3.8.13.tar.xz` | `LICENSES/LGPL-2.1.txt` |
| gnutls-libunistring | bundled-3.8.13 | LGPL-3.0-or-later | `gnutls-3.8.13.tar.xz` | `LICENSES/LGPL-3.0.txt` |
| gsm | 1.0-pl22 | TU-Berlin-1.0 | `gsm-1.0-pl22.tar.gz` | `LICENSES/GSM-COPYRIGHT.txt` |
| harfbuzz | 14.2.1 | MIT | `harfbuzz-14.2.1.tar.xz` | `LICENSES/HarfBuzz-COPYING.txt` |
| libass | 0.17.5 | ISC | `libass-0.17.5.tar.xz` | `LICENSES/libass-COPYING.txt` |
| libdvbpsi | 1.3.3 | LGPL-2.1-or-later | `libdvbpsi-1.3.3.tar.bz2` | `LICENSES/LGPL-2.1.txt` |
| libebml | 1.4.6 | LGPL-2.1-or-later | `libebml-1.4.6.tar.xz` | `LICENSES/LGPL-2.1.txt` |
| libgcrypt | 1.12.2 | LGPL-2.1-or-later | `libgcrypt-1.12.2.tar.bz2` | `LICENSES/LGPL-2.1.txt` |
| libgpg-error | 1.56 | LGPL-2.1-or-later | `libgpg-error-1.56.tar.bz2` | `LICENSES/LGPL-2.1.txt` |
| libiconv | 1.18 | LGPL-2.1-or-later | `libiconv-1.18.tar.gz` | `LICENSES/LGPL-2.1.txt` |
| libjpeg-turbo | 3.1.4.1 | IJG AND Zlib | `libjpeg-turbo-3.1.4.1.tar.gz` | `LICENSES/libjpeg-turbo-LICENSE.txt` |
| libmatroska | 1.7.2 | LGPL-2.1-or-later | `libmatroska-1.7.2.tar.xz` | `LICENSES/LGPL-2.1.txt` |
| libogg | 1.3.6 | BSD-3-Clause | `libogg-1.3.6.tar.xz` | `LICENSES/libogg-COPYING.txt` |
| libpng | 1.6.58 | Libpng-2.0 | `libpng-1.6.58.tar.xz` | `LICENSES/libpng-LICENSE.txt` |
| libssh2 | 1.11.1 | BSD-3-Clause | `libssh2-1.11.1.tar.xz` | `LICENSES/libssh2-COPYING.txt` |
| libvorbis | 1.3.7 | BSD-3-Clause | `libvorbis-1.3.7.tar.xz` | `LICENSES/libvorbis-COPYING.txt` |
| libxml2 | 2.15.3 | MIT | `libxml2-2.15.3.tar.xz` | `LICENSES/libxml2-Copyright.txt` |
| nettle | 3.10.2 | LGPL-3.0-or-later | `nettle-3.10.2.tar.gz` | `LICENSES/LGPL-3.0.txt` |
| openjpeg | 2.5.4 | BSD-2-Clause | `openjpeg-2.5.4.tar.gz` | `LICENSES/OpenJPEG-LICENSE.txt` |
| opus | 1.6.1 | BSD-3-Clause | `opus-1.6.1.tar.gz` | `LICENSES/Opus-COPYING.txt` |
| soxr | 0.1.3 | LGPL-2.1-or-later | `soxr-0.1.3-Source.tar.xz` | `LICENSES/SoXR-LICENCE.txt` |
| speexdsp | 1.2.1 | BSD-3-Clause | `speexdsp-1.2.1.tar.gz` | `LICENSES/SpeexDSP-COPYING.txt` |
| zlib | 1.3.2 | Zlib | `zlib-1.3.2.tar.xz` | `LICENSES/zlib-LICENSE.txt` |

## macOS arm64 playback dependency inventory

The macOS audit candidate reuses the reviewed rows above for FFmpeg, FLAC,
FreeType, FriBidi, GSM, HarfBuzz, libass, libdvbpsi, libebml, libiconv,
libjpeg-turbo, libmatroska, libogg, libpng, libvorbis, libxml2, OpenJPEG,
Opus, SoXR, and zlib. Its additional decoder and HDR renderer inputs are:

| Component | Version | SPDX license | Reviewed source input | Included notice/terms |
| --- | --- | --- | --- | --- |
| dav1d | 1.5.4 | BSD-2-Clause | `dav1d-1.5.4.tar.xz` | `LICENSES/Dav1d-COPYING.txt` |
| glad | 2.0.4 | Apache-2.0 AND CC0-1.0 AND MIT | `glad-2.0.4.tar.gz` | `LICENSES/glad-LICENSE.txt` |
| jinja | 3.1.2 | BSD-3-Clause | `jinja-3.1.2.tar.gz` | `LICENSES/Jinja-LICENSE.txt` |
| libplacebo | 5.264.1 | CC0-1.0 AND LGPL-2.1-or-later | `libplacebo-v5.264.1.tar.gz` | `LICENSES/libplacebo-LICENSE.txt` |
| libvpx | 1.16.0 | BSD-3-Clause | `libvpx-1.16.0.tar.gz` | `LICENSES/libvpx-LICENSE.txt` |
| markupsafe | 2.1.1 | BSD-3-Clause | `markupsafe-2.1.1.tar.gz` | `LICENSES/MarkupSafe-LICENSE.txt` |
| vulkan-headers | 1.3.275 | Apache-2.0 AND MIT | `Vulkan-Headers-1.3.275.tar.gz` | `LICENSES/Vulkan-Headers-LICENSE.txt` |

Jinja and MarkupSafe are native build-time generator inputs only. Vulkan-Headers
is a header-only input to libplacebo's stub API; the macOS runtime does not
contain or load Vulkan Loader or glslang.

The macOS candidate is built with Xcode 26.6 (17F113), the macOS 26.5 SDK,
an arm64-only target, and a minimum deployment version of macOS 14.0. Its
component and module review states remain pending; this inventory does not make
the candidate release-eligible.

## iOS arm64 playback dependency inventory

The iOS device and Apple-silicon simulator candidates reuse the applicable
macOS rows above, but use the system iOS iconv implementation instead of
shipping libiconv. The local contrib closure also records the header-only
dependency compiled into libEBML's Matroska path:

| Component | Version | SPDX license | Reviewed source input | Included notice/terms |
| --- | --- | --- | --- | --- |
| utfcpp | 3.2.5 | BSL-1.0 | `utfcpp-3.2.5.tar.gz` | `LICENSES/BSL-1.0.txt` |

Both iOS slices are built with Xcode 26.6 (17F113), the iOS 26.5 SDK, arm64,
and a minimum deployment version of iOS 16.2. The 84 selected VLC plugins,
their application-private XCFramework graph, and real simulator playback have
candidate evidence. Device playback plus the source, link-command, and license
reviews remain open; this inventory does not make the iOS payload
release-eligible.

## Toolchain runtime inputs

Windows VLC is cross-compiled with the pinned official VideoLAN LLVM/MinGW UCRT image `registry.videolan.org/vlc-debian-llvm-ucrt:20260611225331`. Wine is limited to Meson's cross-executable sanity probe. The resulting DLLs and bridge are loaded and tested on a native GitHub `windows-2022` runner.

The link-audit artifact records every reviewed static runtime archive and its SHA-256, plus the exact compiler, linker commands, and upstream toolchain license files. The same toolchain notices and build information are included in the corresponding-source release asset. Publication remains blocked until that archive-level license inventory is approved.

## Build tooling

The Gradle wrapper is distributed under Apache-2.0; its upstream license is retained in `gradle/wrapper/LICENSE`.
