#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-2.1-or-later

set -euo pipefail

readonly PINNED_REVISION="b5536cdea24b313ba9215eacfbd7fa3295d7f3ee"
readonly PINNED_MESON_VERSION="1.10.0"

if [[ $# -lt 2 || $# -gt 3 ]]; then
    echo "usage: $0 <vlc-source> <absolute-build-directory> [jobs]" >&2
    exit 2
fi

source_directory="$(cd "$1" && pwd -P)"
build_directory="$2"
jobs="${3:-4}"

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "the Linux libVLC runtime must be built on Linux" >&2
    exit 2
fi
if [[ "$build_directory" != /* ]]; then
    echo "build directory must be absolute" >&2
    exit 2
fi
if [[ -e "$build_directory" ]]; then
    echo "build directory must not already exist" >&2
    exit 2
fi
if [[ ! -d "$(dirname "$build_directory")" ]]; then
    echo "build directory parent is missing" >&2
    exit 2
fi
if [[ ! "$jobs" =~ ^[1-9][0-9]*$ ]]; then
    echo "jobs must be a positive integer" >&2
    exit 2
fi

for tool in cc c++ curl git make pkg-config python3 readelf; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "required Linux build tool is missing: $tool" >&2
        exit 1
    fi
done
for package in egl fontconfig gbm glesv2 libdrm libpulse; do
    if ! pkg-config --exists "$package"; then
        echo "required Linux build package is missing: $package" >&2
        exit 1
    fi
done

actual_revision="$(git -C "$source_directory" rev-parse HEAD)"
if [[ "$actual_revision" != "$PINNED_REVISION" ]]; then
    echo "VLC source revision mismatch: $actual_revision" >&2
    exit 1
fi
if [[ -n "$(git -C "$source_directory" status --porcelain --untracked-files=no)" ]]; then
    echo "VLC source checkout contains tracked modifications" >&2
    exit 1
fi

case "$(uname -m)" in
    x86_64|amd64)
        architecture="x86_64"
        ;;
    aarch64|arm64)
        architecture="aarch64"
        ;;
    *)
        echo "unsupported Linux architecture; expected x86_64 or aarch64" >&2
        exit 2
        ;;
esac
host_triplet="$(cc -dumpmachine)"
case "$architecture:$host_triplet" in
    x86_64:x86_64*-linux-gnu*|aarch64:aarch64*-linux-gnu*) ;;
    *)
        echo "native Linux compiler target is not the requested GNU architecture: $host_triplet" >&2
        exit 1
        ;;
esac

mkdir "$build_directory"
readonly tools_directory="$build_directory/tools"
readonly contrib_build_directory="$build_directory/contrib-build"
readonly contrib_prefix="$build_directory/contrib-prefix"
readonly meson_build_directory="$build_directory/vlc-meson"
readonly install_directory="$build_directory/vlc-linux-$architecture"
mkdir "$tools_directory" "$contrib_build_directory"

# Build the exact helper-tool graph recorded by the pinned VLC checkout. In
# particular, this supplies Meson 1.10.0 even when the host distribution ships
# another version. The source checkout remains the authority for tool hashes.
make -C "$source_directory/extras/tools" \
    -f "$source_directory/extras/tools/tools.mak" \
    TOOLS="$source_directory/extras/tools" \
    PREFIX="$tools_directory" \
    -j"$jobs" \
    .buildmeson .buildninja
export PATH="$tools_directory/bin:/usr/local/bin:/usr/bin:/bin"
if [[ "$(meson --version)" != "$PINNED_MESON_VERSION" ]]; then
    echo "pinned VLC Meson version was not selected" >&2
    exit 1
fi

readonly source_date_epoch="$(git -C "$source_directory" show -s --format=%ct "$PINNED_REVISION")"
export LC_ALL=C
export TZ=UTC
export SOURCE_DATE_EPOCH="$source_date_epoch"
unset \
    AR AS CC CFLAGS CPPFLAGS CXX CXXFLAGS LD LDFLAGS NM OBJCOPY OBJDUMP \
    PKG_CONFIG_PATH PKG_CONFIG_LIBDIR PKG_CONFIG_SYSROOT_DIR RANLIB STRIP \
    CPATH C_INCLUDE_PATH CPLUS_INCLUDE_PATH LIBRARY_PATH LD_LIBRARY_PATH \
    LD_PRELOAD DESTDIR MESON_PACKAGE_CACHE_DIR
export CC=cc
export CXX=c++

contrib_options=(
    --disable-all
    --disable-gpl
    --disable-sout
    --enable-ad-clauses
    --enable-ass
    --enable-dav1d
    --enable-dvbpsi
    --enable-ebml
    --enable-ffmpeg
    --enable-flac
    --enable-freetype2
    --enable-fribidi
    --enable-gmp
    --enable-gnutls
    --enable-gsm
    --enable-harfbuzz
    --enable-jpeg
    --enable-libxml2
    --enable-matroska
    --enable-nettle
    --enable-ogg
    --enable-openjpeg
    --enable-opus
    --enable-png
    --enable-soxr
    --enable-vorbis
    --enable-vpx
    --enable-zlib
)
(
    cd "$contrib_build_directory"
    "$source_directory/contrib/bootstrap" \
        --host="$host_triplet" \
        --prefix="$contrib_prefix" \
        "${contrib_options[@]}"
    make list > "$build_directory/contrib-plan.txt"
    make -j"$jobs" fetch
    # The native bootstrap can classify the host zlib as distribution-provided
    # even when zlib was selected explicitly. That makes libpng's generated
    # dependency stamp independent from the source-built zlib stamp. Install
    # the pinned zlib first so libpng can never race the system header.
    make -j1 .zlib
    make -j"$jobs" -k || make -j1
    # This generated target is metadata rather than a linked contrib. It is
    # needed to point VLC's native Meson build at the closed static prefix.
    make -j1 .meson-machinefile
    make list > "$build_directory/contrib-list.txt"
)

readonly contrib_native_file="$contrib_prefix/share/meson/native/contrib.ini"
if [[ ! -f "$contrib_native_file" || -L "$contrib_native_file" ]]; then
    echo "VLC contrib build did not produce its native Meson machine file" >&2
    exit 1
fi

readonly common_c_flags="-O2 -fPIC -fstack-protector-strong -D_FORTIFY_SOURCE=3 -ffile-prefix-map=$source_directory=/usr/src/vlc -ffile-prefix-map=$build_directory=/usr/src/kmediavlc-build"
readonly common_link_flags="-Wl,-z,relro,-z,now,--as-needed -Wl,--build-id=sha1 -Wl,-Bsymbolic"
# The staged shared objects are private to this application runtime. Bind each
# object's own definitions locally so AArch64 data references from the pinned
# static FFmpeg contrib cannot be interposed. Undefined plugin ABI symbols are
# unaffected and continue to resolve from libvlccore.
# The contrib native file points at source-built static archives. Do not set
# Meson's global static preference: the six reviewed system dependencies use
# their distribution shared objects rather than non-PIC development archives.
meson_options=(
    --prefix=/
    --libdir=lib
    --buildtype=release
    --default-library=shared
    --wrap-mode=nodownload
    -Dauto_features=disabled
    -Db_lto=false
    -Db_ndebug=true
    -Dc_args="$common_c_flags"
    -Dcpp_args="$common_c_flags"
    -Dc_link_args="$common_link_flags"
    -Dcpp_link_args="$common_link_flags"
    -Dvlc=false
    -Dtests=disabled
    -Dnls=disabled
    -Dlua=disabled
    -Dstream_outputs=false
    -Dvideolan_manager=false
    -Daddon_manager=false
    -Dupdate-check=disabled
    -Drust=disabled
    -Davx=disabled
    -Dwayland=disabled
    -Dx11=disabled
    -Dxcb=disabled
    -Ddrm=disabled
    -Davcodec=enabled
    -Davformat=enabled
    -Dmerge-ffmpeg=false
    -Ddav1d=enabled
    -Dflac=enabled
    -Dfontconfig=enabled
    -Dfreetype=enabled
    -Dfribidi=enabled
    -Dgles2=enabled
    -Dgnutls=enabled
    -Dharfbuzz=enabled
    -Djpeg=enabled
    -Dlibass=enabled
    -Dlibdvbpsi=enabled
    -Dlibxml2=enabled
    -Dmatroska=enabled
    -Dogg=enabled
    -Dopus=enabled
    -Dpng=enabled
    -Dpulse=enabled
    -Dsoxr=enabled
    -Dswscale=enabled
    -Dvorbis=enabled
    -Dvpx=enabled
)
if [[ "$architecture" == "x86_64" ]]; then
    meson_options+=( -Dsse=enabled )
else
    meson_options+=( -Dsse=disabled )
fi
meson setup \
    "$meson_build_directory" \
    "$source_directory" \
    --native-file "$contrib_native_file" \
    "${meson_options[@]}"
meson compile -C "$meson_build_directory" -j "$jobs"
meson install \
    -C "$meson_build_directory" \
    --destdir "$install_directory" \
    --tags runtime \
    --strip

readonly libvlc="$install_directory/lib/libvlc.so"
readonly core="$install_directory/lib/libvlccore.so.9"
readonly plugin_directory="$install_directory/lib/vlc/plugins"
if [[ ! -f "$libvlc" || ! -f "$core" ]] ||
   [[ -z "$(find "$plugin_directory" -type f -name 'lib*_plugin.so' -print -quit 2>/dev/null)" ]]; then
    echo "VLC source build did not produce the expected shared Linux install" >&2
    exit 1
fi
for library in "$libvlc" "$core"; do
    if ! readelf -h "$library" | grep -Fq 'Class:                             ELF64'; then
        echo "VLC source build produced a non-ELF64 library" >&2
        exit 1
    fi
done

find "$plugin_directory" -type f -name 'lib*_plugin.so' -print \
    | LC_ALL=C sort > "$build_directory/raw-plugin-files.txt"
echo "Built pinned Linux $architecture libVLC install: $install_directory"
