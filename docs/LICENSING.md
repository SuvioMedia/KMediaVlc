<!-- SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary -->

# Licensing boundary

KMediaVlc does not infer license eligibility from the name `libvlc`. Each
release consumes an exact component inventory containing the source revision,
SPDX expression, binary hash, linkage, and corresponding-source location for
every library and plugin.

The packager accepts `DYNAMIC` linkage for every executable component and
`NONE` only for legal files. Static or omitted linkage is rejected; this keeps
libVLC, libvlccore, plugins, dependencies, and the bridge replaceable without
depending on an undocumented relinking exception.

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
