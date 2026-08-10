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
