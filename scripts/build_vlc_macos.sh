#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-2.1-or-later

set -euo pipefail

readonly PINNED_REVISION="b5536cdea24b313ba9215eacfbd7fa3295d7f3ee"

if [[ $# -lt 2 || $# -gt 3 ]]; then
    echo "usage: $0 <vlc-source> <absolute-build-directory> [jobs]" >&2
    exit 2
fi

source_directory="$(cd "$1" && pwd -P)"
build_directory="$2"
jobs="${3:-8}"
repository_root="$(cd "${BASH_SOURCE[0]%/*}/.." && pwd -P)"
configuration="$repository_root/build-recipes/vlc-apple.conf"
source_patch="$repository_root/build-recipes/patches/vlc-macos-opengl-callback-hdr.patch"

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "the macOS libVLC runtime must be built on macOS" >&2
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
if [[ ! -f "$configuration" || -L "$configuration" ]]; then
    echo "pinned Apple build configuration is missing or unsafe" >&2
    exit 1
fi
if [[ ! -f "$source_patch" || -L "$source_patch" ]]; then
    echo "pinned macOS VLC source patch is missing or unsafe" >&2
    exit 1
fi

actual_revision="$(git -C "$source_directory" rev-parse HEAD)"
if [[ "$actual_revision" != "$PINNED_REVISION" ]]; then
    echo "VLC source revision mismatch: $actual_revision" >&2
    exit 1
fi
if [[ -n "$(git -C "$source_directory" status --porcelain --untracked-files=no)" ]]; then
    echo "VLC source checkout contains tracked modifications" >&2
    exit 1
fi
if ! git -C "$source_directory" apply --check --whitespace=error-all "$source_patch"; then
    echo "pinned macOS VLC source patch does not apply cleanly" >&2
    exit 1
fi

patch_applied=false
restore_source_checkout() {
    local status=$?
    trap - EXIT
    if [[ "$patch_applied" == true ]]; then
        if ! git -C "$source_directory" apply --reverse --check "$source_patch" ||
           ! git -C "$source_directory" apply --reverse "$source_patch"; then
            echo "failed to restore the pinned VLC source checkout after the macOS build" >&2
            status=1
        fi
    fi
    exit "$status"
}
trap restore_source_checkout EXIT
git -C "$source_directory" apply --whitespace=error-all "$source_patch"
patch_applied=true

readonly upstream_builder="$source_directory/extras/package/apple/build.sh"
if [[ ! -x "$upstream_builder" ]]; then
    echo "pinned upstream Apple build entry point is missing" >&2
    exit 1
fi

# A fresh arm64 GitHub runner provides gettext/autopoint and pkgconf through
# Homebrew, but their M4 files live outside the aclocal prefix of the tools
# bootstrapped by VLC. Without these explicit providers, autoreconf can leave
# the gettext, iconv, and pkg-config macros unresolved and corrupt configure.
brew_executable="$(command -v brew || true)"
if [[ -z "$brew_executable" ]]; then
    echo "Homebrew is required to bind the macOS autotools macro providers" >&2
    exit 1
fi
gettext_macro_directory="$($brew_executable --prefix gettext)/share/gettext/m4"
pkgconf_macro_directory="$($brew_executable --prefix pkgconf)/share/aclocal"
readonly gettext_macro_directory pkgconf_macro_directory
for macro in \
    "$gettext_macro_directory/gettext.m4" \
    "$gettext_macro_directory/iconv.m4" \
    "$pkgconf_macro_directory/pkg.m4"; do
    if [[ ! -f "$macro" || -L "$macro" ]]; then
        echo "required macOS autotools macro is missing or unsafe: $(basename "$macro")" >&2
        exit 1
    fi
done
readonly bound_aclocal_path="$gettext_macro_directory:$pkgconf_macro_directory"
if [[ -n "${ACLOCAL_PATH:-}" ]]; then
    export ACLOCAL_PATH="$bound_aclocal_path:$ACLOCAL_PATH"
else
    export ACLOCAL_PATH="$bound_aclocal_path"
fi

mkdir "$build_directory"
patch_digest="$(shasum -a 256 "$source_patch" | awk '{print $1}')"
printf '%s  %s\n' "$patch_digest" "$(basename "$source_patch")" \
    > "$build_directory/source-patch-SHA256SUMS"
for macro in \
    "$gettext_macro_directory/gettext.m4" \
    "$gettext_macro_directory/iconv.m4" \
    "$pkgconf_macro_directory/pkg.m4"; do
    digest="$(shasum -a 256 "$macro" | awk '{print $1}')"
    printf '%s  %s\n' "$digest" "$(basename "$macro")"
done > "$build_directory/autotools-macro-SHA256SUMS"
cd "$build_directory"
export VLC_REQUESTED_CORE_COUNT="$jobs"
"$upstream_builder" \
    --arch=arm64 \
    --sdk=macosx \
    --enable-shared \
    --disable-debug \
    --config="$configuration" \
    -j"$jobs"

readonly install_directory="$build_directory/vlc-macosx-arm64"
readonly contrib_directory="$source_directory/contrib/contrib-arm64-apple-macOS_14.0"
if [[ ! -f "$install_directory/lib/libvlc.dylib" ]] ||
   [[ ! -f "$install_directory/lib/libvlccore.dylib" ]] ||
   [[ -z "$(find "$install_directory/lib/vlc/plugins" -type f -name 'lib*_plugin.dylib' -print -quit)" ]]; then
    echo "VLC source build did not produce the expected shared macOS install" >&2
    exit 1
fi
if [[ ! -f "$contrib_directory/Makefile" ]]; then
    echo "VLC source build did not preserve its contrib closure" >&2
    exit 1
fi

make -C "$contrib_directory" list > "$build_directory/contrib-list.txt"
echo "Built pinned macOS arm64 libVLC install: $install_directory"
