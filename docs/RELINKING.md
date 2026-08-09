<!-- SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary -->

# Replacing libVLC

Published runtimes keep libVLC, libvlccore, plugins, and their native
dependencies as replaceable shared libraries. The bridge validates ABI major,
runtime identity, and capabilities, not a private filesystem path.

Release evidence contains the exact source, build recipe, configuration,
object/link inputs where required, and instructions to rebuild a compatible
runtime. A replacement must retain the stable KMediaVlc bridge ABI and must
not mix plugin directories across libVLC majors.
