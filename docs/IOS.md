<!-- SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary -->

# iOS bundled runtime candidate

KMediaVlc builds libVLC 4 from the pinned VideoLAN revision for iOS 16.2 or
newer. The candidate matrix contains arm64 device and Apple-silicon simulator
slices. Intel simulators and older deployment targets are outside this
contract.

iOS cannot extract and load executable code from a Maven artifact at runtime.
The intended integration therefore mirrors KMediaMpv: a versioned CocoaPods
artifact supplies dynamic XCFrameworks, Xcode embeds the selected slices in the
application's flattened `Frameworks` directory, and the consuming application
signs them. KMediaVlc does not sign release frameworks itself.

The primary `KMediaVlc.framework` exports the stable `kmediavlc_client.h` C ABI.
`KMediaVlcLibVlc.framework` and `KMediaVlcCore.framework` contain the pinned VLC
libraries. Each selected VLC module remains a separate replaceable framework
whose directory and executable follow VLC 4's
`lib<module>_plugin.framework/lib<module>_plugin` loader convention.

## Current transport

The first iOS transport is `CPU_PULL`. libVLC's vmem callbacks publish bounded
RGBA8 frames through the same ownership ABI used by the desktop compatibility
path. The bridge deliberately excludes JNI and the macOS CGL/IOSurface
renderer. Requests for `GPU_PUSH` fail closed until a native iOS GPU transport
and its ownership tests exist.

The candidate selects 84 playback modules from 285 source-built modules. It
includes AudioUnit, VideoToolbox plus software fallback, local and HTTP(S)
input, Matroska/MP4 demuxing, subtitles, and the conversion modules required by
CPU pull. The package contains 87 dynamic frameworks per slice: bridge,
libVLC, core, and the 84 selected plugins.

The arm64 device and simulator slices have been reproduced from a clean
checkout of VLC `b5536cdea24b313ba9215eacfbd7fa3295d7f3ee`. All 87 staged
frameworks in each slice passed the closed Mach-O relocation audit. A real
Apple-silicon simulator then loaded the packaged simulator frameworks through
the application bundle and produced a 64-by-36 RGBA8 CPU-pull frame (`9216`
bytes). The two audited slices were also paired as 87 XCFrameworks, archived
deterministically, and reopened by the independent archive verifier. The normal
release gate correctly rejects that archive while its source/license policies
remain pending. This evidence does not make the payload release-eligible.

## Reproducing one slice

Start with a clean checkout of the exact VLC revision recorded in
`build-recipes/ios.json`. Both output paths must be new absolute paths.

```shell
bash scripts/build_vlc_ios.sh /absolute/vlc-source /absolute/vlc-simulator-build iphonesimulator
bash scripts/build_kmediavlc_ios_bridge.sh /absolute/vlc-source /absolute/bridge-simulator-build iphonesimulator
python3 -B scripts/stage_vlc_ios_frameworks.py \
  --root . \
  --install /absolute/vlc-simulator-build/vlc-iphonesimulator-arm64 \
  --bridge /absolute/bridge-simulator-build/libkmediavlc_bridge.dylib \
  --target ios-simulator-arm64 \
  --output /absolute/ios-simulator-frameworks \
  --report /absolute/ios-simulator-frameworks.json \
  --allow-audit-candidate
```

Use `iphoneos` and `ios-arm64` for the device slice. The source wrapper applies
recorded fixes for Meson cross-build generators and preserves the iOS 16.2
deployment flags that upstream GSM otherwise replaces. It installs the
already-pinned `utfcpp 3.2.5` archive as a local contrib; libEBML is never
allowed to fetch an undeclared dependency through CMake.

## Assembling the CocoaPod payload

After both slice reports pass and the repository is at a clean, immutable
commit, assemble and independently reopen the hash-bound payload:

```shell
python3 -B scripts/assemble_ios_xcframeworks.py \
  --device-frameworks /absolute/ios-device-frameworks \
  --device-report /absolute/ios-device-frameworks.json \
  --simulator-frameworks /absolute/ios-simulator-frameworks \
  --simulator-report /absolute/ios-simulator-frameworks.json \
  --output /absolute/kmedia-vlc-ios-aggregate \
  --archive /absolute/kmedia-vlc-0.1.0-ios-xcframeworks.zip \
  --podspec /absolute/KMediaVlc.podspec \
  --version 0.1.0 \
  --revision 0123456789abcdef0123456789abcdef01234567 \
  --allow-audit-candidate
python3 -B scripts/verify_ios_xcframework_archive.py \
  --archive /absolute/kmedia-vlc-0.1.0-ios-xcframeworks.zip \
  --podspec /absolute/KMediaVlc.podspec \
  --expected-version 0.1.0 \
  --expected-revision 0123456789abcdef0123456789abcdef01234567 \
  --allow-audit-candidate
```

The candidate opt-in is required while either source/license policy remains
pending. Release automation omits that flag, so an unapproved payload fails
closed. The assembler never signs a framework; the consuming application owns
that step.

## Publication gates still open

The iOS payload is not release-eligible yet. Publication remains blocked until
all of the following are checked for both slices:

- clean source-build evidence and exact contrib/archive hashes;
- closed per-framework Mach-O and source/license inventories;
- real device playback and AudioUnit acceptance;
- corresponding-source and downstream LGPL relinking material;
- KMediaPlayer CocoaPods integration using the signed application bundle.

Official VideoLAN nightlies remain useful only for local API experiments and
are never release inputs.
