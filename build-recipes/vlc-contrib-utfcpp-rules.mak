# SPDX-License-Identifier: LGPL-2.1-or-later

# VLC already pins and verifies the utf8cpp archive next to TagLib, but does
# not expose it as an independently installable contrib. libEBML 1.4.6 uses
# utf8cpp directly, so install the header-only package without pulling TagLib
# or allowing libEBML's CMake fallback to fetch from the network.

PKGS += utfcpp
DEPS_ebml += utfcpp $(DEPS_utfcpp)

utfcpp: utfcpp-$(UTFCPP_VERSION).tar.gz .sum-utfcpp
	$(UNPACK)
	$(MOVE)

UTFCPP_CONF := \
	-DUTF8_INSTALL=ON \
	-DUTF8_SAMPLES=OFF \
	-DUTF8_TESTS=OFF

.utfcpp: utfcpp toolchain.cmake
	$(CMAKECLEAN)
	$(HOSTVARS_CMAKE) $(CMAKE) $(UTFCPP_CONF)
	+$(CMAKEBUILD)
	$(CMAKEINSTALL)
	touch $@
