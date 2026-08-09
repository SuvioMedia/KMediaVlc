<!-- SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary -->

# Third-party notices

## VideoLAN VLC / libVLC

KMediaVlc targets the libVLC 4 API from VideoLAN VLC. VideoLAN documents
libVLC as LGPL-2.1-or-later while warning that individual plugins can use
more restrictive licenses. KMediaVlc therefore does not accept a stock VLC
installation or nightly archive as a publishable bundled payload.

Official source: https://code.videolan.org/videolan/vlc

Pinned development revision:
`b5536cdea24b313ba9215eacfbd7fa3295d7f3ee`.

Every release must provide the complete corresponding source and build
recipe for that revision, plus exact notices for every enabled plugin and
native dependency. GPL, AGPL, nonfree, unknown-license, and uninventoryed
modules make a payload ineligible.

The Gradle wrapper is distributed under Apache-2.0; its upstream license is
retained in `gradle/wrapper/LICENSE`.
