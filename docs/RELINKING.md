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
