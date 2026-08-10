// SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

#include "kmediavlc_client.h"

#include <jni.h>

#include <array>
#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <cstring>
#include <limits>
#include <memory>
#include <mutex>
#include <string>
#include <utility>
#include <vector>

#if !defined(_WIN32)
#  include <unistd.h>
#  if defined(__APPLE__)
#    include <IOSurface/IOSurface.h>
#  endif
#else
#  include <windows.h>
#  include <d3d11.h>
#  include <dxgi1_2.h>
#endif

namespace {

JavaVM* g_java_vm = nullptr;

std::string utf8_from_java(JNIEnv* env, jstring value) {
    if (value == nullptr) return {};
    const jsize length = env->GetStringLength(value);
    const jchar* characters = env->GetStringChars(value, nullptr);
    if (characters == nullptr) return {};
    std::string result;
    result.reserve(static_cast<std::size_t>(length) * 3U);
    for (jsize index = 0; index < length; ++index) {
        std::uint32_t code_point = characters[index];
        if (code_point >= 0xD800U && code_point <= 0xDBFFU && index + 1 < length) {
            const std::uint32_t low = characters[index + 1];
            if (low >= 0xDC00U && low <= 0xDFFFU) {
                code_point = 0x10000U + ((code_point - 0xD800U) << 10U) + (low - 0xDC00U);
                ++index;
            }
        }
        if (code_point <= 0x7FU) {
            result.push_back(static_cast<char>(code_point));
        } else if (code_point <= 0x7FFU) {
            result.push_back(static_cast<char>(0xC0U | (code_point >> 6U)));
            result.push_back(static_cast<char>(0x80U | (code_point & 0x3FU)));
        } else if (code_point <= 0xFFFFU) {
            result.push_back(static_cast<char>(0xE0U | (code_point >> 12U)));
            result.push_back(static_cast<char>(0x80U | ((code_point >> 6U) & 0x3FU)));
            result.push_back(static_cast<char>(0x80U | (code_point & 0x3FU)));
        } else {
            result.push_back(static_cast<char>(0xF0U | (code_point >> 18U)));
            result.push_back(static_cast<char>(0x80U | ((code_point >> 12U) & 0x3FU)));
            result.push_back(static_cast<char>(0x80U | ((code_point >> 6U) & 0x3FU)));
            result.push_back(static_cast<char>(0x80U | (code_point & 0x3FU)));
        }
    }
    env->ReleaseStringChars(value, characters);
    return result;
}

struct AttachedEnvironment final {
    JNIEnv* env = nullptr;
    bool attached = false;

    AttachedEnvironment() {
        if (g_java_vm == nullptr) return;
        const jint status = g_java_vm->GetEnv(reinterpret_cast<void**>(&env), JNI_VERSION_1_8);
        if (status == JNI_EDETACHED) {
#if defined(__ANDROID__)
            if (g_java_vm->AttachCurrentThread(&env, nullptr) == JNI_OK) attached = true;
#else
            if (g_java_vm->AttachCurrentThread(reinterpret_cast<void**>(&env), nullptr) == JNI_OK) attached = true;
#endif
        } else if (status != JNI_OK) {
            env = nullptr;
        }
    }

    ~AttachedEnvironment() {
        if (attached && g_java_vm != nullptr) g_java_vm->DetachCurrentThread();
    }
};

class JniEventSink final {
public:
    JniEventSink(JNIEnv* env, jobject sink) {
        object_ = env->NewGlobalRef(sink);
        if (object_ == nullptr) return;
        jclass type = env->GetObjectClass(sink);
        if (type == nullptr) return;
        frame_available_ = env->GetMethodID(type, "onFrameAvailable", "(JJ)V");
        state_changed_ = env->GetMethodID(type, "onPlaybackStateChanged", "(IJ)V");
        env->DeleteLocalRef(type);
        if (frame_available_ == nullptr || state_changed_ == nullptr) {
            if (env->ExceptionCheck()) env->ExceptionClear();
            env->DeleteGlobalRef(object_);
            object_ = nullptr;
        }
    }

    ~JniEventSink() = default;
    JniEventSink(const JniEventSink&) = delete;
    JniEventSink& operator=(const JniEventSink&) = delete;

    bool valid() const noexcept { return object_ != nullptr; }

    void disable_and_release(JNIEnv* env) {
        enabled_.store(false, std::memory_order_release);
        std::unique_lock lock(active_mutex_);
        active_changed_.wait(lock, [this] { return active_callbacks_.load(std::memory_order_acquire) == 0; });
        if (object_ != nullptr) env->DeleteGlobalRef(object_);
        object_ = nullptr;
    }

    void frame_available(std::uint64_t serial, std::uint64_t generation) {
        invoke(frame_available_, static_cast<jlong>(serial), static_cast<jlong>(generation));
    }

    void state_changed(int state, std::uint64_t generation) {
        invoke(state_changed_, static_cast<jint>(state), static_cast<jlong>(generation));
    }

private:
    class CallbackGuard final {
    public:
        explicit CallbackGuard(JniEventSink& owner) : owner_(owner) {
            if (!owner_.enabled_.load(std::memory_order_acquire)) return;
            owner_.active_callbacks_.fetch_add(1, std::memory_order_acq_rel);
            if (!owner_.enabled_.load(std::memory_order_acquire)) {
                finish();
                return;
            }
            active_ = true;
        }
        ~CallbackGuard() { if (active_) finish(); }
        bool active() const noexcept { return active_; }
    private:
        void finish() {
            active_ = false;
            if (owner_.active_callbacks_.fetch_sub(1, std::memory_order_acq_rel) == 1) {
                std::lock_guard lock(owner_.active_mutex_);
                owner_.active_changed_.notify_all();
            }
        }
        JniEventSink& owner_;
        bool active_ = false;
    };

    template <typename First, typename Second>
    void invoke(jmethodID method, First first, Second second) {
        CallbackGuard guard(*this);
        if (!guard.active() || method == nullptr || object_ == nullptr) return;
        AttachedEnvironment attached;
        if (attached.env == nullptr) return;
        attached.env->CallVoidMethod(object_, method, first, second);
        if (attached.env->ExceptionCheck()) attached.env->ExceptionClear();
    }

    jobject object_ = nullptr;
    jmethodID frame_available_ = nullptr;
    jmethodID state_changed_ = nullptr;
    std::atomic<bool> enabled_{true};
    std::atomic<std::uint32_t> active_callbacks_{0};
    std::mutex active_mutex_;
    std::condition_variable active_changed_;
};

struct JniPlayer final {
    kmediavlc_player* native = nullptr;
    std::unique_ptr<JniEventSink> events;
};

JniPlayer* player_from(jlong value) {
    return reinterpret_cast<JniPlayer*>(static_cast<std::uintptr_t>(value));
}

kmediavlc_frame* frame_from(jlong value) {
    return reinterpret_cast<kmediavlc_frame*>(static_cast<std::uintptr_t>(value));
}

void frame_available_callback(void* opaque, std::uint64_t serial, std::uint64_t generation) {
    auto* sink = static_cast<JniEventSink*>(opaque);
    if (sink != nullptr) sink->frame_available(serial, generation);
}

void state_changed_callback(void* opaque, kmediavlc_playback_state state, std::uint64_t generation) {
    auto* sink = static_cast<JniEventSink*>(opaque);
    if (sink != nullptr) sink->state_changed(static_cast<int>(state), generation);
}

std::vector<std::string> java_strings(JNIEnv* env, jobjectArray values, bool& valid) {
    valid = values != nullptr;
    std::vector<std::string> result;
    if (!valid) return result;
    const jsize count = env->GetArrayLength(values);
    result.reserve(static_cast<std::size_t>(count));
    for (jsize index = 0; index < count; ++index) {
        auto value = static_cast<jstring>(env->GetObjectArrayElement(values, index));
        if (value == nullptr) {
            valid = false;
            return {};
        }
        result.push_back(utf8_from_java(env, value));
        env->DeleteLocalRef(value);
    }
    return result;
}

std::uint64_t float_bits(float value) {
    std::uint32_t bits = 0;
    static_assert(sizeof(bits) == sizeof(value));
    std::memcpy(&bits, &value, sizeof(bits));
    return bits;
}

#if defined(_WIN32)
template <typename Interface>
void release_interface(Interface*& value) noexcept {
    if (value != nullptr) value->Release();
    value = nullptr;
}

bool luid_matches(const LUID& left, std::uint64_t right) {
    const auto packed =
        (static_cast<std::uint64_t>(static_cast<std::uint32_t>(left.HighPart)) << 32U) |
        static_cast<std::uint32_t>(left.LowPart);
    return packed == right;
}

float half_to_float(std::uint16_t value) {
    const std::uint32_t sign = static_cast<std::uint32_t>(value & 0x8000U) << 16U;
    std::uint32_t exponent = (value >> 10U) & 0x1FU;
    std::uint32_t mantissa = value & 0x03FFU;
    std::uint32_t bits = 0;
    if (exponent == 0) {
        if (mantissa == 0) {
            bits = sign;
        } else {
            std::int32_t unbiased = -14;
            while ((mantissa & 0x0400U) == 0) {
                mantissa <<= 1U;
                --unbiased;
            }
            mantissa &= 0x03FFU;
            bits = sign |
                (static_cast<std::uint32_t>(unbiased + 127) << 23U) |
                (mantissa << 13U);
        }
    } else if (exponent == 0x1FU) {
        bits = sign | 0x7F800000U | (mantissa << 13U);
    } else {
        bits = sign | ((exponent + 112U) << 23U) | (mantissa << 13U);
    }
    float result = 0.0F;
    std::memcpy(&result, &bits, sizeof(result));
    return result;
}

bool inspect_shared_fp16(
    std::uint64_t adapter_luid,
    std::uintptr_t shared_handle,
    std::array<float, 7>& output) {
    IDXGIFactory1* factory = nullptr;
    IDXGIAdapter1* adapter = nullptr;
    ID3D11Device* device = nullptr;
    ID3D11DeviceContext* context = nullptr;
    ID3D11Texture2D* texture = nullptr;
    IDXGIKeyedMutex* keyed_mutex = nullptr;
    ID3D11Texture2D* staging = nullptr;
    bool acquired = false;
    bool success = false;
    D3D11_TEXTURE2D_DESC description{};
    D3D11_TEXTURE2D_DESC staging_description{};
    D3D11_MAPPED_SUBRESOURCE mapped{};
    std::uint32_t x = 0;
    std::uint32_t y = 0;
    std::array<float, 4> sample{};

    HRESULT result = CreateDXGIFactory1(__uuidof(IDXGIFactory1), reinterpret_cast<void**>(&factory));
    if (FAILED(result) || factory == nullptr) goto cleanup;
    for (UINT index = 0; ; ++index) {
        IDXGIAdapter1* candidate = nullptr;
        result = factory->EnumAdapters1(index, &candidate);
        if (result == DXGI_ERROR_NOT_FOUND) break;
        if (FAILED(result) || candidate == nullptr) continue;
        DXGI_ADAPTER_DESC1 adapter_description{};
        if (SUCCEEDED(candidate->GetDesc1(&adapter_description)) &&
            luid_matches(adapter_description.AdapterLuid, adapter_luid)) {
            adapter = candidate;
            break;
        }
        candidate->Release();
    }
    if (adapter == nullptr) goto cleanup;
    result = D3D11CreateDevice(
        adapter,
        D3D_DRIVER_TYPE_UNKNOWN,
        nullptr,
        D3D11_CREATE_DEVICE_BGRA_SUPPORT,
        nullptr,
        0,
        D3D11_SDK_VERSION,
        &device,
        nullptr,
        &context);
    if (FAILED(result) || device == nullptr || context == nullptr) goto cleanup;
    result = device->OpenSharedResource(
        reinterpret_cast<HANDLE>(shared_handle),
        __uuidof(ID3D11Texture2D),
        reinterpret_cast<void**>(&texture));
    if (FAILED(result) || texture == nullptr) goto cleanup;
    result = texture->QueryInterface(
        __uuidof(IDXGIKeyedMutex), reinterpret_cast<void**>(&keyed_mutex));
    if (FAILED(result) || keyed_mutex == nullptr) goto cleanup;
    result = keyed_mutex->AcquireSync(0, 1000);
    if (result != S_OK) goto cleanup;
    acquired = true;

    texture->GetDesc(&description);
    if ((description.Format != DXGI_FORMAT_R16G16B16A16_FLOAT &&
         description.Format != DXGI_FORMAT_R8G8B8A8_UNORM) ||
        description.Width == 0 || description.Height == 0) goto cleanup;
    staging_description = description;
    staging_description.Usage = D3D11_USAGE_STAGING;
    staging_description.BindFlags = 0;
    staging_description.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    staging_description.MiscFlags = 0;
    result = device->CreateTexture2D(&staging_description, nullptr, &staging);
    if (FAILED(result) || staging == nullptr) goto cleanup;
    context->CopyResource(staging, texture);
    context->Flush();
    result = context->Map(staging, 0, D3D11_MAP_READ, 0, &mapped);
    if (FAILED(result) || mapped.pData == nullptr) goto cleanup;
    x = description.Width / 2U;
    y = description.Height / 2U;
    if (description.Format == DXGI_FORMAT_R16G16B16A16_FLOAT) {
        const auto* pixel = reinterpret_cast<const std::uint16_t*>(
            static_cast<const std::uint8_t*>(mapped.pData) +
            static_cast<std::size_t>(y) * mapped.RowPitch +
            static_cast<std::size_t>(x) * 8U);
        sample = {
            half_to_float(pixel[0]),
            half_to_float(pixel[1]),
            half_to_float(pixel[2]),
            half_to_float(pixel[3]),
        };
    } else {
        const auto* pixel =
            static_cast<const std::uint8_t*>(mapped.pData) +
            static_cast<std::size_t>(y) * mapped.RowPitch +
            static_cast<std::size_t>(x) * 4U;
        constexpr float inverse_byte = 1.0F / 255.0F;
        sample = {
            pixel[0] * inverse_byte,
            pixel[1] * inverse_byte,
            pixel[2] * inverse_byte,
            pixel[3] * inverse_byte,
        };
    }
    output = {
        static_cast<float>(description.Format),
        static_cast<float>(description.Width),
        static_cast<float>(description.Height),
        sample[0],
        sample[1],
        sample[2],
        sample[3],
    };
    context->Unmap(staging, 0);
    success = true;

cleanup:
    if (acquired) keyed_mutex->ReleaseSync(0);
    release_interface(staging);
    release_interface(keyed_mutex);
    release_interface(texture);
    release_interface(context);
    release_interface(device);
    release_interface(adapter);
    release_interface(factory);
    return success;
}
#endif

} // namespace

extern "C" {

JNIEXPORT jint JNICALL JNI_OnLoad(JavaVM* vm, void*) {
    g_java_vm = vm;
    return JNI_VERSION_1_8;
}

JNIEXPORT jint JNICALL
Java_io_github_shusek_kmediavlc_runtime_desktop_NativeBridge_bridgeAbiVersion(JNIEnv*, jclass) {
    return KMEDIAVLC_BRIDGE_ABI_VERSION;
}

JNIEXPORT jlong JNICALL
Java_io_github_shusek_kmediavlc_runtime_desktop_NativeBridge_defaultWindowsAdapterLuid(JNIEnv*, jclass) {
#if defined(_WIN32)
    IDXGIFactory1* factory = nullptr;
    if (FAILED(CreateDXGIFactory1(__uuidof(IDXGIFactory1), reinterpret_cast<void**>(&factory))) || factory == nullptr) {
        return 0;
    }
    jlong result = 0;
    for (UINT index = 0; ; ++index) {
        IDXGIAdapter1* adapter = nullptr;
        const HRESULT enumerated = factory->EnumAdapters1(index, &adapter);
        if (enumerated == DXGI_ERROR_NOT_FOUND) break;
        if (FAILED(enumerated) || adapter == nullptr) continue;
        DXGI_ADAPTER_DESC1 description{};
        if (SUCCEEDED(adapter->GetDesc1(&description)) &&
            (description.Flags & DXGI_ADAPTER_FLAG_SOFTWARE) == 0) {
            const std::uint64_t packed =
                (static_cast<std::uint64_t>(static_cast<std::uint32_t>(description.AdapterLuid.HighPart)) << 32U) |
                static_cast<std::uint32_t>(description.AdapterLuid.LowPart);
            result = static_cast<jlong>(packed);
            adapter->Release();
            break;
        }
        adapter->Release();
    }
    factory->Release();
    return result;
#else
    return 0;
#endif
}

JNIEXPORT jfloatArray JNICALL
Java_io_github_shusek_kmediavlc_runtime_desktop_NativeBridge_inspectWindowsD3D11Frame(
    JNIEnv* env, jclass, jlong adapter_luid, jlong shared_handle) {
#if defined(_WIN32)
    if (adapter_luid == 0 || shared_handle == 0) return nullptr;
    std::array<float, 7> values{};
    if (!inspect_shared_fp16(
            static_cast<std::uint64_t>(adapter_luid),
            static_cast<std::uintptr_t>(shared_handle),
            values)) return nullptr;
    jfloatArray result = env->NewFloatArray(static_cast<jsize>(values.size()));
    if (result != nullptr) {
        env->SetFloatArrayRegion(result, 0, static_cast<jsize>(values.size()), values.data());
    }
    return result;
#else
    (void)env;
    (void)adapter_luid;
    (void)shared_handle;
    return nullptr;
#endif
}

JNIEXPORT jlongArray JNICALL
Java_io_github_shusek_kmediavlc_runtime_desktop_NativeBridge_inspectMacIosurfaceFrame(
    JNIEnv* env, jclass, jlong surface_id) {
#if defined(__APPLE__)
    if (surface_id <= 0 || surface_id > std::numeric_limits<std::uint32_t>::max()) return nullptr;
    IOSurfaceRef surface = IOSurfaceLookup(static_cast<IOSurfaceID>(surface_id));
    if (surface == nullptr) return nullptr;
    const jlong values[6]{
        static_cast<jlong>(IOSurfaceGetWidth(surface)),
        static_cast<jlong>(IOSurfaceGetHeight(surface)),
        static_cast<jlong>(IOSurfaceGetBytesPerElement(surface)),
        static_cast<jlong>(IOSurfaceGetBytesPerRow(surface)),
        static_cast<jlong>(IOSurfaceGetAllocSize(surface)),
        static_cast<jlong>(IOSurfaceGetPixelFormat(surface)),
    };
    CFRelease(surface);
    jlongArray result = env->NewLongArray(6);
    if (result != nullptr) env->SetLongArrayRegion(result, 0, 6, values);
    return result;
#else
    (void)env;
    (void)surface_id;
    return nullptr;
#endif
}

JNIEXPORT jlong JNICALL
Java_io_github_shusek_kmediavlc_runtime_desktop_NativeBridge_createPlayer(
    JNIEnv* env,
    jclass,
    jstring libvlc_path,
    jstring plugin_directory,
    jint delivery_mode,
    jboolean request_hdr,
    jfloat sdr_white_nits,
    jfloat display_peak_nits,
    jobject event_sink) {
    if (event_sink == nullptr) return 0;
    auto wrapper = std::make_unique<JniPlayer>();
    wrapper->events = std::make_unique<JniEventSink>(env, event_sink);
    if (!wrapper->events->valid()) return 0;
    const std::string libvlc = utf8_from_java(env, libvlc_path);
    const std::string plugins = utf8_from_java(env, plugin_directory);
    kmediavlc_player_config config{};
    config.struct_size = sizeof(config);
    config.bridge_abi_version = KMEDIAVLC_BRIDGE_ABI_VERSION;
    config.libvlc_path_utf8 = libvlc.c_str();
    config.plugin_directory_utf8 = plugins.c_str();
    config.delivery_mode = static_cast<kmediavlc_delivery_mode>(delivery_mode);
    config.request_hdr = request_hdr == JNI_TRUE;
    config.sdr_white_nits = sdr_white_nits;
    config.display_peak_nits = display_peak_nits;
    config.frame_available = frame_available_callback;
    config.playback_state_changed = state_changed_callback;
    config.callback_opaque = wrapper->events.get();
    wrapper->native = kmediavlc_player_create(&config);
    if (wrapper->native == nullptr) {
        wrapper->events->disable_and_release(env);
        return 0;
    }
    return static_cast<jlong>(reinterpret_cast<std::uintptr_t>(wrapper.release()));
}

JNIEXPORT jboolean JNICALL
Java_io_github_shusek_kmediavlc_runtime_desktop_NativeBridge_open(
    JNIEnv* env,
    jclass,
    jlong player_handle,
    jstring uri,
    jobjectArray header_values,
    jboolean autoplay) {
    auto* player = player_from(player_handle);
    if (player == nullptr || player->native == nullptr) return JNI_FALSE;
    bool valid = false;
    auto strings = java_strings(env, header_values, valid);
    if (!valid) return JNI_FALSE;
    std::vector<const char*> pointers;
    pointers.reserve(strings.size());
    for (const auto& value : strings) pointers.push_back(value.c_str());
    const std::string uri_value = utf8_from_java(env, uri);
    return kmediavlc_player_open(
        player->native,
        uri_value.c_str(),
        pointers.empty() ? nullptr : pointers.data(),
        pointers.size(),
        autoplay == JNI_TRUE) ? JNI_TRUE : JNI_FALSE;
}

#define KMEDIAVLC_JNI_SIMPLE(name, native_name) \
JNIEXPORT jboolean JNICALL \
Java_io_github_shusek_kmediavlc_runtime_desktop_NativeBridge_##name( \
    JNIEnv*, jclass, jlong player_handle) { \
    auto* player = player_from(player_handle); \
    return player != nullptr && player->native != nullptr && native_name(player->native) ? JNI_TRUE : JNI_FALSE; \
}

KMEDIAVLC_JNI_SIMPLE(play, kmediavlc_player_play)
KMEDIAVLC_JNI_SIMPLE(pause, kmediavlc_player_pause)
KMEDIAVLC_JNI_SIMPLE(stop, kmediavlc_player_stop)

#undef KMEDIAVLC_JNI_SIMPLE

JNIEXPORT jboolean JNICALL
Java_io_github_shusek_kmediavlc_runtime_desktop_NativeBridge_seek(
    JNIEnv*, jclass, jlong player_handle, jlong time, jboolean fast) {
    auto* player = player_from(player_handle);
    return player != nullptr && player->native != nullptr &&
        kmediavlc_player_seek(player->native, time, fast == JNI_TRUE) ? JNI_TRUE : JNI_FALSE;
}

JNIEXPORT jboolean JNICALL
Java_io_github_shusek_kmediavlc_runtime_desktop_NativeBridge_setVolume(
    JNIEnv*, jclass, jlong player_handle, jfloat volume) {
    auto* player = player_from(player_handle);
    return player != nullptr && player->native != nullptr &&
        kmediavlc_player_set_volume(player->native, volume) ? JNI_TRUE : JNI_FALSE;
}

JNIEXPORT jboolean JNICALL
Java_io_github_shusek_kmediavlc_runtime_desktop_NativeBridge_setRate(
    JNIEnv*, jclass, jlong player_handle, jfloat rate) {
    auto* player = player_from(player_handle);
    return player != nullptr && player->native != nullptr &&
        kmediavlc_player_set_rate(player->native, rate) ? JNI_TRUE : JNI_FALSE;
}

JNIEXPORT jboolean JNICALL
Java_io_github_shusek_kmediavlc_runtime_desktop_NativeBridge_setLoop(
    JNIEnv*, jclass, jlong player_handle, jboolean loop) {
    auto* player = player_from(player_handle);
    return player != nullptr && player->native != nullptr &&
        kmediavlc_player_set_loop(player->native, loop == JNI_TRUE) ? JNI_TRUE : JNI_FALSE;
}

JNIEXPORT jboolean JNICALL
Java_io_github_shusek_kmediavlc_runtime_desktop_NativeBridge_resize(
    JNIEnv*, jclass, jlong player_handle, jint width, jint height) {
    auto* player = player_from(player_handle);
    return player != nullptr && player->native != nullptr && width > 0 && height > 0 &&
        kmediavlc_player_resize(player->native, static_cast<std::uint32_t>(width), static_cast<std::uint32_t>(height))
        ? JNI_TRUE : JNI_FALSE;
}

JNIEXPORT jboolean JNICALL
Java_io_github_shusek_kmediavlc_runtime_desktop_NativeBridge_updateOutput(
    JNIEnv* env,
    jclass,
    jlong player_handle,
    jint target_type,
    jlong generation,
    jint width,
    jint height,
    jboolean request_hdr,
    jfloat sdr_white_nits,
    jfloat peak_nits,
    jlong device_handle,
    jlong command_queue,
    jstring render_node,
    jintArray drm_formats,
    jlongArray drm_modifiers,
    jboolean acquire_fences,
    jboolean release_fences) {
    auto* player = player_from(player_handle);
    if (player == nullptr || player->native == nullptr || generation < 0 || width < 0 || height < 0) return JNI_FALSE;
    const jsize format_count = drm_formats == nullptr ? 0 : env->GetArrayLength(drm_formats);
    const jsize modifier_count = drm_modifiers == nullptr ? 0 : env->GetArrayLength(drm_modifiers);
    if (format_count != modifier_count) return JNI_FALSE;
    std::vector<jint> formats(static_cast<std::size_t>(format_count));
    std::vector<jlong> modifiers(static_cast<std::size_t>(modifier_count));
    if (format_count != 0) {
        env->GetIntArrayRegion(drm_formats, 0, format_count, formats.data());
        env->GetLongArrayRegion(drm_modifiers, 0, modifier_count, modifiers.data());
        if (env->ExceptionCheck()) return JNI_FALSE;
    }
    std::vector<kmediavlc_drm_format_modifier> pairs;
    pairs.reserve(static_cast<std::size_t>(format_count));
    for (jsize index = 0; index < format_count; ++index) {
        pairs.push_back({static_cast<std::uint32_t>(formats[static_cast<std::size_t>(index)]),
                         static_cast<std::uint64_t>(modifiers[static_cast<std::size_t>(index)])});
    }
    const std::string node = utf8_from_java(env, render_node);
    kmediavlc_output_target target{};
    target.struct_size = sizeof(target);
    target.bridge_abi_version = KMEDIAVLC_BRIDGE_ABI_VERSION;
    target.type = static_cast<kmediavlc_output_target_type>(target_type);
    target.generation = static_cast<std::uint64_t>(generation);
    target.width = static_cast<std::uint32_t>(width);
    target.height = static_cast<std::uint32_t>(height);
    target.request_hdr = request_hdr == JNI_TRUE;
    target.sdr_white_nits = sdr_white_nits;
    target.display_peak_nits = peak_nits;
    target.adapter_luid = static_cast<std::uint64_t>(device_handle);
    target.metal_device = static_cast<std::uintptr_t>(device_handle);
    target.metal_command_queue = static_cast<std::uintptr_t>(command_queue);
    target.render_node_utf8 = node.empty() ? nullptr : node.c_str();
    target.drm_formats = pairs.empty() ? nullptr : pairs.data();
    target.drm_format_count = pairs.size();
    target.acquire_fences = acquire_fences == JNI_TRUE;
    target.release_fences = release_fences == JNI_TRUE;
    return kmediavlc_player_update_output(player->native, &target) ? JNI_TRUE : JNI_FALSE;
}

JNIEXPORT jlongArray JNICALL
Java_io_github_shusek_kmediavlc_runtime_desktop_NativeBridge_snapshot(
    JNIEnv* env, jclass, jlong player_handle) {
    auto* player = player_from(player_handle);
    if (player == nullptr || player->native == nullptr) return nullptr;
    kmediavlc_player_snapshot snapshot{};
    snapshot.struct_size = sizeof(snapshot);
    snapshot.bridge_abi_version = KMEDIAVLC_BRIDGE_ABI_VERSION;
    if (!kmediavlc_player_get_snapshot(player->native, &snapshot)) return nullptr;
    const jlong values[8]{
        static_cast<jlong>(snapshot.state),
        static_cast<jlong>(snapshot.media_generation),
        static_cast<jlong>(snapshot.position_microseconds),
        static_cast<jlong>(snapshot.duration_microseconds),
        static_cast<jlong>(snapshot.video_width),
        static_cast<jlong>(snapshot.video_height),
        static_cast<jlong>(snapshot.buffered_permille),
        snapshot.seekable ? 1 : 0,
    };
    jlongArray result = env->NewLongArray(8);
    if (result != nullptr) env->SetLongArrayRegion(result, 0, 8, values);
    return result;
}

JNIEXPORT jlongArray JNICALL
Java_io_github_shusek_kmediavlc_runtime_desktop_NativeBridge_acquireLatestFrame(
    JNIEnv* env, jclass, jlong player_handle) {
    auto* player = player_from(player_handle);
    if (player == nullptr || player->native == nullptr) return nullptr;
    kmediavlc_frame_info info{};
    info.struct_size = sizeof(info);
    info.bridge_abi_version = KMEDIAVLC_BRIDGE_ABI_VERSION;
    kmediavlc_frame* frame = kmediavlc_player_acquire_latest_frame(player->native, &info);
    if (frame == nullptr) return nullptr;
    const jlong values[19]{
        static_cast<jlong>(reinterpret_cast<std::uintptr_t>(frame)),
        static_cast<jlong>(info.serial),
        static_cast<jlong>(info.output_generation),
        static_cast<jlong>(info.pts_microseconds),
        static_cast<jlong>(info.width),
        static_cast<jlong>(info.height),
        static_cast<jlong>(info.pixel_format),
        static_cast<jlong>(info.source_dynamic_range),
        static_cast<jlong>(info.handle_type),
        static_cast<jlong>(info.platform_handle),
        static_cast<jlong>(info.acquire_fence),
        static_cast<jlong>(info.stride),
        static_cast<jlong>(info.fourcc),
        static_cast<jlong>(info.offset),
        static_cast<jlong>(info.modifier),
        static_cast<jlong>(float_bits(info.sdr_white_nits)),
        static_cast<jlong>(float_bits(info.content_peak_nits)),
        info.premultiplied_alpha ? 1 : 0,
        static_cast<jlong>(info.cpu_byte_count),
    };
    jlongArray result = env->NewLongArray(19);
    if (result == nullptr) {
#if !defined(_WIN32)
        if (info.acquire_fence >= 0) close(static_cast<int>(info.acquire_fence));
#endif
        kmediavlc_frame_release(frame, -1);
        return nullptr;
    }
    env->SetLongArrayRegion(result, 0, 19, values);
    if (env->ExceptionCheck()) {
#if !defined(_WIN32)
        if (info.acquire_fence >= 0) close(static_cast<int>(info.acquire_fence));
#endif
        kmediavlc_frame_release(frame, -1);
        return nullptr;
    }
    return result;
}

JNIEXPORT jobject JNICALL
Java_io_github_shusek_kmediavlc_runtime_desktop_NativeBridge_cpuFrameBuffer(
    JNIEnv* env, jclass, jlong frame_handle) {
    auto* frame = frame_from(frame_handle);
    std::size_t bytes = 0;
    const void* pixels = kmediavlc_frame_cpu_pixels(frame, &bytes);
    if (pixels == nullptr || bytes > static_cast<std::size_t>(std::numeric_limits<jlong>::max())) return nullptr;
    return env->NewDirectByteBuffer(const_cast<void*>(pixels), static_cast<jlong>(bytes));
}

JNIEXPORT jstring JNICALL
Java_io_github_shusek_kmediavlc_runtime_desktop_NativeBridge_lastError(
    JNIEnv* env, jclass, jlong player_handle) {
    auto* player = player_from(player_handle);
    if (player == nullptr || player->native == nullptr) return nullptr;
    const char* error = kmediavlc_player_last_error(player->native);
    return error == nullptr ? nullptr : env->NewStringUTF(error);
}

JNIEXPORT void JNICALL
Java_io_github_shusek_kmediavlc_runtime_desktop_NativeBridge_destroyPlayer(
    JNIEnv* env, jclass, jlong player_handle) {
    std::unique_ptr<JniPlayer> player(player_from(player_handle));
    if (!player) return;
    if (player->native != nullptr) kmediavlc_player_destroy(player->native);
    player->native = nullptr;
    if (player->events) player->events->disable_and_release(env);
}

JNIEXPORT void JNICALL
Java_io_github_shusek_kmediavlc_runtime_desktop_NativeBridge_releaseFrame(
    JNIEnv*, jclass, jlong frame_handle, jint release_fence) {
    kmediavlc_frame_release(frame_from(frame_handle), release_fence);
}

JNIEXPORT void JNICALL
Java_io_github_shusek_kmediavlc_runtime_desktop_NativeBridge_closeFence(
    JNIEnv*, jclass, jint fence) {
#if !defined(_WIN32)
    if (fence >= 0) close(fence);
#else
    (void)fence;
#endif
}

} // extern "C"
