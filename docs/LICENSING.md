<!-- SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary -->

# Licensing boundary

KMediaVlc does not infer license eligibility from the name `libvlc`. Each
release consumes an exact component inventory containing the source revision,
SPDX expression, binary hash, linkage, and corresponding-source location for
every library and plugin.

The packager accepts `DYNAMIC` linkage for every executable payload file and
`NONE` only for data and legal files. A bundled DLL may contain only the
separately audited static contribs listed in its canonical SPDX conjunction;
their corresponding source and relinking recipe remain mandatory. An
uninventoried static contrib or a static libVLC/bridge payload is rejected.

The release gate rejects:

- GPL, AGPL, nonfree, or unknown license expressions;
- plugins absent from the allowlisted build policy;
- binaries without exact source and notice entries;
- static linkage not accompanied by complete relinking material;
- stock/nightly VLC archives presented as release payloads;
- mismatched `libvlc`, `libvlccore`, plugin, or bridge revisions.

The Maven runtime includes `LICENSE`, `NOTICE`, third-party notices, license
texts, a SHA-256 inventory with per-file SPDX/source/linkage metadata, and a
corresponding-source offer. Dynamic replacement instructions are part of
every release. Publication cannot consume an arbitrary payload directory; it
depends on the same fail-closed packager that creates the verified resources.

On Android, the AAR inventory is exactly two shared libraries per ABI. The
client bridge remains proprietary and dynamically links to `libvlc.so`.
VideoLAN's `libvlcjni.so` and Java wrapper are not distributed. The candidate
`libvlc.so` internally contains statically linked VLC modules and contribs;
therefore the upstream `--license a` switch is only a starting filter, not
proof of eligibility. Publication stays disabled until the generated module
symbols and every linked archive are mapped to allowed licenses and complete
corresponding source.

The source builder applies a committed patch to a transient copy of the exact
pinned `libvlcjni` checkout. It deterministically sorts the module input and
excludes the known VLC modules that declare GPL-2.0-or-later. It also keeps the
Android-only Blu-ray/BD-J feature outside this runtime profile. The per-ABI
audit then uses the final linker map, not the filesystem alone: it records every
archive member that actually contributes to `libvlc.so`, verifies each selected
module archive carries VLC's exact LGPL marker, rejects a GPL marker in every
module and the final library, and binds the report to the patch, generated
module array, and link map hashes. The final link may garbage-collect unused
LGPL metadata strings, so eligibility derives from the exact archive/object
graph rather than from a retained string alone. This is release evidence
tooling; its unreviewed output leaves the final effective SPDX expression unset
and does not by itself make a candidate publishable.

The completed pinned source build currently reports 62 actually linked contrib
archives per Android ABI, plus four NDK runtime archives and the three VLC core
archives. All selected VLC module archives passed the compiled marker check. A
closed policy now maps those 62 paths to 54 contrib components and 55 exact
source tarballs and rejects any missing or extra archive. The report hashes each
source input, 83 selected in-archive license/patent/source-notice records, and
the NDK distribution notices and Clang provenance files. The four NDK runtime
archives now map to exact `llvm-project` and Android build/patch commits and to
their source subtrees. It records conservative candidate SPDX sets but
keeps every component at `pending-linked-member-review`, so the report can be
promoted only to `candidate-source-mapped-license-review-pending`. Each linked
member still needs reviewed SPDX and packaged notice metadata, and the recorded
NDK revisions still need a retained package for the final tested release commit
and explicit promotion in the legal manifest before that manifest can advance.

The repository now has a deterministic Android NDK source packager and a separate verifier. The
packager includes the complete pinned `llvm_android` Git tree and the closed LLVM runtime source
and build-support subtrees. The verifier reconstructs the expected inventory from both exact Git
trees and compares every packaged member to its Git blob as well as its recorded SHA-256. A real
candidate from the pinned revisions passed both the standalone and Gradle gates. This does not
silently change the existing legal manifest: the final release-bound archive must be retained and
reviewed before its NDK component can be promoted to `corresponding-source-mapped`.

The source builder stages those raw records as a separate hash-bound legal bundle only after the
two ABI audit reports have identical component evidence. The AAR verifier rejects any missing,
extra, symbolic, resized, or rehashed legal file and binds the bundle to the current static
component policy. Candidate builds package the evidence for review, but Maven publication also
requires `reviewStatus=approved`, a non-null effective SPDX expression, and
`sourceStatus=corresponding-source-mapped` for the NDK runtime component.
