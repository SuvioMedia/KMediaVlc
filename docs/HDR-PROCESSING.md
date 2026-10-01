<!-- SPDX-License-Identifier: LGPL-2.1-or-later -->

# macOS HDR processing correction

The local processing branch corrects two color-boundary errors in the rc.10 producer. It is not
a new published runtime. It still requires a complete recipe rebuild and matched release evidence.

- libplacebo 5.264.1 produces linear RGB normalized to `PL_COLOR_SDR_WHITE` (203 nits). The IOSurface
  frame metadata must report that scale independently of the presentation target's requested white.
- The pinned VLC OpenGL sampler used an 8-bit YUV matrix for P010, including the wrong neutral
  chroma midpoint. The recipe patch now derives the matrix from source full/limited range, component
  precision and texture alignment. Planar high-bit-depth values are LSB-aligned; P010/P012 and
  packed Y210/Y212 values are MSB-aligned. Packed 8-bit YUYV remains an 8-bit component format.

The existing linear callback path preserves source HDR metadata to avoid an SDR tone map. The
corrected producer was tested by KMediaPlayer with actual Main10 PQ/HLG neutral videos and a
wide-gamut PQ video. GPU classifiers assert absolute BT.2020 values, including output after a
two-stop exposure increase, while native readback confirms the 203-nit metadata. Changing window
white to 80/100/203/300 nits preserves the source's physical luminance. The SDR processing/EOF tests
also pass with the modified sampler and bridge. These are offscreen pixel checks, not scanout tests.

The independent numeric regression compiles the sampler's actual matrix and format selection:

```sh
python3 scripts/test_macos_sampler_color.py --source /path/to/recipe-patched-vlc
```

It passes 1716 combinations of full/limited range, BT.601/709/2020, 8/9/10/12/16-bit planar and packed
storage, reversed UV, neutral endpoints and deterministic color samples. The original upstream
sampler fails the same reference. The full patch applies cleanly to the pinned VLC revision
`e439692079a75cacb5f07310d1ec2dc20bfd1fe0`. All four fake-libVLC native integration tests pass,
including the new case where target white is 100 nits but the linear producer still reports 203.

The player acceptance run used an isolated copy of the verified macOS rc.10 runtime, replacing only
the native bridge and `libglsampler_builtin_plugin.dylib`. The sampler was built from the complete
patched `sampler.c`, `gl_api.c`, `gl_util.c` and `libplacebo/utils.c`, linked with static libplacebo
5.264.1 and the matching libvlccore. The plugin was relocated, ad-hoc signed and its plugin cache
regenerated. The original binary inventory is kept as base provenance, not as a checksum claim for
the modified build. No released cache or remote artifact was changed.
