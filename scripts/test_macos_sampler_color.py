#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Compile the actual patched VLC sampler's YUV matrix and check code-value math.

Usage: python3 scripts/test_macos_sampler_color.py --source build/hdr-vlc-source
No decoder, GPU or downloaded fixture is needed. The source must already have
the recipe patches applied. No production runtime or cache is modified.
"""

import argparse
import ctypes
from pathlib import Path
import random
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    sampler = (source / "modules/video_output/opengl/sampler.c").read_text()
    begin = sampler.index("#define MATRIX_YUV_TO_RGB(KR, KB)")
    # Include the old constants too when demonstrating that the unpatched source fails.
    legacy = sampler.find("static const float MATRIX_COLOR_RANGE_LIMITED")
    if legacy >= 0:
        begin = min(begin, legacy)
    end = sampler.index("static void\nsampler_base_fetch_locations", begin)
    code = r'''
#include <vlc_common.h>
#include <vlc_es.h>
#include <vlc_fourcc.h>
struct sampler_priv { float conv_matrix[16]; bool yuv_color; };
struct vlc_gl_sampler { void *sys; video_format_t fmt_in; bool half_float; };
static struct sampler_priv *PRIV(struct vlc_gl_sampler *s) { return s->sys; }
''' + sampler[begin:end] + r'''
void test_matrix(unsigned format, unsigned full, unsigned space, float *result) {
    const struct { vlc_fourcc_t fcc; unsigned size, bits; } formats[] = {
        {VLC_CODEC_NV12, 1, 8}, {VLC_CODEC_I420_9L, 2, 9},
        {VLC_CODEC_I420_10L, 2, 10}, {VLC_CODEC_I420_12L, 2, 12},
        {VLC_CODEC_I420_16L, 2, 16}, {VLC_CODEC_P010, 2, 10},
        {VLC_CODEC_P012, 2, 12}, {VLC_CODEC_P016, 2, 16},
        {VLC_CODEC_Y210, 4, 32}, {VLC_CODEC_Y212, 4, 32},
        {VLC_CODEC_NV21, 1, 8}, {VLC_CODEC_YV12, 1, 8},
        {VLC_CODEC_YUYV, 2, 16},
    };
    const video_color_space_t spaces[] = {COLOR_SPACE_BT601, COLOR_SPACE_BT709, COLOR_SPACE_BT2020};
    const vlc_chroma_description_t desc = {
        .fcc = formats[format].fcc, .pixel_size = formats[format].size,
        .pixel_bits = formats[format].bits,
        .plane_count = format == 0 || (format >= 5 && format <= 7) || format == 10 ? 2 :
                       (format >= 1 && format <= 4) || format == 11 ? 3 : 1,
    };
    struct sampler_priv priv = {0};
    struct vlc_gl_sampler sampler = {
        .sys = &priv, .fmt_in.color_range = full ? COLOR_RANGE_FULL : COLOR_RANGE_LIMITED,
    };
    sampler_yuv_base_init(&sampler, &desc, spaces[space]);
    memcpy(result, priv.conv_matrix, sizeof(priv.conv_matrix));
}
'''
    # Each tuple describes actual texture storage, independently of the matrix.
    formats = [(8, 8, 0, False), (9, 16, 0, False), (10, 16, 0, False),
               (12, 16, 0, False), (16, 16, 0, False), (10, 16, 6, False),
               (12, 16, 4, False), (16, 16, 0, False), (10, 16, 6, False),
               (12, 16, 4, False), (8, 8, 0, True), (8, 8, 0, True),
               (8, 8, 0, False)]
    count = 0
    with tempfile.TemporaryDirectory(prefix="kmediavlc-color-") as directory:
        work = Path(directory)
        c = work / "matrix.c"
        c.write_text(code)
        lib = work / "matrix.dylib"
        subprocess.run(["cc", "-std=gnu11", "-dynamiclib", "-O2", "-I" + str(source / "include"),
                        str(c), "-o", str(lib)], check=True)
        matrix = ctypes.CDLL(str(lib)).test_matrix
        matrix.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_float)]
        matrix.restype = None
        rng = random.Random(203)
        for index, (bits, storage, shift, swapped) in enumerate(formats):
            maximum, step = (1 << bits) - 1, 1 << (bits - 8)
            neutral = 1 << (bits - 1)
            for full in (False, True):
                black, white = (0, maximum) if full else (16 * step, 235 * step)
                chroma_range = maximum if full else 224 * step
                vectors = [(black, neutral, neutral), (white, neutral, neutral)]
                vectors += [tuple(rng.randrange(maximum + 1) for _ in range(3)) for _ in range(20)]
                for space, (kr, kb) in enumerate(((0.299, 0.114), (0.2126, 0.0722), (0.2627, 0.0593))):
                    m = (ctypes.c_float * 16)()
                    matrix(index, full, space, m)
                    for y, cb, cr in vectors:
                        yl = (y - black) / (white - black)
                        u, v = (cb - neutral) / chroma_range, (cr - neutral) / chroma_range
                        red, blue = yl + 2 * (1 - kr) * v, yl + 2 * (1 - kb) * u
                        expected = (red, (yl - kr * red - kb * blue) / (1 - kr - kb), blue)
                        samples = (y, cr, cb) if swapped else (y, cb, cr)
                        texels = [value * (1 << shift) / ((1 << storage) - 1) for value in samples] + [1]
                        actual = [sum(m[column * 4 + row] * texels[column] for column in range(4)) for row in range(3)]
                        assert max(abs(a - e) for a, e in zip(actual, expected)) < 2e-6, (
                            index, full, space, (y, cb, cr), actual, expected)
                        count += 1
    print(f"Passed {count} actual sampler matrix cases (packed/planar, 8–16 bit, full/limited, BT.601/709/2020).")


if __name__ == "__main__":
    main()
