// SPDX-License-Identifier: LGPL-2.1-or-later

#include "bridge_internal.hpp"

#include <CoreFoundation/CoreFoundation.h>
#include <CoreVideo/CVPixelBuffer.h>
#include <IOSurface/IOSurface.h>
#include <OpenGL/CGLIOSurface.h>
#include <OpenGL/OpenGL.h>
#include <OpenGL/gl3.h>

#include <dlfcn.h>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <limits>
#include <memory>
#include <mutex>
#include <string>
#include <utility>
#include <vector>

namespace kmediavlc {
namespace {

constexpr std::size_t kSurfaceCount = 4;

bool debug_callbacks_enabled() {
    static const bool enabled = [] {
        const char* value = std::getenv("KMEDIAVLC_DEBUG_CALLBACKS");
        return value != nullptr && value[0] == '1' && value[1] == '\0';
    }();
    return enabled;
}

void trace_render_config(
    const libvlc_video_render_cfg_t* config,
    bool request_hdr) {
    if (!debug_callbacks_enabled()) return;
    if (config == nullptr) {
        std::fprintf(
            stderr,
            "[KMediaVlc macOS] render-config=null request-hdr=%d\n",
            request_hdr ? 1 : 0);
    } else {
        std::fprintf(
            stderr,
            "[KMediaVlc macOS] render-config width=%u height=%u bitdepth=%u "
            "full-range=%d colorspace=%d primaries=%d transfer=%d request-hdr=%d\n",
            config->width,
            config->height,
            config->bitdepth,
            config->full_range ? 1 : 0,
            static_cast<int>(config->colorspace),
            static_cast<int>(config->primaries),
            static_cast<int>(config->transfer),
            request_hdr ? 1 : 0);
    }
    std::fflush(stderr);
}

class MacOpenGlContext final {
public:
    ~MacOpenGlContext() {
        std::lock_guard lock(mutex);
        if (context != nullptr) CGLReleaseContext(context);
        context = nullptr;
    }

    MacOpenGlContext(const MacOpenGlContext&) = delete;
    MacOpenGlContext& operator=(const MacOpenGlContext&) = delete;
    MacOpenGlContext() = default;

    bool create(std::string& error) {
        std::lock_guard lock(mutex);
        if (context != nullptr) return true;
        CGLPixelFormatAttribute accelerated_attributes[] = {
            kCGLPFAAccelerated,
            kCGLPFAAllowOfflineRenderers,
            kCGLPFAOpenGLProfile,
            static_cast<CGLPixelFormatAttribute>(kCGLOGLPVersion_3_2_Core),
            static_cast<CGLPixelFormatAttribute>(0),
        };
        CGLPixelFormatAttribute offline_attributes[] = {
            kCGLPFAAllowOfflineRenderers,
            kCGLPFAOpenGLProfile,
            static_cast<CGLPixelFormatAttribute>(kCGLOGLPVersion_3_2_Core),
            static_cast<CGLPixelFormatAttribute>(0),
        };
        const auto try_create = [this](CGLPixelFormatAttribute* attributes) {
            CGLPixelFormatObj pixel_format = nullptr;
            GLint pixel_format_count = 0;
            const CGLError choose_result =
                CGLChoosePixelFormat(attributes, &pixel_format, &pixel_format_count);
            if (choose_result != kCGLNoError || pixel_format == nullptr || pixel_format_count == 0) {
                if (pixel_format != nullptr) CGLReleasePixelFormat(pixel_format);
                return false;
            }
            const CGLError create_result = CGLCreateContext(pixel_format, nullptr, &context);
            CGLReleasePixelFormat(pixel_format);
            if (create_result == kCGLNoError && context != nullptr) return true;
            if (context != nullptr) CGLReleaseContext(context);
            context = nullptr;
            return false;
        };
        if (try_create(accelerated_attributes) || try_create(offline_attributes)) return true;
        error = "No usable macOS OpenGL 3.2 producer context is available.";
        return false;
    }

    std::recursive_mutex mutex;
    CGLContextObj context = nullptr;
};

class ScopedCurrentContext final {
public:
    explicit ScopedCurrentContext(std::shared_ptr<MacOpenGlContext> context)
        : context_(std::move(context)), lock_(context_->mutex), previous_(CGLGetCurrentContext()) {
        current_ = context_->context != nullptr &&
            CGLSetCurrentContext(context_->context) == kCGLNoError;
    }

    ~ScopedCurrentContext() {
        if (current_) CGLSetCurrentContext(previous_);
    }

    ScopedCurrentContext(const ScopedCurrentContext&) = delete;
    ScopedCurrentContext& operator=(const ScopedCurrentContext&) = delete;

    explicit operator bool() const noexcept { return current_; }

private:
    std::shared_ptr<MacOpenGlContext> context_;
    std::unique_lock<std::recursive_mutex> lock_;
    CGLContextObj previous_ = nullptr;
    bool current_ = false;
};

void set_number(CFMutableDictionaryRef properties, CFStringRef key, std::int64_t value) {
    CFNumberRef number = CFNumberCreate(kCFAllocatorDefault, kCFNumberSInt64Type, &value);
    if (number != nullptr) {
        CFDictionarySetValue(properties, key, number);
        CFRelease(number);
    }
}

IOSurfaceRef create_io_surface(
    std::uint32_t width,
    std::uint32_t height,
    bool floating_point,
    std::string& error) {
    const std::size_t bytes_per_element = floating_point ? 8U : 4U;
    if (width == 0 || height == 0 ||
        static_cast<std::size_t>(width) > std::numeric_limits<std::size_t>::max() / bytes_per_element) {
        error = "The requested IOSurface dimensions are invalid.";
        return nullptr;
    }
    const std::size_t unaligned_row_bytes = static_cast<std::size_t>(width) * bytes_per_element;
    const std::size_t row_bytes = IOSurfaceAlignProperty(kIOSurfaceBytesPerRow, unaligned_row_bytes);
    if (row_bytes == 0 || static_cast<std::size_t>(height) >
            std::numeric_limits<std::size_t>::max() / row_bytes) {
        error = "The requested IOSurface allocation is too large.";
        return nullptr;
    }
    const std::size_t allocation_size = row_bytes * static_cast<std::size_t>(height);
    if (bytes_per_element > static_cast<std::size_t>(std::numeric_limits<std::int64_t>::max()) ||
        row_bytes > static_cast<std::size_t>(std::numeric_limits<std::int64_t>::max()) ||
        allocation_size > static_cast<std::size_t>(std::numeric_limits<std::int64_t>::max())) {
        error = "The requested IOSurface allocation exceeds its property range.";
        return nullptr;
    }
    CFMutableDictionaryRef properties = CFDictionaryCreateMutable(
        kCFAllocatorDefault,
        0,
        &kCFTypeDictionaryKeyCallBacks,
        &kCFTypeDictionaryValueCallBacks);
    if (properties == nullptr) {
        error = "The IOSurface property dictionary could not be created.";
        return nullptr;
    }
    set_number(properties, kIOSurfaceWidth, width);
    set_number(properties, kIOSurfaceHeight, height);
    set_number(
        properties,
        kIOSurfaceBytesPerElement,
        static_cast<std::int64_t>(bytes_per_element));
    set_number(properties, kIOSurfaceBytesPerRow, static_cast<std::int64_t>(row_bytes));
    set_number(properties, kIOSurfaceAllocSize, static_cast<std::int64_t>(allocation_size));
    set_number(
        properties,
        kIOSurfacePixelFormat,
        floating_point ? kCVPixelFormatType_64RGBAHalf : kCVPixelFormatType_32BGRA);
    IOSurfaceRef surface = IOSurfaceCreate(properties);
    CFRelease(properties);
    if (surface == nullptr || IOSurfaceGetID(surface) == 0) {
        if (surface != nullptr) CFRelease(surface);
        error = "The shareable macOS IOSurface could not be allocated.";
        return nullptr;
    }
    return surface;
}

class MacSurface final {
public:
    ~MacSurface() {
        if (context != nullptr && (framebuffer != 0 || texture != 0)) {
            ScopedCurrentContext current(context);
            if (current) {
                if (framebuffer != 0) glDeleteFramebuffers(1, &framebuffer);
                if (texture != 0) glDeleteTextures(1, &texture);
            }
        }
        if (surface != nullptr) CFRelease(surface);
        surface = nullptr;
    }

    MacSurface(const MacSurface&) = delete;
    MacSurface& operator=(const MacSurface&) = delete;
    MacSurface() = default;

    std::shared_ptr<MacOpenGlContext> context;
    IOSurfaceRef surface = nullptr;
    GLuint texture = 0;
    GLuint framebuffer = 0;
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    bool floating_point = false;
    std::uint32_t fourcc = 0;
    std::uint32_t stride = 0;
    std::uint64_t output_generation = 0;
    float sdr_white_nits = 203.0F;
    float display_peak_nits = 203.0F;
    kmediavlc_source_dynamic_range source_dynamic_range = KMEDIAVLC_SOURCE_DYNAMIC_RANGE_UNKNOWN;
    bool source_extended = false;
};

kmediavlc_source_dynamic_range source_dynamic_range(
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

class MacIosurfaceRenderer final : public PlatformRenderer {
public:
    explicit MacIosurfaceRenderer(kmediavlc_player* player) : player_(player) {}
    ~MacIosurfaceRenderer() override { release_resources(); }

    bool install(libvlc_media_player_t* media_player, std::string& error) override {
        context_ = std::make_shared<MacOpenGlContext>();
        if (!context_->create(error)) {
            context_.reset();
            return false;
        }
        media_player_ = media_player;
        if (!player_->api->video_set_output_callbacks(
                media_player,
                libvlc_video_engine_opengl,
                setup_callback,
                cleanup_callback,
                window_callback,
                update_output_callback,
                swap_callback,
                make_current_callback,
                get_proc_address_callback,
                nullptr,
                nullptr,
                this)) {
            error = "The pinned libVLC 4 runtime rejected macOS OpenGL texture callbacks.";
            media_player_ = nullptr;
            context_.reset();
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
            get_proc_address_callback,
            nullptr,
            nullptr,
            this);
        installed_ = false;
        media_player_ = nullptr;
        // libvlc_media_player_switch_vout() may finish tearing down the old GL
        // display while libvlc_media_player_release() runs. Keep this callback
        // object and its CGL context alive until the bridge releases the media
        // player; the renderer destructor performs the final resource cleanup.
    }

    bool output_target_changed(const OutputTargetSnapshot& target, std::string& error) override {
        if (target.type == KMEDIAVLC_OUTPUT_UNAVAILABLE) return true;
        if (target.type != KMEDIAVLC_OUTPUT_MACOS_IOSURFACE || target.generation == 0 ||
            target.width == 0 || target.height == 0 || target.metal_device == 0 ||
            target.metal_command_queue == 0) {
            error = "The macOS IOSurface producer target is incomplete.";
            return false;
        }
        return true;
    }

    bool resize(std::uint32_t width, std::uint32_t height) override {
        return width != 0 && height != 0;
    }

private:
    static bool setup_callback(
        void** opaque,
        const libvlc_video_setup_device_cfg_t*,
        libvlc_video_setup_device_info_t* output) {
        if (opaque == nullptr || *opaque == nullptr || output == nullptr) return false;
        *output = {};
        return static_cast<MacIosurfaceRenderer*>(*opaque)->context_ != nullptr;
    }

    static void cleanup_callback(void* opaque) {
        if (opaque != nullptr) static_cast<MacIosurfaceRenderer*>(opaque)->release_resources();
    }

    static void window_callback(
        void* opaque,
        libvlc_video_output_resize_cb resize,
        libvlc_video_output_mouse_move_cb,
        libvlc_video_output_mouse_press_cb,
        libvlc_video_output_mouse_release_cb,
        void* report_opaque) {
        if (opaque != nullptr) {
            static_cast<MacIosurfaceRenderer*>(opaque)->set_resize_reporter(resize, report_opaque);
        }
    }

    static bool update_output_callback(
        void* opaque,
        const libvlc_video_render_cfg_t* config,
        libvlc_video_output_cfg_t* output) {
        return opaque != nullptr &&
            static_cast<MacIosurfaceRenderer*>(opaque)->update_output(config, output);
    }

    static void swap_callback(void* opaque) {
        if (opaque != nullptr) static_cast<MacIosurfaceRenderer*>(opaque)->swap();
    }

    static bool make_current_callback(void* opaque, bool enter) {
        return opaque != nullptr && static_cast<MacIosurfaceRenderer*>(opaque)->make_current(enter);
    }

    static void* get_proc_address_callback(void*, const char* name) {
        return name == nullptr ? nullptr : dlsym(RTLD_DEFAULT, name);
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
        if (output == nullptr || context_ == nullptr) return false;
        const auto target = copy_output_target(player_);
        if (target.type != KMEDIAVLC_OUTPUT_MACOS_IOSURFACE || target.generation == 0 ||
            target.width == 0 || target.height == 0 || target.metal_device == 0 ||
            target.metal_command_queue == 0) {
            set_error(player_, "The active macOS TextureView output is unavailable.");
            return false;
        }

        ScopedCurrentContext current(context_);
        if (!current) {
            set_error(player_, "The macOS OpenGL producer context could not be made current.");
            return false;
        }
        GLint maximum_texture_size = 0;
        glGetIntegerv(GL_MAX_TEXTURE_SIZE, &maximum_texture_size);
        if (maximum_texture_size <= 0 ||
            target.width > static_cast<std::uint32_t>(maximum_texture_size) ||
            target.height > static_cast<std::uint32_t>(maximum_texture_size)) {
            set_error(player_, "The requested macOS IOSurface exceeds the OpenGL texture limit.");
            return false;
        }
        trace_render_config(config, target.request_hdr);
        source_dynamic_range_ = source_dynamic_range(config);
        source_extended_ = source_dynamic_range_ == KMEDIAVLC_SOURCE_DYNAMIC_RANGE_HDR10 ||
            source_dynamic_range_ == KMEDIAVLC_SOURCE_DYNAMIC_RANGE_HLG;
        const bool hdr_output = target.request_hdr && source_extended_;
        if (!ensure_surfaces(target.width, target.height, hdr_output)) return false;
        if (render_lock_held_ && current_surface_ == nullptr && !bind_writable_surface()) {
            set_error(player_, "No writable IOSurface is available for the libVLC producer.");
            return false;
        }

        player_->video_width.store(target.width, std::memory_order_release);
        player_->video_height.store(target.height, std::memory_order_release);
        output->u.opengl_format = GL_RGBA;
        output->full_range = true;
        output->colorspace = libvlc_video_colorspace_BT709;
        output->primaries = libvlc_video_primaries_BT709;
        output->transfer = hdr_output
            ? libvlc_video_transfer_func_LINEAR
            : libvlc_video_transfer_func_SRGB;
        output->orientation = libvlc_video_orient_top_left;
        return true;
    }

    bool ensure_surfaces(std::uint32_t width, std::uint32_t height, bool floating_point) {
        if (!surfaces_.empty() && surfaces_.front()->width == width &&
            surfaces_.front()->height == height &&
            surfaces_.front()->floating_point == floating_point) {
            return true;
        }
        std::vector<std::shared_ptr<MacSurface>> replacement;
        replacement.reserve(kSurfaceCount);
        for (std::size_t index = 0; index < kSurfaceCount; ++index) {
            auto surface = create_surface(width, height, floating_point);
            if (!surface) return false;
            replacement.push_back(std::move(surface));
        }
        current_surface_.reset();
        surfaces_ = std::move(replacement);
        return true;
    }

    std::shared_ptr<MacSurface> create_surface(
        std::uint32_t width,
        std::uint32_t height,
        bool floating_point) {
        auto result = std::make_shared<MacSurface>();
        result->context = context_;
        std::string error;
        result->surface = create_io_surface(width, height, floating_point, error);
        if (result->surface == nullptr) {
            set_error(player_, std::move(error));
            return {};
        }

        glGenTextures(1, &result->texture);
        glBindTexture(GL_TEXTURE_RECTANGLE, result->texture);
        glTexParameteri(GL_TEXTURE_RECTANGLE, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_RECTANGLE, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_RECTANGLE, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
        glTexParameteri(GL_TEXTURE_RECTANGLE, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
        const GLenum internal_format = floating_point ? GL_RGBA16F : GL_RGBA8;
        const GLenum format = floating_point ? GL_RGBA : GL_BGRA;
        const GLenum type = floating_point ? GL_HALF_FLOAT : GL_UNSIGNED_INT_8_8_8_8_REV;
        if (result->texture == 0 || CGLTexImageIOSurface2D(
                context_->context,
                GL_TEXTURE_RECTANGLE,
                internal_format,
                static_cast<GLsizei>(width),
                static_cast<GLsizei>(height),
                format,
                type,
                result->surface,
                0) != kCGLNoError) {
            set_error(player_, "The IOSurface could not be bound as an OpenGL texture.");
            return {};
        }

        glGenFramebuffers(1, &result->framebuffer);
        glBindFramebuffer(GL_FRAMEBUFFER, result->framebuffer);
        glFramebufferTexture2D(
            GL_FRAMEBUFFER,
            GL_COLOR_ATTACHMENT0,
            GL_TEXTURE_RECTANGLE,
            result->texture,
            0);
        if (result->framebuffer == 0 || glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) {
            set_error(player_, "The IOSurface OpenGL framebuffer is incomplete.");
            return {};
        }
        result->width = width;
        result->height = height;
        result->floating_point = floating_point;
        result->fourcc = floating_point
            ? kCVPixelFormatType_64RGBAHalf
            : kCVPixelFormatType_32BGRA;
        const std::size_t row_bytes = IOSurfaceGetBytesPerRow(result->surface);
        if (row_bytes == 0 || row_bytes > std::numeric_limits<std::uint32_t>::max()) {
            set_error(player_, "The IOSurface row stride is invalid.");
            return {};
        }
        result->stride = static_cast<std::uint32_t>(row_bytes);
        return result;
    }

    bool bind_writable_surface() {
        current_surface_.reset();
        for (const auto& surface : surfaces_) {
            if (surface.use_count() == 1) {
                current_surface_ = surface;
                break;
            }
        }
        if (!current_surface_) return false;
        const auto target = copy_output_target(player_);
        const bool floating_point = target.request_hdr && source_extended_;
        if (target.type != KMEDIAVLC_OUTPUT_MACOS_IOSURFACE || target.generation == 0 ||
            target.width != current_surface_->width || target.height != current_surface_->height ||
            floating_point != current_surface_->floating_point) {
            current_surface_.reset();
            return false;
        }
        current_surface_->output_generation = target.generation;
        current_surface_->sdr_white_nits = target.sdr_white_nits;
        current_surface_->display_peak_nits = target.display_peak_nits;
        current_surface_->source_dynamic_range = source_dynamic_range_;
        current_surface_->source_extended = source_extended_;
        glBindFramebuffer(GL_FRAMEBUFFER, current_surface_->framebuffer);
        glViewport(
            0,
            0,
            static_cast<GLsizei>(current_surface_->width),
            static_cast<GLsizei>(current_surface_->height));
        static constexpr GLfloat clear[4]{0.0F, 0.0F, 0.0F, 1.0F};
        glClearBufferfv(GL_COLOR, 0, clear);
        return glGetError() == GL_NO_ERROR;
    }

    bool rebind_current_surface() {
        if (current_surface_ == nullptr) return false;
        glBindFramebuffer(GL_FRAMEBUFFER, current_surface_->framebuffer);
        glViewport(
            0,
            0,
            static_cast<GLsizei>(current_surface_->width),
            static_cast<GLsizei>(current_surface_->height));
        return glGetError() == GL_NO_ERROR;
    }

    bool make_current(bool enter) {
        if (context_ == nullptr || context_->context == nullptr) return false;
        if (!enter) {
            if (!render_lock_held_) return false;
            glFlush();
            CGLSetCurrentContext(previous_context_);
            previous_context_ = nullptr;
            render_lock_held_ = false;
            context_->mutex.unlock();
            return true;
        }
        context_->mutex.lock();
        previous_context_ = CGLGetCurrentContext();
        if (CGLSetCurrentContext(context_->context) != kCGLNoError) {
            previous_context_ = nullptr;
            context_->mutex.unlock();
            return false;
        }
        render_lock_held_ = true;
        if (surfaces_.empty()) return true;
        // vgl enters the callback context once to render and then a second time
        // from VglSwapBuffers. The rendered surface must survive that second
        // entry: selecting and clearing a new writable surface here would erase
        // the completed frame immediately before swap_callback publishes it.
        if (current_surface_ != nullptr && rebind_current_surface()) return true;
        if (bind_writable_surface()) return true;
        CGLSetCurrentContext(previous_context_);
        previous_context_ = nullptr;
        render_lock_held_ = false;
        context_->mutex.unlock();
        return false;
    }

    void swap() {
        if (context_ == nullptr) return;
        std::shared_ptr<MacSurface> surface;
        {
            std::lock_guard lock(context_->mutex);
            if (!render_lock_held_ || CGLGetCurrentContext() != context_->context) return;
            // TextureView consumes this IOSurface through Metal and macOS exposes no
            // cross-API acquire fence for the OpenGL callback path. Finish the producer
            // commands before publishing; otherwise Metal can sample a surface while VLC
            // is still drawing it, which appears as intermittent black/partial frames.
            glFinish();
            if (debug_callbacks_enabled() && current_surface_ != nullptr) {
                const std::uint64_t frame_index = ++debug_frame_index_;
                if (frame_index <= 12 || frame_index % 120 == 0) {
                    static constexpr float positions[][2]{
                        {0.25F, 0.25F},
                        {0.75F, 0.25F},
                        {0.50F, 0.50F},
                        {0.25F, 0.75F},
                        {0.75F, 0.75F},
                    };
                    float maximum_rgb = 0.0F;
                    float minimum_alpha = std::numeric_limits<float>::max();
                    float maximum_alpha = 0.0F;
                    GLenum read_error = GL_NO_ERROR;
                    while (glGetError() != GL_NO_ERROR) {}
                    for (const auto& position : positions) {
                        const GLint x = static_cast<GLint>(
                            position[0] * static_cast<float>(current_surface_->width - 1));
                        const GLint y = static_cast<GLint>(
                            position[1] * static_cast<float>(current_surface_->height - 1));
                        GLfloat pixel[4]{};
                        glReadPixels(x, y, 1, 1, GL_RGBA, GL_FLOAT, pixel);
                        maximum_rgb = std::max(
                            maximum_rgb,
                            std::max({std::abs(pixel[0]), std::abs(pixel[1]), std::abs(pixel[2])}));
                        minimum_alpha = std::min(minimum_alpha, pixel[3]);
                        maximum_alpha = std::max(maximum_alpha, pixel[3]);
                    }
                    read_error = glGetError();
                    std::fprintf(
                        stderr,
                        "[KMediaVlc macOS] producer-frame=%llu max-rgb=%.6f "
                        "alpha=[%.6f,%.6f] gl-error=0x%x format=%s\n",
                        static_cast<unsigned long long>(frame_index),
                        maximum_rgb,
                        minimum_alpha,
                        maximum_alpha,
                        static_cast<unsigned>(read_error),
                        current_surface_->floating_point ? "rgba16f" : "bgra8");
                    std::fflush(stderr);
                }
            }
            surface = std::move(current_surface_);
        }
        if (!surface) return;
        if (surface->output_generation == 0) return;
        auto frame = std::make_unique<kmediavlc_frame>();
        frame->platform_owner = surface;
        frame->info.output_generation = surface->output_generation;
        frame->info.pts_microseconds = current_position_microseconds(player_);
        frame->info.width = surface->width;
        frame->info.height = surface->height;
        frame->info.pixel_format = surface->floating_point
            ? KMEDIAVLC_RGBA16F_LINEAR_SRGB
            : KMEDIAVLC_RGBA8_SRGB;
        frame->info.source_dynamic_range = surface->source_dynamic_range;
        frame->info.handle_type = KMEDIAVLC_IOSURFACE_ID;
        frame->info.platform_handle = IOSurfaceGetID(surface->surface);
        frame->info.acquire_fence = -1;
        frame->info.stride = surface->stride;
        frame->info.fourcc = surface->fourcc;
        frame->info.sdr_white_nits = surface->sdr_white_nits;
        frame->info.content_peak_nits = surface->source_extended
            ? surface->display_peak_nits
            : surface->sdr_white_nits;
        frame->info.premultiplied_alpha = true;
        publish_frame(player_, std::move(frame));
    }

    void release_resources() noexcept {
        if (context_ == nullptr) return;
        std::lock_guard lock(context_->mutex);
        current_surface_.reset();
        surfaces_.clear();
    }

    kmediavlc_player* player_ = nullptr;
    libvlc_media_player_t* media_player_ = nullptr;
    bool installed_ = false;
    std::shared_ptr<MacOpenGlContext> context_;
    std::vector<std::shared_ptr<MacSurface>> surfaces_;
    std::shared_ptr<MacSurface> current_surface_;
    bool render_lock_held_ = false;
    CGLContextObj previous_context_ = nullptr;
    kmediavlc_source_dynamic_range source_dynamic_range_ = KMEDIAVLC_SOURCE_DYNAMIC_RANGE_UNKNOWN;
    bool source_extended_ = false;
    std::uint64_t debug_frame_index_ = 0;
};

} // namespace

std::unique_ptr<PlatformRenderer> create_platform_renderer(kmediavlc_player* player) {
    return std::make_unique<MacIosurfaceRenderer>(player);
}

} // namespace kmediavlc
