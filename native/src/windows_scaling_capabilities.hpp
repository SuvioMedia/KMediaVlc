// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once
#include <d3d11.h>
#include <wrl/client.h>

inline int video_scaling_options(ID3D11Device* device, ID3D11DeviceContext* context, int vendor) {
    using Microsoft::WRL::ComPtr;
    ComPtr<ID3D11VideoDevice> video;
    ComPtr<ID3D11VideoContext> video_context;
    if (FAILED(device->QueryInterface(IID_PPV_ARGS(&video))) ||
        FAILED(context->QueryInterface(IID_PPV_ARGS(&video_context)))) return 7;
    D3D11_VIDEO_PROCESSOR_CONTENT_DESC content{};
    content.InputFrameFormat = D3D11_VIDEO_FRAME_FORMAT_PROGRESSIVE;
    content.InputWidth = 640; content.InputHeight = 360;
    content.OutputWidth = 1280; content.OutputHeight = 720;
    content.Usage = D3D11_VIDEO_USAGE_PLAYBACK_NORMAL;
    ComPtr<ID3D11VideoProcessorEnumerator> enumerator;
    ComPtr<ID3D11VideoProcessor> processor;
    if (FAILED(video->CreateVideoProcessorEnumerator(&content, &enumerator)) ||
        FAILED(video->CreateVideoProcessor(enumerator.Get(), 0, &processor))) return 7;
    if (vendor == 1) {
        constexpr GUID extension{0xd43ce1b3, 0x1f4b, 0x48ac, {0xba, 0xee, 0xc3, 0xc2, 0x53, 0x75, 0xe6, 0xf7}};
        UINT supported = 0;
        if (SUCCEEDED(video_context->VideoProcessorGetStreamExtension(processor.Get(), 0,
                &extension, sizeof(supported), &supported)) && supported) return 31;
    } else if (vendor == 2) {
        constexpr GUID extension{0xedd1d4b9, 0x8659, 0x4cbc, {0xa4, 0xd6, 0x98, 0x31, 0xa2, 0x16, 0x3a, 0xc3}};
        UINT value = 3;
        struct { UINT function; void* value; } request{1, &value};
        if (FAILED(video_context->VideoProcessorSetOutputExtension(processor.Get(), &extension, sizeof(request), &request))) return 15;
        request.function = 0x20; value = 1;
        if (FAILED(video_context->VideoProcessorSetOutputExtension(processor.Get(), &extension, sizeof(request), &request))) return 15;
        request.function = 0x37; value = 2;
        if (SUCCEEDED(video_context->VideoProcessorSetStreamExtension(processor.Get(), 0, &extension, sizeof(request), &request))) return 31;
    }
    return 15;
}
