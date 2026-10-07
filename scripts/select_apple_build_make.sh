#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-2.1-or-later

# Source this before VideoLAN's Apple-hosted builds. Ninja's pipe jobserver
# changes break macOS's system Make 3.81; GNU Make 4.4 supplies a named FIFO.
if [[ "$(uname -s)" == "Darwin" ]]; then
    apple_make_prefix="$(brew --prefix make)"
    if [[ ! -x "$apple_make_prefix/libexec/gnubin/make" ]]; then
        echo "Apple-hosted VLC builds require Homebrew GNU Make 4.4 or newer" >&2
        exit 1
    fi
    export PATH="$apple_make_prefix/libexec/gnubin:$PATH"
    apple_make_version="$(make --version | sed -n '1s/^GNU Make //p')"
    if [[ ! "$apple_make_version" =~ ^([0-9]+)\.([0-9]+) ]] ||
       (( BASH_REMATCH[1] < 4 || (BASH_REMATCH[1] == 4 && BASH_REMATCH[2] < 4) )); then
        echo "Apple-hosted VLC builds require GNU Make 4.4 or newer" >&2
        exit 1
    fi
    export MAKEFLAGS=""
    export GNUMAKEFLAGS="--jobserver-style=fifo"
    echo "Selected GNU Make $apple_make_version with FIFO jobserver"
fi
