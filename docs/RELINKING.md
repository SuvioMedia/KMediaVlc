<!-- SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary -->

# Replacing libVLC

Published runtimes keep the bridge, libVLC, libvlccore, and VLC plugins as
replaceable DLLs. VLC's allowlisted contrib libraries are statically linked
inside the affected plugin DLLs; the per-file inventory records their
canonical SPDX conjunction and the corresponding-source archive contains the
source and exact build recipe needed to rebuild and relink those plugins. The
bridge validates ABI major, runtime identity, and capabilities, not a private
filesystem path.

Release evidence contains the exact source, build recipe, configuration,
object/link inputs where required, and instructions to rebuild a compatible
runtime. A replacement must retain the stable KMediaVlc bridge ABI and must
not mix plugin directories across libVLC majors.

## Android candidate

Android packages `libvlc.so` and `libkmediavlc_android.so` as two separate
shared libraries for each ABI. The proprietary bridge has a dynamic
`DT_NEEDED` edge to `libvlc.so`; no libVLC object code is copied into the
bridge. VLC's Android build folds its selected modules and contribs into
`libvlc.so`, so the corresponding-source bundle must include both pinned VLC
and libvlcjni checkouts, every contrib source input, the exact module list,
and the NDK 29 build/relink recipe.

The NDK part of that recipe is now source-mapped to Clang `r563880c`, LLVM
commit `386af4a5c64ab75eaee2448dc38f2e34a40bfed0`, and Android
`llvm_android` build/patch commit `1dab3288f660d43a6cb2479107e2b54b3ab0a2a1`.
The audit records the exact source subtrees used by each of the four linked
runtime archives and binds the r29 manifest plus macOS/Linux prebuilt tags.
This records provenance; it is not a substitute for packaging those sources.

The relink recipe also includes KMediaVlc's committed `libvlcjni` policy patch.
For the Android profile that patch disables Blu-ray/BD-J and records the
limitation in the closed build recipe.
Each build creates a linker map and a path-free audit that binds the patch and
generated module-array hashes to the exact static archives and object members
that entered `libvlc.so`. Those reports must be reviewed together with the
corresponding contrib sources and notices; they are not a substitute for the
sources or for the full relinking instructions.

The candidate AAR carries a separate legal-evidence manifest and the exact raw
notice inputs selected from those source archives. It binds both ABI report
hashes and rejects any file mismatch, but publication still requires an
approved effective SPDX conclusion, an NDK status of
`corresponding-source-mapped`, and the complete source/relinking bundle.

The pinned recipe has been exercised successfully for ARM64 and ARMv7 and the
resulting stripped payload has passed the real AAR inventory gate. Publication
still requires turning the generated per-ABI archive graphs into reviewed,
retained corresponding-source evidence.

An application can replace the pair by substituting a rebuilt AAR (or its
matching `jni/<abi>` entries) while retaining bridge ABI 1 and the exact VLC
4 callback ABI. This remains documentation for an audit candidate, not a
release promise, until `docs/ANDROID.md` lists no open publication gates.
