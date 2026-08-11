// SPDX-License-Identifier: LGPL-2.1-or-later

#include "bridge_internal.hpp"

#include <windows.h>
#include <d3d11.h>
#include <d3dcompiler.h>
#include <dxgi.h>
#include <dxgi1_2.h>

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <memory>
#include <mutex>
#include <string>
#include <utility>
#include <vector>

namespace kmediavlc {
namespace {

constexpr std::size_t kTextureCount = 4;
constexpr DWORD kProducerAcquireTimeoutMilliseconds = 5;

bool debug_callbacks_enabled() {
    static const bool enabled = [] {
        const char* value = std::getenv("KMEDIAVLC_DEBUG_CALLBACKS");
        return value != nullptr && value[0] == '1' && value[1] == '\0';
    }();
    return enabled;
}

void trace_callback(const char* event) {
    if (!debug_callbacks_enabled()) return;
    std::fprintf(stderr, "[KMediaVlc D3D11] %s\n", event);
    std::fflush(stderr);
}

template <typename Interface>
void release_interface(Interface*& value) noexcept {
    if (value != nullptr) value->Release();
    value = nullptr;
}

bool same_luid(const LUID& left, std::uint64_t right) {
    const auto packed =
        (static_cast<std::uint64_t>(static_cast<std::uint32_t>(left.HighPart)) << 32U) |
        static_cast<std::uint32_t>(left.LowPart);
    return packed == right;
}

class WindowsTexture final {
public:
    ~WindowsTexture() {
        release_interface(conversion_render_target);
        release_interface(producer_shader_resource);
        release_interface(producer_render_target);
        release_interface(producer_texture);
        release_interface(keyed_mutex);
        release_interface(texture);
    }

    WindowsTexture(const WindowsTexture&) = delete;
    WindowsTexture& operator=(const WindowsTexture&) = delete;
    WindowsTexture() = default;

    ID3D11Texture2D* texture = nullptr;
    ID3D11Texture2D* producer_texture = nullptr;
    ID3D11RenderTargetView* producer_render_target = nullptr;
    ID3D11ShaderResourceView* producer_shader_resource = nullptr;
    ID3D11RenderTargetView* conversion_render_target = nullptr;
    IDXGIKeyedMutex* keyed_mutex = nullptr;
    HANDLE shared_handle = nullptr; // Legacy shared handle: never CloseHandle().
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    DXGI_FORMAT format = DXGI_FORMAT_UNKNOWN;
    DXGI_FORMAT producer_format = DXGI_FORMAT_UNKNOWN;
};

class WindowsD3D11Renderer final : public PlatformRenderer {
public:
    explicit WindowsD3D11Renderer(kmediavlc_player* player) : player_(player) {}
    ~WindowsD3D11Renderer() override { release_resources(); }

    bool install(libvlc_media_player_t* media_player, std::string& error) override {
        trace_callback("install");
        media_player_ = media_player;
        if (!player_->api->video_set_output_callbacks(
                media_player,
                libvlc_video_engine_d3d11,
                setup_callback,
                cleanup_callback,
                window_callback,
                update_output_callback,
                swap_callback,
                make_current_callback,
                nullptr,
                metadata_callback,
                select_plane_callback,
                this)) {
            error = "The pinned LibVLC 4 runtime rejected D3D11 texture callbacks.";
            media_player_ = nullptr;
            return false;
        }
        installed_ = true;
        return true;
    }

    void uninstall(libvlc_media_player_t* media_player) noexcept override {
        if (!installed_ || media_player == nullptr) return;
        player_->api->video_set_output_callbacks(
            media_player,
            libvlc_video_engine_disable,
            setup_callback,
            cleanup_callback,
            window_callback,
            update_output_callback,
            swap_callback,
            make_current_callback,
            nullptr,
            metadata_callback,
            select_plane_callback,
            this);
        installed_ = false;
        media_player_ = nullptr;
        release_resources();
    }

    bool output_target_changed(const OutputTargetSnapshot& target, std::string& error) override {
        if (target.type == KMEDIAVLC_OUTPUT_UNAVAILABLE) return true;
        if (target.type != KMEDIAVLC_OUTPUT_WINDOWS_D3D11 || target.width == 0 || target.height == 0 ||
            target.generation == 0 || target.adapter_luid == 0) {
            error = "The D3D11 producer target is incomplete.";
            return false;
        }
        std::lock_guard lock(render_mutex_);
        if (device_ != nullptr && !same_luid(device_luid_, target.adapter_luid)) {
            error = "The active D3D11 adapter changed; the LibVLC player must be recreated.";
            return false;
        }
        return true;
    }

    bool resize(std::uint32_t width, std::uint32_t height) override {
        if (width == 0 || height == 0) return false;
        return true;
    }

private:
    static bool setup_callback(
        void** opaque,
        const libvlc_video_setup_device_cfg_t* config,
        libvlc_video_setup_device_info_t* output) {
        trace_callback("setup callback");
        if (opaque == nullptr || *opaque == nullptr) return false;
        return static_cast<WindowsD3D11Renderer*>(*opaque)->setup(config, output);
    }

    static void cleanup_callback(void* opaque) {
        trace_callback("cleanup callback");
        if (opaque != nullptr) static_cast<WindowsD3D11Renderer*>(opaque)->cleanup();
    }

    static void window_callback(
        void* opaque,
        libvlc_video_output_resize_cb resize,
        libvlc_video_output_mouse_move_cb,
        libvlc_video_output_mouse_press_cb,
        libvlc_video_output_mouse_release_cb,
        void* report_opaque) {
        trace_callback("window callback");
        if (opaque != nullptr) {
            static_cast<WindowsD3D11Renderer*>(opaque)->set_resize_reporter(resize, report_opaque);
        }
    }

    static bool update_output_callback(
        void* opaque,
        const libvlc_video_render_cfg_t* config,
        libvlc_video_output_cfg_t* output) {
        trace_callback("update-output callback");
        return opaque != nullptr &&
            static_cast<WindowsD3D11Renderer*>(opaque)->update_output(config, output);
    }

    static void swap_callback(void* opaque) {
        trace_callback("swap callback");
        if (opaque != nullptr) static_cast<WindowsD3D11Renderer*>(opaque)->swap();
    }

    static bool make_current_callback(void* opaque, bool enter) {
        trace_callback(enter ? "make-current enter" : "make-current leave");
        return opaque != nullptr && static_cast<WindowsD3D11Renderer*>(opaque)->make_current(enter);
    }

    static bool select_plane_callback(void* opaque, std::size_t plane, void* output) {
        trace_callback("select-plane callback");
        return opaque != nullptr &&
            static_cast<WindowsD3D11Renderer*>(opaque)->select_plane(plane, output);
    }

    static void metadata_callback(void* opaque, libvlc_video_metadata_type_t type, const void* metadata) {
        if (opaque != nullptr) static_cast<WindowsD3D11Renderer*>(opaque)->metadata(type, metadata);
    }

    bool setup(
        const libvlc_video_setup_device_cfg_t* config,
        libvlc_video_setup_device_info_t* output) {
        if (output == nullptr) return false;
        const auto target = copy_output_target(player_);
        if (target.type != KMEDIAVLC_OUTPUT_WINDOWS_D3D11 || target.adapter_luid == 0) {
            set_error(player_, "A verified Windows TextureView target is required before playback starts.");
            return false;
        }
        std::lock_guard lock(render_mutex_);
        if (!create_device(target.adapter_luid, config != nullptr && config->hardware_decoding)) return false;
        output->u.d3d11.device_context = context_;
        output->u.d3d11.context_mutex = nullptr;
        context_->AddRef();
        callback_context_reference_ = true;
        trace_callback("setup complete");
        return true;
    }

    bool create_device(std::uint64_t adapter_luid, bool video_support) {
        if (device_ != nullptr) return same_luid(device_luid_, adapter_luid);
        IDXGIFactory1* factory = nullptr;
        HRESULT result = CreateDXGIFactory1(__uuidof(IDXGIFactory1), reinterpret_cast<void**>(&factory));
        if (FAILED(result) || factory == nullptr) {
            set_error(player_, "CreateDXGIFactory1 failed for the TextureView adapter.");
            return false;
        }
        IDXGIAdapter1* selected = nullptr;
        for (UINT index = 0; ; ++index) {
            IDXGIAdapter1* candidate = nullptr;
            result = factory->EnumAdapters1(index, &candidate);
            if (result == DXGI_ERROR_NOT_FOUND) break;
            if (FAILED(result) || candidate == nullptr) continue;
            DXGI_ADAPTER_DESC1 description{};
            if (SUCCEEDED(candidate->GetDesc1(&description)) && same_luid(description.AdapterLuid, adapter_luid)) {
                selected = candidate;
                device_luid_ = description.AdapterLuid;
                break;
            }
            candidate->Release();
        }
        factory->Release();
        if (selected == nullptr) {
            set_error(player_, "The TextureView DXGI adapter LUID is unavailable.");
            return false;
        }
        UINT flags = D3D11_CREATE_DEVICE_BGRA_SUPPORT;
        if (video_support) flags |= D3D11_CREATE_DEVICE_VIDEO_SUPPORT;
        const std::array<D3D_FEATURE_LEVEL, 4> levels{
            D3D_FEATURE_LEVEL_11_1,
            D3D_FEATURE_LEVEL_11_0,
            D3D_FEATURE_LEVEL_10_1,
            D3D_FEATURE_LEVEL_10_0,
        };
        D3D_FEATURE_LEVEL actual{};
        result = D3D11CreateDevice(
            selected,
            D3D_DRIVER_TYPE_UNKNOWN,
            nullptr,
            flags,
            levels.data(),
            static_cast<UINT>(levels.size()),
            D3D11_SDK_VERSION,
            &device_,
            &actual,
            &context_);
        if (result == E_INVALIDARG) {
            result = D3D11CreateDevice(
                selected,
                D3D_DRIVER_TYPE_UNKNOWN,
                nullptr,
                flags,
                levels.data() + 1,
                static_cast<UINT>(levels.size() - 1U),
                D3D11_SDK_VERSION,
                &device_,
                &actual,
                &context_);
        }
        selected->Release();
        if (FAILED(result) || device_ == nullptr || context_ == nullptr || actual < D3D_FEATURE_LEVEL_10_0) {
            release_interface(context_);
            release_interface(device_);
            set_error(player_, "D3D11CreateDevice failed on the TextureView adapter.");
            return false;
        }
        ID3D10Multithread* multithread = nullptr;
        if (SUCCEEDED(context_->QueryInterface(__uuidof(ID3D10Multithread), reinterpret_cast<void**>(&multithread)))) {
            multithread->SetMultithreadProtected(TRUE);
            multithread->Release();
        }
        return true;
    }

    void set_resize_reporter(libvlc_video_output_resize_cb resize, void* opaque) {
        std::uint32_t width = 0;
        std::uint32_t height = 0;
        {
            std::lock_guard lock(player_->output_mutex);
            player_->report_resize = resize;
            player_->report_resize_opaque = opaque;
            width = player_->output_target.width;
            height = player_->output_target.height;
        }
        if (resize != nullptr && width != 0 && height != 0) resize(opaque, width, height);
    }

    bool update_output(
        const libvlc_video_render_cfg_t* config,
        libvlc_video_output_cfg_t* output) {
        if (output == nullptr) return false;
        const auto target = copy_output_target(player_);
        if (target.type != KMEDIAVLC_OUTPUT_WINDOWS_D3D11 || target.generation == 0 ||
            target.width == 0 || target.height == 0 || target.adapter_luid == 0) {
            set_error(player_, "The active D3D11 TextureView output is unavailable.");
            return false;
        }
        std::lock_guard lock(render_mutex_);
        if (device_ == nullptr || !same_luid(device_luid_, target.adapter_luid)) {
            set_error(player_, "The D3D11 render device no longer matches the TextureView host generation.");
            return false;
        }
        source_dynamic_range_ = source_dynamic_range(config);
        source_extended_ = source_dynamic_range_ == KMEDIAVLC_SOURCE_DYNAMIC_RANGE_HDR10 ||
            source_dynamic_range_ == KMEDIAVLC_SOURCE_DYNAMIC_RANGE_HLG;
        const bool hdr_output = target.request_hdr && source_extended_;
        const DXGI_FORMAT published_format = hdr_output
            ? DXGI_FORMAT_R16G16B16A16_FLOAT
            : DXGI_FORMAT_R8G8B8A8_UNORM;
        const DXGI_FORMAT producer_format = hdr_output
            ? DXGI_FORMAT_R16G16B16A16_UNORM
            : DXGI_FORMAT_R8G8B8A8_UNORM;
        if (!ensure_textures(target.width, target.height, published_format, producer_format)) return false;
        player_->video_width.store(target.width, std::memory_order_release);
        player_->video_height.store(target.height, std::memory_order_release);
        output->u.dxgi_format = producer_format;
        output->full_range = true;
        output->colorspace = hdr_output
            ? libvlc_video_colorspace_BT2020
            : libvlc_video_colorspace_BT709;
        output->primaries = hdr_output
            ? libvlc_video_primaries_BT2020
            : libvlc_video_primaries_BT709;
        output->transfer = hdr_output
            ? libvlc_video_transfer_func_PQ
            : libvlc_video_transfer_func_SRGB;
        output->orientation = libvlc_video_orient_top_left;
        trace_callback(hdr_output ? "update-output complete: rgba16f" : "update-output complete: rgba8");
        return true;
    }

    static kmediavlc_source_dynamic_range source_dynamic_range(
        const libvlc_video_render_cfg_t* config) noexcept {
        if (config == nullptr) return KMEDIAVLC_SOURCE_DYNAMIC_RANGE_UNKNOWN;
        if (config->transfer == libvlc_video_transfer_func_PQ) {
            return KMEDIAVLC_SOURCE_DYNAMIC_RANGE_HDR10;
        }
        if (config->transfer == libvlc_video_transfer_func_HLG) {
            return KMEDIAVLC_SOURCE_DYNAMIC_RANGE_HLG;
        }
        return KMEDIAVLC_SOURCE_DYNAMIC_RANGE_SDR;
    }

    bool ensure_textures(
        std::uint32_t width,
        std::uint32_t height,
        DXGI_FORMAT published_format,
        DXGI_FORMAT producer_format) {
        if (!textures_.empty() && textures_.front()->width == width &&
            textures_.front()->height == height && textures_.front()->format == published_format &&
            textures_.front()->producer_format == producer_format) return true;
        if (published_format == DXGI_FORMAT_R16G16B16A16_FLOAT && !ensure_conversion_pipeline()) {
            return false;
        }
        std::vector<std::shared_ptr<WindowsTexture>> replacement;
        replacement.reserve(kTextureCount);
        for (std::size_t index = 0; index < kTextureCount; ++index) {
            auto texture = create_texture(width, height, published_format, producer_format);
            if (!texture) return false;
            replacement.push_back(std::move(texture));
        }
        textures_ = std::move(replacement);
        current_.reset();
        return true;
    }

    std::shared_ptr<WindowsTexture> create_texture(
        std::uint32_t width,
        std::uint32_t height,
        DXGI_FORMAT published_format,
        DXGI_FORMAT producer_format) {
        auto output = std::make_shared<WindowsTexture>();
        D3D11_TEXTURE2D_DESC description{};
        description.Width = width;
        description.Height = height;
        description.MipLevels = 1;
        description.ArraySize = 1;
        description.Format = published_format;
        description.SampleDesc.Count = 1;
        description.Usage = D3D11_USAGE_DEFAULT;
        description.BindFlags = D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE;
        description.MiscFlags = D3D11_RESOURCE_MISC_SHARED_KEYEDMUTEX;
        HRESULT result = device_->CreateTexture2D(&description, nullptr, &output->texture);
        if (FAILED(result) || output->texture == nullptr) {
            set_error(player_, "The keyed D3D11 video texture could not be created.");
            return {};
        }
        result = output->texture->QueryInterface(
            __uuidof(IDXGIKeyedMutex), reinterpret_cast<void**>(&output->keyed_mutex));
        if (FAILED(result) || output->keyed_mutex == nullptr) {
            set_error(player_, "The D3D11 video texture has no keyed mutex.");
            return {};
        }
        IDXGIResource* resource = nullptr;
        result = output->texture->QueryInterface(__uuidof(IDXGIResource), reinterpret_cast<void**>(&resource));
        if (FAILED(result) || resource == nullptr) {
            set_error(player_, "The D3D11 video texture is not shareable.");
            return {};
        }
        result = resource->GetSharedHandle(&output->shared_handle);
        resource->Release();
        if (FAILED(result) || output->shared_handle == nullptr) {
            set_error(player_, "The legacy D3D11 shared handle could not be exported.");
            return {};
        }
        output->width = width;
        output->height = height;
        output->format = published_format;
        output->producer_format = producer_format;

        if (producer_format == published_format) {
            result = device_->CreateRenderTargetView(
                output->texture, nullptr, &output->producer_render_target);
            if (FAILED(result) || output->producer_render_target == nullptr) {
                set_error(player_, "The D3D11 video render target could not be created.");
                return {};
            }
            return output;
        }

        D3D11_TEXTURE2D_DESC producer_description = description;
        producer_description.Format = producer_format;
        producer_description.MiscFlags = 0;
        result = device_->CreateTexture2D(
            &producer_description, nullptr, &output->producer_texture);
        if (FAILED(result) || output->producer_texture == nullptr) {
            set_error(player_, "The bounded-PQ D3D11 intermediate texture could not be created.");
            return {};
        }
        result = device_->CreateRenderTargetView(
            output->producer_texture, nullptr, &output->producer_render_target);
        if (FAILED(result) || output->producer_render_target == nullptr) {
            set_error(player_, "The bounded-PQ D3D11 render target could not be created.");
            return {};
        }
        result = device_->CreateShaderResourceView(
            output->producer_texture, nullptr, &output->producer_shader_resource);
        if (FAILED(result) || output->producer_shader_resource == nullptr) {
            set_error(player_, "The bounded-PQ D3D11 shader input could not be created.");
            return {};
        }
        result = device_->CreateRenderTargetView(
            output->texture, nullptr, &output->conversion_render_target);
        if (FAILED(result) || output->conversion_render_target == nullptr) {
            set_error(player_, "The FP16 D3D11 conversion target could not be created.");
            return {};
        }
        return output;
    }

    bool ensure_conversion_pipeline() {
        if (conversion_vertex_shader_ != nullptr && conversion_pixel_shader_ != nullptr &&
            conversion_sampler_ != nullptr && conversion_constants_ != nullptr) return true;
        static constexpr char shader_source[] = R"hlsl(
            cbuffer HdrConstants : register(b0) {
                float inverseSdrWhiteNits;
                float3 padding;
            };
            Texture2D<float4> sourceTexture : register(t0);
            SamplerState sourceSampler : register(s0);

            struct VertexOutput {
                float4 position : SV_POSITION;
                float2 uv : TEXCOORD0;
            };

            VertexOutput vertexMain(uint id : SV_VertexID) {
                const float2 positions[3] = {
                    float2(-1.0, -1.0),
                    float2(-1.0,  3.0),
                    float2( 3.0, -1.0)
                };
                const float2 coordinates[3] = {
                    float2(0.0,  1.0),
                    float2(0.0, -1.0),
                    float2(2.0,  1.0)
                };
                VertexOutput output;
                output.position = float4(positions[id], 0.0, 1.0);
                output.uv = coordinates[id];
                return output;
            }

            float3 pqToNits(float3 encoded) {
                const float m1 = 0.1593017578125;
                const float m2 = 78.84375;
                const float c1 = 0.8359375;
                const float c2 = 18.8515625;
                const float c3 = 18.6875;
                float3 powered = pow(saturate(encoded), 1.0 / m2);
                float3 numerator = max(powered - c1, 0.0);
                float3 denominator = max(c2 - c3 * powered, 0.000001);
                return 10000.0 * pow(numerator / denominator, 1.0 / m1);
            }

            float4 pixelMain(VertexOutput input) : SV_TARGET {
                float4 sampleValue = sourceTexture.Sample(sourceSampler, input.uv);
                float3 bt2020 = pqToNits(sampleValue.rgb) * inverseSdrWhiteNits;
                float3 linearSrgb;
                linearSrgb.r =  1.660491 * bt2020.r - 0.587641 * bt2020.g - 0.072850 * bt2020.b;
                linearSrgb.g = -0.124550 * bt2020.r + 1.132900 * bt2020.g - 0.008349 * bt2020.b;
                linearSrgb.b = -0.018151 * bt2020.r - 0.100579 * bt2020.g + 1.118730 * bt2020.b;
                return float4(linearSrgb, sampleValue.a);
            }
        )hlsl";

        ID3DBlob* vertex_blob = nullptr;
        ID3DBlob* pixel_blob = nullptr;
        ID3DBlob* errors = nullptr;
        HRESULT result = D3DCompile(
            shader_source,
            sizeof(shader_source) - 1U,
            "KMediaVlcPqToScRgb",
            nullptr,
            nullptr,
            "vertexMain",
            "vs_4_0",
            D3DCOMPILE_ENABLE_STRICTNESS,
            0,
            &vertex_blob,
            &errors);
        release_interface(errors);
        if (FAILED(result) || vertex_blob == nullptr) {
            release_interface(vertex_blob);
            set_error(player_, "The PQ-to-scRGB D3D11 vertex shader did not compile.");
            return false;
        }
        result = D3DCompile(
            shader_source,
            sizeof(shader_source) - 1U,
            "KMediaVlcPqToScRgb",
            nullptr,
            nullptr,
            "pixelMain",
            "ps_4_0",
            D3DCOMPILE_ENABLE_STRICTNESS,
            0,
            &pixel_blob,
            &errors);
        release_interface(errors);
        if (FAILED(result) || pixel_blob == nullptr) {
            release_interface(vertex_blob);
            release_interface(pixel_blob);
            set_error(player_, "The PQ-to-scRGB D3D11 pixel shader did not compile.");
            return false;
        }
        result = device_->CreateVertexShader(
            vertex_blob->GetBufferPointer(), vertex_blob->GetBufferSize(), nullptr, &conversion_vertex_shader_);
        if (SUCCEEDED(result)) {
            result = device_->CreatePixelShader(
                pixel_blob->GetBufferPointer(), pixel_blob->GetBufferSize(), nullptr, &conversion_pixel_shader_);
        }
        release_interface(vertex_blob);
        release_interface(pixel_blob);
        if (FAILED(result) || conversion_vertex_shader_ == nullptr || conversion_pixel_shader_ == nullptr) {
            set_error(player_, "The PQ-to-scRGB D3D11 shaders could not be created.");
            return false;
        }
        D3D11_SAMPLER_DESC sampler{};
        sampler.Filter = D3D11_FILTER_MIN_MAG_MIP_LINEAR;
        sampler.AddressU = D3D11_TEXTURE_ADDRESS_CLAMP;
        sampler.AddressV = D3D11_TEXTURE_ADDRESS_CLAMP;
        sampler.AddressW = D3D11_TEXTURE_ADDRESS_CLAMP;
        sampler.ComparisonFunc = D3D11_COMPARISON_NEVER;
        sampler.MaxLOD = D3D11_FLOAT32_MAX;
        result = device_->CreateSamplerState(&sampler, &conversion_sampler_);
        if (FAILED(result) || conversion_sampler_ == nullptr) {
            set_error(player_, "The PQ-to-scRGB D3D11 sampler could not be created.");
            return false;
        }
        D3D11_BUFFER_DESC constants{};
        constants.ByteWidth = 16;
        constants.Usage = D3D11_USAGE_DEFAULT;
        constants.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
        result = device_->CreateBuffer(&constants, nullptr, &conversion_constants_);
        if (FAILED(result) || conversion_constants_ == nullptr) {
            set_error(player_, "The PQ-to-scRGB D3D11 constants could not be created.");
            return false;
        }
        return true;
    }

    bool convert_current_to_sc_rgb() {
        if (!current_ || current_->producer_shader_resource == nullptr ||
            current_->conversion_render_target == nullptr || context_ == nullptr) return false;
        const auto target = copy_output_target(player_);
        if (!std::isfinite(target.sdr_white_nits) || target.sdr_white_nits <= 0.0F) return false;
        // VLC's D3D11 shader maps ordinary sRGB white to the scRGB/PQ reference
        // of 80 nits. HDR transfers retain absolute luminance and must instead
        // be normalized to the active system SDR white reported by Nucleus.
        const float reference_nits = source_extended_ ? target.sdr_white_nits : 80.0F;
        const std::array<float, 4> constants{1.0F / reference_nits, 0.0F, 0.0F, 0.0F};
        context_->UpdateSubresource(conversion_constants_, 0, nullptr, constants.data(), 0, 0);
        D3D11_VIEWPORT viewport{};
        viewport.Width = static_cast<float>(current_->width);
        viewport.Height = static_cast<float>(current_->height);
        viewport.MaxDepth = 1.0F;
        context_->RSSetViewports(1, &viewport);
        context_->OMSetRenderTargets(1, &current_->conversion_render_target, nullptr);
        context_->IASetInputLayout(nullptr);
        context_->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
        context_->VSSetShader(conversion_vertex_shader_, nullptr, 0);
        context_->PSSetShader(conversion_pixel_shader_, nullptr, 0);
        context_->PSSetShaderResources(0, 1, &current_->producer_shader_resource);
        context_->PSSetSamplers(0, 1, &conversion_sampler_);
        context_->PSSetConstantBuffers(0, 1, &conversion_constants_);
        context_->Draw(3, 0);
        ID3D11ShaderResourceView* no_resource = nullptr;
        context_->PSSetShaderResources(0, 1, &no_resource);
        return true;
    }

    bool make_current(bool enter) {
        if (!enter) {
            if (!render_lock_held_) return false;
            if (current_ && current_->format == DXGI_FORMAT_R16G16B16A16_FLOAT &&
                !convert_current_to_sc_rgb()) {
                set_error(player_, "The bounded-PQ frame could not be converted to linear scRGB FP16.");
                if (current_->keyed_mutex != nullptr) current_->keyed_mutex->ReleaseSync(0);
                current_.reset();
                render_lock_held_ = false;
                render_mutex_.unlock();
                return false;
            }
            if (context_ != nullptr) context_->Flush();
            if (current_ && current_->keyed_mutex != nullptr) current_->keyed_mutex->ReleaseSync(0);
            render_lock_held_ = false;
            render_mutex_.unlock();
            return true;
        }
        render_mutex_.lock();
        render_lock_held_ = true;
        current_.reset();
        for (const auto& texture : textures_) {
            if (texture.use_count() != 1 || texture->keyed_mutex == nullptr) continue;
            const HRESULT acquired = texture->keyed_mutex->AcquireSync(0, kProducerAcquireTimeoutMilliseconds);
            if (acquired == S_OK) {
                current_ = texture;
                break;
            }
        }
        if (!current_ || context_ == nullptr) {
            trace_callback("make-current failed: no writable texture");
            render_lock_held_ = false;
            render_mutex_.unlock();
            return false;
        }
        static constexpr FLOAT clear[4]{0.0F, 0.0F, 0.0F, 1.0F};
        context_->ClearRenderTargetView(current_->producer_render_target, clear);
        return true;
    }

    bool select_plane(std::size_t plane, void* output) {
        if (!render_lock_held_ || plane != 0 || !current_ || context_ == nullptr) return false;
        context_->OMSetRenderTargets(1, &current_->producer_render_target, nullptr);
        if (output != nullptr) {
            *static_cast<ID3D11RenderTargetView**>(output) = current_->producer_render_target;
        }
        return true;
    }

    void swap() {
        std::shared_ptr<WindowsTexture> texture;
        {
            std::lock_guard lock(render_mutex_);
            texture = std::move(current_);
        }
        if (!texture) return;
        const auto target = copy_output_target(player_);
        if (target.type != KMEDIAVLC_OUTPUT_WINDOWS_D3D11 || target.generation == 0) return;
        auto frame = std::make_unique<kmediavlc_frame>();
        frame->platform_owner = texture;
        frame->info.output_generation = target.generation;
        frame->info.pts_microseconds = current_position_microseconds(player_);
        frame->info.width = texture->width;
        frame->info.height = texture->height;
        frame->info.pixel_format = texture->format == DXGI_FORMAT_R16G16B16A16_FLOAT
            ? KMEDIAVLC_RGBA16F_LINEAR_SRGB
            : KMEDIAVLC_RGBA8_SRGB;
        frame->info.source_dynamic_range = source_dynamic_range_;
        frame->info.handle_type = KMEDIAVLC_D3D11_SHARED_HANDLE;
        frame->info.platform_handle = reinterpret_cast<std::uintptr_t>(texture->shared_handle);
        frame->info.acquire_fence = -1;
        frame->info.sdr_white_nits = target.sdr_white_nits;
        const float metadata_peak = content_peak_nits_.load(std::memory_order_acquire);
        frame->info.content_peak_nits = source_extended_
            ? (metadata_peak > 0.0F ? metadata_peak : target.display_peak_nits)
            : target.sdr_white_nits;
        frame->info.premultiplied_alpha = true;
        publish_frame(player_, std::move(frame));
        trace_callback("frame published");
    }

    void metadata(libvlc_video_metadata_type_t type, const void* value) {
        if (type != libvlc_video_metadata_frame_hdr10 || value == nullptr) return;
        const auto* metadata = static_cast<const libvlc_video_frame_hdr10_metadata_t*>(value);
        float peak = static_cast<float>(metadata->MaxContentLightLevel);
        if (peak <= 0.0F && metadata->MaxMasteringLuminance != 0) {
            peak = static_cast<float>(metadata->MaxMasteringLuminance) / 10000.0F;
        }
        if (std::isfinite(peak) && peak > 0.0F) content_peak_nits_.store(peak, std::memory_order_release);
    }

    void cleanup() noexcept {
        release_resources();
    }

    void release_resources() noexcept {
        std::lock_guard lock(render_mutex_);
        current_.reset();
        textures_.clear();
        release_interface(conversion_constants_);
        release_interface(conversion_sampler_);
        release_interface(conversion_pixel_shader_);
        release_interface(conversion_vertex_shader_);
        if (callback_context_reference_ && context_ != nullptr) {
            context_->Release();
            callback_context_reference_ = false;
        }
        release_interface(context_);
        release_interface(device_);
        device_luid_ = {};
    }

    kmediavlc_player* player_ = nullptr;
    libvlc_media_player_t* media_player_ = nullptr;
    bool installed_ = false;
    ID3D11Device* device_ = nullptr;
    ID3D11DeviceContext* context_ = nullptr;
    ID3D11VertexShader* conversion_vertex_shader_ = nullptr;
    ID3D11PixelShader* conversion_pixel_shader_ = nullptr;
    ID3D11SamplerState* conversion_sampler_ = nullptr;
    ID3D11Buffer* conversion_constants_ = nullptr;
    bool callback_context_reference_ = false;
    bool source_extended_ = false;
    kmediavlc_source_dynamic_range source_dynamic_range_ =
        KMEDIAVLC_SOURCE_DYNAMIC_RANGE_UNKNOWN;
    LUID device_luid_{};
    std::mutex render_mutex_;
    bool render_lock_held_ = false;
    std::vector<std::shared_ptr<WindowsTexture>> textures_;
    std::shared_ptr<WindowsTexture> current_;
    std::atomic<float> content_peak_nits_{0.0F};
};

} // namespace

std::unique_ptr<PlatformRenderer> create_platform_renderer(kmediavlc_player* player) {
    return std::make_unique<WindowsD3D11Renderer>(player);
}

} // namespace kmediavlc
