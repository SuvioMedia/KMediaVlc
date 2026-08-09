<!-- SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary -->

# Native client ABI

`include/kmediavlc_client.h` is the stable boundary consumed by KMediaPlayer.
Platform implementations are built against the exact pinned libVLC 4 header
and included only in verified release payloads.

The ABI deliberately exposes notification plus latest-frame acquisition. It
does not expose libVLC preview structs to Kotlin/JVM and does not accept a
native child window.
