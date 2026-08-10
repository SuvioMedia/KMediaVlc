// SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

#include "bridge_internal.hpp"

#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <GLES2/gl2ext.h>
#include <drm_fourcc.h>
#include <gbm.h>
#include <xf86drm.h>

#include <dlfcn.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

#include <algorithm>
#include <array>
#include <cstdint>
#include <limits>
#include <memory>
#include <mutex>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#ifndef EGL_PLATFORM_GBM_KHR
#  define EGL_PLATFORM_GBM_KHR 0x31D7
#endif

namespace kmediavlc {
namespace {

constexpr std::size_t kSurfaceCount = 4;
constexpr std::size_t kMaximumAdvertisedFormats = 256;
constexpr std::uint32_t kMaximumOutputDimension = 16'384;
constexpr std::uint32_t kOutputDrmFormat = DRM_FORMAT_ABGR8888;

void close_fd(int& descriptor) noexcept {
    if (descriptor >= 0) close(descriptor);
    descriptor = -1;
}

bool has_extension(const char* extensions, std::string_view expected) {
    if (extensions == nullptr || expected.empty() || expected.find(' ') != std::string_view::npos) {
        return false;
    }
    const std::string_view available(extensions);
    std::size_t position = 0;
    while ((position = available.find(expected, position)) != std::string_view::npos) {
        const bool starts_token = position == 0 || available[position - 1U] == ' ';
        const std::size_t end = position + expected.size();
        const bool ends_token = end == available.size() || available[end] == ' ';
        if (starts_token && ends_token) return true;
        position = end;
    }
    return false;
}

template <typename Function>
Function egl_function(const char* name) {
    return reinterpret_cast<Function>(eglGetProcAddress(name));
}

class LinuxEglContext final {
public:
    using GbmCreateWithModifiers2 = gbm_bo* (*)(
        gbm_device*,
        std::uint32_t,
        std::uint32_t,
        std::uint32_t,
        const std::uint64_t*,
        unsigned int,
        std::uint32_t);

    ~LinuxEglContext() {
        std::lock_guard lock(mutex);
        if (display != EGL_NO_DISPLAY) {
            if (eglGetCurrentDisplay() == display && eglGetCurrentContext() == context) {
                eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
            }
            if (pbuffer != EGL_NO_SURFACE) eglDestroySurface(display, pbuffer);
            if (context != EGL_NO_CONTEXT) eglDestroyContext(display, context);
            eglTerminate(display);
        }
        pbuffer = EGL_NO_SURFACE;
        context = EGL_NO_CONTEXT;
        display = EGL_NO_DISPLAY;
        if (gbm != nullptr) gbm_device_destroy(gbm);
        gbm = nullptr;
        close_fd(render_node_fd);
    }

    LinuxEglContext(const LinuxEglContext&) = delete;
    LinuxEglContext& operator=(const LinuxEglContext&) = delete;

    static std::shared_ptr<LinuxEglContext> create(
        const OutputTargetSnapshot& target,
        std::string& error) {
        auto result = std::shared_ptr<LinuxEglContext>(new LinuxEglContext());
        if (!result->initialize(target, error)) return {};
        return result;
    }

    bool supports_requested_fences(const OutputTargetSnapshot& target) const noexcept {
        return (!target.acquire_fences && !target.release_fences) || explicit_fences;
    }

    bool choose_modifier(const OutputTargetSnapshot& target, std::uint64_t& selected, std::string& error) {
        if (query_dma_buf_formats == nullptr || query_dma_buf_modifiers == nullptr) {
            error = "The Linux EGL driver cannot enumerate concrete DMA-BUF modifiers.";
            return false;
        }
        EGLint format_count = 0;
        if (query_dma_buf_formats(display, 0, nullptr, &format_count) != EGL_TRUE ||
            format_count <= 0 || format_count > 4'096) {
            error = "The Linux EGL driver returned an invalid DMA-BUF format inventory.";
            return false;
        }
        std::vector<EGLint> formats(static_cast<std::size_t>(format_count));
        EGLint populated_formats = 0;
        if (query_dma_buf_formats(display, format_count, formats.data(), &populated_formats) != EGL_TRUE ||
            populated_formats <= 0 || populated_formats > format_count ||
            std::find(
                formats.begin(),
                formats.begin() + populated_formats,
                static_cast<EGLint>(kOutputDrmFormat)) == formats.begin() + populated_formats) {
            error = "The Linux EGL driver cannot import renderable ABGR8888 DMA-BUFs.";
            return false;
        }

        EGLint modifier_count = 0;
        if (query_dma_buf_modifiers(
                display,
                static_cast<EGLint>(kOutputDrmFormat),
                0,
                nullptr,
                nullptr,
                &modifier_count) != EGL_TRUE ||
            modifier_count <= 0 || modifier_count > 4'096) {
            error = "The Linux EGL driver returned an invalid ABGR8888 modifier inventory.";
            return false;
        }
        std::vector<EGLuint64KHR> modifiers(static_cast<std::size_t>(modifier_count));
        std::vector<EGLBoolean> external_only(static_cast<std::size_t>(modifier_count));
        EGLint populated_modifiers = 0;
        if (query_dma_buf_modifiers(
                display,
                static_cast<EGLint>(kOutputDrmFormat),
                modifier_count,
                modifiers.data(),
                external_only.data(),
                &populated_modifiers) != EGL_TRUE ||
            populated_modifiers <= 0 || populated_modifiers > modifier_count) {
            error = "The Linux EGL driver could not enumerate ABGR8888 modifiers.";
            return false;
        }

        for (const auto& consumer : target.drm_formats) {
            if (consumer.format != kOutputDrmFormat) continue;
            for (EGLint index = 0; index < populated_modifiers; ++index) {
                const auto position = static_cast<std::size_t>(index);
                if (external_only[position] == EGL_FALSE &&
                    static_cast<std::uint64_t>(modifiers[position]) == consumer.modifier) {
                    selected = consumer.modifier;
                    return true;
                }
            }
        }
        error = "The producer and consumer have no renderable ABGR8888 DMA-BUF modifier in common.";
        return false;
    }

    bool wait_for_release_fence(int descriptor) noexcept {
        if (descriptor < 0) return true;
        if (!explicit_fences || create_sync == nullptr || destroy_sync == nullptr || wait_sync == nullptr) {
            close_fd(descriptor);
            return false;
        }
        const EGLint attributes[]{
            EGL_SYNC_NATIVE_FENCE_FD_ANDROID,
            descriptor,
            EGL_NONE,
        };
        const EGLSyncKHR sync = create_sync(display, EGL_SYNC_NATIVE_FENCE_ANDROID, attributes);
        // EGL_ANDROID_native_fence_sync transfers descriptor ownership when it
        // is passed to eglCreateSyncKHR, including on an error path.
        descriptor = -1;
        if (sync == EGL_NO_SYNC_KHR) return false;
        const bool waited = wait_sync(display, sync, 0) == EGL_TRUE;
        destroy_sync(display, sync);
        return waited;
    }

    int create_acquire_fence() noexcept {
        if (!explicit_fences || create_sync == nullptr || destroy_sync == nullptr ||
            duplicate_native_fence == nullptr) {
            return -1;
        }
        const EGLint attributes[]{
            EGL_SYNC_NATIVE_FENCE_FD_ANDROID,
            EGL_NO_NATIVE_FENCE_FD_ANDROID,
            EGL_NONE,
        };
        const EGLSyncKHR sync = create_sync(display, EGL_SYNC_NATIVE_FENCE_ANDROID, attributes);
        if (sync == EGL_NO_SYNC_KHR) return -1;
        glFlush();
        const int descriptor = duplicate_native_fence(display, sync);
        destroy_sync(display, sync);
        if (descriptor < 0) return -1;
        if (fcntl(descriptor, F_SETFD, FD_CLOEXEC) == -1) {
            int owned = descriptor;
            close_fd(owned);
            return -1;
        }
        return descriptor;
    }

    std::recursive_mutex mutex;
    std::string render_node;
    int render_node_fd = -1;
    gbm_device* gbm = nullptr;
    EGLDisplay display = EGL_NO_DISPLAY;
    EGLContext context = EGL_NO_CONTEXT;
    EGLSurface pbuffer = EGL_NO_SURFACE;
    GLint maximum_texture_size = 0;
    bool explicit_fences = false;
    PFNEGLCREATEIMAGEKHRPROC create_image = nullptr;
    PFNEGLDESTROYIMAGEKHRPROC destroy_image = nullptr;
    PFNEGLQUERYDMABUFFORMATSEXTPROC query_dma_buf_formats = nullptr;
    PFNEGLQUERYDMABUFMODIFIERSEXTPROC query_dma_buf_modifiers = nullptr;
    PFNEGLCREATESYNCKHRPROC create_sync = nullptr;
    PFNEGLDESTROYSYNCKHRPROC destroy_sync = nullptr;
    PFNEGLWAITSYNCKHRPROC wait_sync = nullptr;
    PFNEGLDUPNATIVEFENCEFDANDROIDPROC duplicate_native_fence = nullptr;
    PFNGLEGLIMAGETARGETTEXTURE2DOESPROC image_target_texture = nullptr;
    GbmCreateWithModifiers2 create_bo_with_modifiers = nullptr;

private:
    LinuxEglContext() = default;

    bool initialize(const OutputTargetSnapshot& target, std::string& error) {
        int open_flags = O_RDWR | O_CLOEXEC;
#if defined(O_NOFOLLOW)
        open_flags |= O_NOFOLLOW;
#endif
        render_node_fd = open(target.render_node.c_str(), open_flags);
        if (render_node_fd < 0) {
            error = "The requested Linux DRM render node could not be opened safely.";
            return false;
        }
        struct stat status {};
        if (fstat(render_node_fd, &status) != 0 || !S_ISCHR(status.st_mode) ||
            drmGetNodeTypeFromFd(render_node_fd) != DRM_NODE_RENDER) {
            error = "The requested Linux DRM path is not a render node.";
            return false;
        }
        render_node = target.render_node;
        gbm = gbm_create_device(render_node_fd);
        if (gbm == nullptr) {
            error = "GBM could not create a device for the requested render node.";
            return false;
        }

        const char* client_extensions = eglQueryString(EGL_NO_DISPLAY, EGL_EXTENSIONS);
        if (!has_extension(client_extensions, "EGL_KHR_platform_gbm") &&
            !has_extension(client_extensions, "EGL_MESA_platform_gbm")) {
            error = "The Linux EGL loader does not advertise the GBM platform.";
            return false;
        }
        const auto get_platform_display =
            egl_function<PFNEGLGETPLATFORMDISPLAYEXTPROC>("eglGetPlatformDisplayEXT");
        if (get_platform_display == nullptr) {
            error = "The Linux EGL loader is missing eglGetPlatformDisplayEXT.";
            return false;
        }
        display = get_platform_display(EGL_PLATFORM_GBM_KHR, gbm, nullptr);
        EGLint major = 0;
        EGLint minor = 0;
        if (display == EGL_NO_DISPLAY || eglInitialize(display, &major, &minor) != EGL_TRUE) {
            error = "The Linux GBM EGL display could not be initialized.";
            return false;
        }
        if (eglBindAPI(EGL_OPENGL_ES_API) != EGL_TRUE) {
            error = "The Linux EGL driver could not bind OpenGL ES.";
            return false;
        }

        const EGLint config_attributes[]{
            EGL_SURFACE_TYPE,
            EGL_PBUFFER_BIT,
            EGL_RENDERABLE_TYPE,
            EGL_OPENGL_ES2_BIT,
            EGL_RED_SIZE,
            8,
            EGL_GREEN_SIZE,
            8,
            EGL_BLUE_SIZE,
            8,
            EGL_ALPHA_SIZE,
            8,
            EGL_NONE,
        };
        EGLConfig config = nullptr;
        EGLint config_count = 0;
        if (eglChooseConfig(display, config_attributes, &config, 1, &config_count) != EGL_TRUE ||
            config_count != 1 || config == nullptr) {
            error = "The Linux EGL driver has no GLES2 pbuffer configuration.";
            return false;
        }
        const EGLint context_attributes[]{EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE};
        context = eglCreateContext(display, config, EGL_NO_CONTEXT, context_attributes);
        const EGLint pbuffer_attributes[]{EGL_WIDTH, 1, EGL_HEIGHT, 1, EGL_NONE};
        pbuffer = eglCreatePbufferSurface(display, config, pbuffer_attributes);
        if (context == EGL_NO_CONTEXT || pbuffer == EGL_NO_SURFACE ||
            eglMakeCurrent(display, pbuffer, pbuffer, context) != EGL_TRUE) {
            error = "The Linux GLES2 producer context could not be created.";
            return false;
        }

        const char* display_extensions = eglQueryString(display, EGL_EXTENSIONS);
        const char* gl_extensions = reinterpret_cast<const char*>(glGetString(GL_EXTENSIONS));
        const bool dma_buf_extensions =
            has_extension(display_extensions, "EGL_KHR_image_base") &&
            has_extension(display_extensions, "EGL_EXT_image_dma_buf_import") &&
            has_extension(display_extensions, "EGL_EXT_image_dma_buf_import_modifiers") &&
            has_extension(gl_extensions, "GL_OES_EGL_image");
        if (!dma_buf_extensions) {
            eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
            error = "The Linux EGL/GLES2 driver lacks the required DMA-BUF image extensions.";
            return false;
        }

        create_image = egl_function<PFNEGLCREATEIMAGEKHRPROC>("eglCreateImageKHR");
        destroy_image = egl_function<PFNEGLDESTROYIMAGEKHRPROC>("eglDestroyImageKHR");
        query_dma_buf_formats =
            egl_function<PFNEGLQUERYDMABUFFORMATSEXTPROC>("eglQueryDmaBufFormatsEXT");
        query_dma_buf_modifiers =
            egl_function<PFNEGLQUERYDMABUFMODIFIERSEXTPROC>("eglQueryDmaBufModifiersEXT");
        image_target_texture = egl_function<PFNGLEGLIMAGETARGETTEXTURE2DOESPROC>(
            "glEGLImageTargetTexture2DOES");
        create_bo_with_modifiers = reinterpret_cast<GbmCreateWithModifiers2>(
            dlsym(RTLD_DEFAULT, "gbm_bo_create_with_modifiers2"));
        if (create_image == nullptr || destroy_image == nullptr || query_dma_buf_formats == nullptr ||
            query_dma_buf_modifiers == nullptr || image_target_texture == nullptr ||
            create_bo_with_modifiers == nullptr) {
            eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
            error = "The Linux graphics stack is missing a required DMA-BUF entry point.";
            return false;
        }

        create_sync = egl_function<PFNEGLCREATESYNCKHRPROC>("eglCreateSyncKHR");
        destroy_sync = egl_function<PFNEGLDESTROYSYNCKHRPROC>("eglDestroySyncKHR");
        wait_sync = egl_function<PFNEGLWAITSYNCKHRPROC>("eglWaitSyncKHR");
        duplicate_native_fence = egl_function<PFNEGLDUPNATIVEFENCEFDANDROIDPROC>(
            "eglDupNativeFenceFDANDROID");
        explicit_fences =
            has_extension(display_extensions, "EGL_ANDROID_native_fence_sync") &&
            has_extension(display_extensions, "EGL_KHR_wait_sync") &&
            create_sync != nullptr && destroy_sync != nullptr && wait_sync != nullptr &&
            duplicate_native_fence != nullptr;
        glGetIntegerv(GL_MAX_TEXTURE_SIZE, &maximum_texture_size);
        const GLenum gl_error = glGetError();
        eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
        if (gl_error != GL_NO_ERROR || maximum_texture_size <= 0) {
            error = "The Linux GLES2 driver returned an invalid texture limit.";
            return false;
        }
        if (!supports_requested_fences(target)) {
            error = "The Linux EGL driver lacks the requested explicit sync-file support.";
            return false;
        }
        return true;
    }
};

class LinuxSurface final {
public:
    ~LinuxSurface() {
        close_fd(acquire_fence_fd);
        {
            std::lock_guard lock(release_mutex);
            close_fd(release_fence_fd);
        }
        if (egl_context != nullptr) {
            std::lock_guard lock(egl_context->mutex);
            const EGLDisplay previous_display = eglGetCurrentDisplay();
            const EGLContext previous_context = eglGetCurrentContext();
            const EGLSurface previous_draw = eglGetCurrentSurface(EGL_DRAW);
            const EGLSurface previous_read = eglGetCurrentSurface(EGL_READ);
            const bool current = eglMakeCurrent(
                egl_context->display,
                egl_context->pbuffer,
                egl_context->pbuffer,
                egl_context->context) == EGL_TRUE;
            if (current) {
                if (framebuffer != 0) glDeleteFramebuffers(1, &framebuffer);
                if (texture != 0) glDeleteTextures(1, &texture);
                if (previous_display != EGL_NO_DISPLAY && previous_context != EGL_NO_CONTEXT) {
                    eglMakeCurrent(previous_display, previous_draw, previous_read, previous_context);
                } else {
                    eglMakeCurrent(
                        egl_context->display,
                        EGL_NO_SURFACE,
                        EGL_NO_SURFACE,
                        EGL_NO_CONTEXT);
                }
            }
            if (image != EGL_NO_IMAGE_KHR && egl_context->destroy_image != nullptr) {
                egl_context->destroy_image(egl_context->display, image);
            }
        }
        image = EGL_NO_IMAGE_KHR;
        framebuffer = 0;
        texture = 0;
        if (bo != nullptr) gbm_bo_destroy(bo);
        bo = nullptr;
    }

    LinuxSurface(const LinuxSurface&) = delete;
    LinuxSurface& operator=(const LinuxSurface&) = delete;
    LinuxSurface() = default;

    std::shared_ptr<LinuxEglContext> egl_context;
    gbm_bo* bo = nullptr;
    EGLImageKHR image = EGL_NO_IMAGE_KHR;
    GLuint texture = 0;
    GLuint framebuffer = 0;
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    std::uint32_t stride = 0;
    std::uint32_t offset = 0;
    std::uint64_t modifier = DRM_FORMAT_MOD_INVALID;
    std::uint64_t output_generation = 0;
    float sdr_white_nits = 203.0F;
    kmediavlc_source_dynamic_range source_dynamic_range = KMEDIAVLC_SOURCE_DYNAMIC_RANGE_UNKNOWN;
    int acquire_fence_fd = -1;

    std::mutex release_mutex;
    int release_fence_fd = -1;
    bool release_fence_required = false;
    bool retired = false;
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

class LinuxDmaBufRenderer final : public PlatformRenderer {
public:
    explicit LinuxDmaBufRenderer(kmediavlc_player* player) : player_(player) {}
    ~LinuxDmaBufRenderer() override { release_resources(); }

    bool install(libvlc_media_player_t* media_player, std::string& error) override {
        media_player_ = media_player;
        if (!player_->api->video_set_output_callbacks(
                media_player,
                libvlc_video_engine_gles2,
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
            error = "The pinned libVLC 4 runtime rejected Linux GLES2 texture callbacks.";
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
            get_proc_address_callback,
            nullptr,
            nullptr,
            this);
        installed_ = false;
        media_player_ = nullptr;
    }

    bool output_target_changed(const OutputTargetSnapshot& target, std::string& error) override {
        std::lock_guard lock(state_mutex_);
        if (target.type == KMEDIAVLC_OUTPUT_UNAVAILABLE) {
            release_resources_locked();
            return true;
        }
        if (!validate_target(target, error)) return false;

        std::shared_ptr<LinuxEglContext> candidate = context_;
        if (candidate == nullptr || candidate->render_node != target.render_node) {
            candidate = LinuxEglContext::create(target, error);
            if (candidate == nullptr) return false;
        }
        if (!candidate->supports_requested_fences(target)) {
            error = "The Linux EGL driver lacks the requested explicit sync-file support.";
            return false;
        }
        std::uint64_t selected = DRM_FORMAT_MOD_INVALID;
        if (!candidate->choose_modifier(target, selected, error)) return false;

        if (candidate != context_) {
            current_surface_.reset();
            surfaces_.clear();
            context_ = std::move(candidate);
        }
        selected_modifier_ = selected;
        return true;
    }

    bool resize(std::uint32_t width, std::uint32_t height) override {
        return width != 0 && height != 0 && width <= kMaximumOutputDimension &&
            height <= kMaximumOutputDimension;
    }

private:
    static bool setup_callback(
        void** opaque,
        const libvlc_video_setup_device_cfg_t*,
        libvlc_video_setup_device_info_t* output) {
        if (opaque == nullptr || *opaque == nullptr || output == nullptr) return false;
        *output = {};
        return static_cast<LinuxDmaBufRenderer*>(*opaque)->setup();
    }

    static void cleanup_callback(void* opaque) {
        if (opaque != nullptr) static_cast<LinuxDmaBufRenderer*>(opaque)->release_resources();
    }

    static void window_callback(
        void* opaque,
        libvlc_video_output_resize_cb resize,
        libvlc_video_output_mouse_move_cb,
        libvlc_video_output_mouse_press_cb,
        libvlc_video_output_mouse_release_cb,
        void* report_opaque) {
        if (opaque != nullptr) {
            static_cast<LinuxDmaBufRenderer*>(opaque)->set_resize_reporter(resize, report_opaque);
        }
    }

    static bool update_output_callback(
        void* opaque,
        const libvlc_video_render_cfg_t* config,
        libvlc_video_output_cfg_t* output) {
        return opaque != nullptr &&
            static_cast<LinuxDmaBufRenderer*>(opaque)->update_output(config, output);
    }

    static void swap_callback(void* opaque) {
        if (opaque != nullptr) static_cast<LinuxDmaBufRenderer*>(opaque)->swap();
    }

    static bool make_current_callback(void* opaque, bool enter) {
        return opaque != nullptr && static_cast<LinuxDmaBufRenderer*>(opaque)->make_current(enter);
    }

    static void* get_proc_address_callback(void*, const char* name) {
        if (name == nullptr) return nullptr;
        const auto egl_address = eglGetProcAddress(name);
        if (egl_address != nullptr) return reinterpret_cast<void*>(egl_address);
        return dlsym(RTLD_DEFAULT, name);
    }

    static void release_surface_callback(
        void* opaque,
        std::intptr_t release_fence,
        bool acquired) noexcept {
        auto* surface = static_cast<LinuxSurface*>(opaque);
        if (surface == nullptr) {
            if (release_fence >= 0 &&
                release_fence <= static_cast<std::intptr_t>(std::numeric_limits<int>::max())) {
                int descriptor = static_cast<int>(release_fence);
                close_fd(descriptor);
            }
            return;
        }
        std::lock_guard lock(surface->release_mutex);
        if (release_fence >= 0 &&
            release_fence <= static_cast<std::intptr_t>(std::numeric_limits<int>::max())) {
            int descriptor = static_cast<int>(release_fence);
            if (acquired && surface->release_fence_fd < 0) {
                surface->release_fence_fd = descriptor;
            } else {
                close_fd(descriptor);
            }
        } else if (acquired && surface->release_fence_required) {
            surface->retired = true;
        }
    }

    bool validate_target(const OutputTargetSnapshot& target, std::string& error) const {
        if (target.type != KMEDIAVLC_OUTPUT_LINUX_DMABUF || target.generation == 0 ||
            target.width == 0 || target.height == 0 || target.width > kMaximumOutputDimension ||
            target.height > kMaximumOutputDimension || target.render_node.empty() ||
            target.render_node.front() != '/' || target.render_node.size() >= 4'096U ||
            target.drm_formats.empty() || target.drm_formats.size() > kMaximumAdvertisedFormats) {
            error = "The Linux DMA-BUF producer target is incomplete or out of range.";
            return false;
        }
        bool has_output_format = false;
        for (std::size_t index = 0; index < target.drm_formats.size(); ++index) {
            const auto& candidate = target.drm_formats[index];
            if (candidate.modifier == DRM_FORMAT_MOD_INVALID) {
                error = "Linux DMA-BUF negotiation requires concrete DRM modifiers.";
                return false;
            }
            if (candidate.format == kOutputDrmFormat) has_output_format = true;
            for (std::size_t prior = 0; prior < index; ++prior) {
                if (target.drm_formats[prior].format == candidate.format &&
                    target.drm_formats[prior].modifier == candidate.modifier) {
                    error = "The Linux DMA-BUF format/modifier list contains duplicates.";
                    return false;
                }
            }
        }
        if (!has_output_format) {
            error = "The Linux consumer does not advertise ABGR8888 DMA-BUF import.";
            return false;
        }
        return true;
    }

    bool setup() {
        std::lock_guard lock(state_mutex_);
        const auto target = copy_output_target(player_);
        std::string error;
        if (!validate_target(target, error)) {
            set_error(player_, std::move(error));
            return false;
        }
        if (context_ == nullptr || context_->render_node != target.render_node) {
            auto replacement = LinuxEglContext::create(target, error);
            if (replacement == nullptr) {
                set_error(player_, std::move(error));
                return false;
            }
            context_ = std::move(replacement);
        }
        if (!context_->supports_requested_fences(target) ||
            !context_->choose_modifier(target, selected_modifier_, error)) {
            if (error.empty()) error = "The Linux output requires unsupported explicit fences.";
            set_error(player_, std::move(error));
            return false;
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
        if (output == nullptr || context_ == nullptr) return false;
        const auto target = copy_output_target(player_);
        std::string error;
        if (!validate_target(target, error) || context_->render_node != target.render_node ||
            !context_->supports_requested_fences(target)) {
            if (error.empty()) error = "The active Linux DMA-BUF output is unavailable.";
            set_error(player_, std::move(error));
            return false;
        }
        std::uint64_t selected = DRM_FORMAT_MOD_INVALID;
        if (!context_->choose_modifier(target, selected, error)) {
            set_error(player_, std::move(error));
            return false;
        }
        selected_modifier_ = selected;
        if (target.width > static_cast<std::uint32_t>(context_->maximum_texture_size) ||
            target.height > static_cast<std::uint32_t>(context_->maximum_texture_size)) {
            set_error(player_, "The requested Linux DMA-BUF exceeds the GLES2 texture limit.");
            return false;
        }
        source_dynamic_range_ = source_dynamic_range(config);
        if (!ensure_surfaces(target.width, target.height, selected_modifier_)) return false;
        if (render_lock_held_ && current_surface_ == nullptr && !bind_writable_surface()) {
            set_error(player_, "No reusable Linux DMA-BUF is available for the libVLC producer.");
            return false;
        }

        player_->video_width.store(target.width, std::memory_order_release);
        player_->video_height.store(target.height, std::memory_order_release);
        output->u.opengl_format = GL_RGBA;
        output->full_range = true;
        output->colorspace = libvlc_video_colorspace_BT709;
        output->primaries = libvlc_video_primaries_BT709;
        // The bounded Linux transport is SDR ABGR8888. libVLC performs any
        // HDR-to-SDR conversion before the texture is exported.
        output->transfer = libvlc_video_transfer_func_SRGB;
        output->orientation = libvlc_video_orient_top_left;
        return true;
    }

    bool ensure_surfaces(
        std::uint32_t width,
        std::uint32_t height,
        std::uint64_t modifier) {
        if (!surfaces_.empty() && surfaces_.front()->width == width &&
            surfaces_.front()->height == height && surfaces_.front()->modifier == modifier) {
            return true;
        }
        std::vector<std::shared_ptr<LinuxSurface>> replacement;
        replacement.reserve(kSurfaceCount);
        for (std::size_t index = 0; index < kSurfaceCount; ++index) {
            auto surface = create_surface(width, height, modifier);
            if (surface == nullptr) return false;
            replacement.push_back(std::move(surface));
        }
        current_surface_.reset();
        surfaces_ = std::move(replacement);
        return true;
    }

    std::shared_ptr<LinuxSurface> create_surface(
        std::uint32_t width,
        std::uint32_t height,
        std::uint64_t modifier) {
        if (context_ == nullptr || context_->create_bo_with_modifiers == nullptr) return {};
        auto result = std::make_shared<LinuxSurface>();
        result->egl_context = context_;
        const std::array<std::uint64_t, 1> modifiers{modifier};
        result->bo = context_->create_bo_with_modifiers(
            context_->gbm,
            width,
            height,
            kOutputDrmFormat,
            modifiers.data(),
            static_cast<unsigned int>(modifiers.size()),
            GBM_BO_USE_RENDERING);
        if (result->bo == nullptr || gbm_bo_get_plane_count(result->bo) != 1 ||
            gbm_bo_get_modifier(result->bo) != modifier) {
            set_error(player_, "GBM could not allocate the negotiated single-plane DMA-BUF modifier.");
            return {};
        }
        const std::uint32_t stride = gbm_bo_get_stride_for_plane(result->bo, 0);
        const std::uint32_t offset = gbm_bo_get_offset(result->bo, 0);
        if (stride == 0 || stride > static_cast<std::uint32_t>(std::numeric_limits<EGLint>::max()) ||
            offset > static_cast<std::uint32_t>(std::numeric_limits<EGLint>::max())) {
            set_error(player_, "GBM returned an invalid DMA-BUF plane layout.");
            return {};
        }
        int import_fd = gbm_bo_get_fd(result->bo);
        if (import_fd < 0) {
            set_error(player_, "GBM could not export the producer buffer for EGL import.");
            return {};
        }
        const EGLint attributes[]{
            EGL_WIDTH,
            static_cast<EGLint>(width),
            EGL_HEIGHT,
            static_cast<EGLint>(height),
            EGL_LINUX_DRM_FOURCC_EXT,
            static_cast<EGLint>(kOutputDrmFormat),
            EGL_DMA_BUF_PLANE0_FD_EXT,
            import_fd,
            EGL_DMA_BUF_PLANE0_OFFSET_EXT,
            static_cast<EGLint>(offset),
            EGL_DMA_BUF_PLANE0_PITCH_EXT,
            static_cast<EGLint>(stride),
            EGL_DMA_BUF_PLANE0_MODIFIER_LO_EXT,
            static_cast<EGLint>(modifier & 0xffff'ffffULL),
            EGL_DMA_BUF_PLANE0_MODIFIER_HI_EXT,
            static_cast<EGLint>(modifier >> 32U),
            EGL_NONE,
        };
        result->image = context_->create_image(
            context_->display,
            EGL_NO_CONTEXT,
            EGL_LINUX_DMA_BUF_EXT,
            nullptr,
            attributes);
        close_fd(import_fd);
        if (result->image == EGL_NO_IMAGE_KHR) {
            set_error(player_, "EGL rejected the negotiated GBM DMA-BUF image.");
            return {};
        }

        while (glGetError() != GL_NO_ERROR) {}
        glGenTextures(1, &result->texture);
        glBindTexture(GL_TEXTURE_2D, result->texture);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
        context_->image_target_texture(GL_TEXTURE_2D, result->image);
        glGenFramebuffers(1, &result->framebuffer);
        glBindFramebuffer(GL_FRAMEBUFFER, result->framebuffer);
        glFramebufferTexture2D(
            GL_FRAMEBUFFER,
            GL_COLOR_ATTACHMENT0,
            GL_TEXTURE_2D,
            result->texture,
            0);
        if (result->texture == 0 || result->framebuffer == 0 ||
            glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE ||
            glGetError() != GL_NO_ERROR) {
            set_error(player_, "The negotiated Linux DMA-BUF is not a renderable GLES2 framebuffer.");
            return {};
        }
        result->width = width;
        result->height = height;
        result->stride = stride;
        result->offset = offset;
        result->modifier = modifier;
        return result;
    }

    bool prepare_for_reuse(const std::shared_ptr<LinuxSurface>& surface) {
        int release_fence = -1;
        {
            std::lock_guard lock(surface->release_mutex);
            if (surface->retired) return false;
            release_fence = std::exchange(surface->release_fence_fd, -1);
        }
        if (release_fence < 0) return true;
        if (context_->wait_for_release_fence(release_fence)) return true;
        std::lock_guard lock(surface->release_mutex);
        surface->retired = true;
        return false;
    }

    bool bind_writable_surface() {
        current_surface_.reset();
        const auto target = copy_output_target(player_);
        if (target.type != KMEDIAVLC_OUTPUT_LINUX_DMABUF || target.generation == 0 ||
            context_ == nullptr || target.render_node != context_->render_node) {
            return false;
        }
        for (auto& slot : surfaces_) {
            if (slot.use_count() != 1) continue;
            bool retired = false;
            {
                std::lock_guard lock(slot->release_mutex);
                retired = slot->retired;
            }
            if (retired || !prepare_for_reuse(slot)) {
                auto replacement = create_surface(target.width, target.height, selected_modifier_);
                if (replacement == nullptr) continue;
                slot = std::move(replacement);
            }
            if (slot->width != target.width || slot->height != target.height ||
                slot->modifier != selected_modifier_) {
                continue;
            }
            current_surface_ = slot;
            break;
        }
        if (current_surface_ == nullptr) return false;

        current_surface_->output_generation = target.generation;
        current_surface_->sdr_white_nits = target.sdr_white_nits;
        current_surface_->source_dynamic_range = source_dynamic_range_;
        current_surface_->release_fence_required = target.release_fences;
        glBindFramebuffer(GL_FRAMEBUFFER, current_surface_->framebuffer);
        glViewport(
            0,
            0,
            static_cast<GLsizei>(current_surface_->width),
            static_cast<GLsizei>(current_surface_->height));
        glClearColor(0.0F, 0.0F, 0.0F, 1.0F);
        glClear(GL_COLOR_BUFFER_BIT);
        return glGetError() == GL_NO_ERROR;
    }

    bool make_current(bool enter) {
        if (!enter) return leave_current();
        state_mutex_.lock();
        if (context_ == nullptr) {
            state_mutex_.unlock();
            return false;
        }
        context_->mutex.lock();
        previous_display_ = eglGetCurrentDisplay();
        previous_context_ = eglGetCurrentContext();
        previous_draw_ = eglGetCurrentSurface(EGL_DRAW);
        previous_read_ = eglGetCurrentSurface(EGL_READ);
        if (eglMakeCurrent(
                context_->display,
                context_->pbuffer,
                context_->pbuffer,
                context_->context) != EGL_TRUE) {
            context_->mutex.unlock();
            state_mutex_.unlock();
            return false;
        }
        render_lock_held_ = true;
        if (surfaces_.empty() || bind_writable_surface()) return true;
        restore_previous_context();
        render_lock_held_ = false;
        context_->mutex.unlock();
        state_mutex_.unlock();
        return false;
    }

    bool leave_current() {
        if (!render_lock_held_ || context_ == nullptr) return false;
        bool completed = true;
        if (current_surface_ != nullptr) {
            const auto target = copy_output_target(player_);
            if (target.acquire_fences) {
                current_surface_->acquire_fence_fd = context_->create_acquire_fence();
                completed = current_surface_->acquire_fence_fd >= 0;
            } else {
                glFinish();
                completed = glGetError() == GL_NO_ERROR;
            }
            if (!completed) {
                std::lock_guard lock(current_surface_->release_mutex);
                current_surface_->retired = true;
            }
        }
        restore_previous_context();
        render_lock_held_ = false;
        context_->mutex.unlock();
        state_mutex_.unlock();
        if (!completed) {
            set_error(player_, "The Linux producer could not create the requested acquire fence.");
        }
        return completed;
    }

    void restore_previous_context() noexcept {
        if (context_ == nullptr) return;
        if (previous_display_ != EGL_NO_DISPLAY && previous_context_ != EGL_NO_CONTEXT) {
            eglMakeCurrent(previous_display_, previous_draw_, previous_read_, previous_context_);
        } else {
            eglMakeCurrent(context_->display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
        }
        previous_display_ = EGL_NO_DISPLAY;
        previous_context_ = EGL_NO_CONTEXT;
        previous_draw_ = EGL_NO_SURFACE;
        previous_read_ = EGL_NO_SURFACE;
    }

    void swap() {
        std::lock_guard lock(state_mutex_);
        auto surface = std::move(current_surface_);
        if (surface == nullptr || surface->output_generation == 0) return;
        {
            std::lock_guard release_lock(surface->release_mutex);
            if (surface->retired) return;
        }
        int dma_buf_fd = gbm_bo_get_fd(surface->bo);
        if (dma_buf_fd < 0 || fcntl(dma_buf_fd, F_SETFD, FD_CLOEXEC) == -1) {
            close_fd(dma_buf_fd);
            set_error(player_, "GBM could not export the completed Linux frame.");
            return;
        }
        auto frame = std::make_unique<kmediavlc_frame>();
        frame->platform_owner = surface;
        frame->platform_release = release_surface_callback;
        frame->info.output_generation = surface->output_generation;
        frame->info.pts_microseconds = current_position_microseconds(player_);
        frame->info.width = surface->width;
        frame->info.height = surface->height;
        frame->info.pixel_format = KMEDIAVLC_RGBA8_SRGB;
        frame->info.source_dynamic_range = surface->source_dynamic_range;
        frame->info.handle_type = KMEDIAVLC_DMABUF;
        frame->info.platform_handle = static_cast<std::uintptr_t>(dma_buf_fd);
        frame->info.acquire_fence = std::exchange(surface->acquire_fence_fd, -1);
        frame->info.stride = surface->stride;
        frame->info.fourcc = kOutputDrmFormat;
        frame->info.offset = surface->offset;
        frame->info.modifier = surface->modifier;
        frame->info.sdr_white_nits = surface->sdr_white_nits;
        frame->info.content_peak_nits = surface->sdr_white_nits;
        frame->info.premultiplied_alpha = true;
        publish_frame(player_, std::move(frame));
    }

    void release_resources() noexcept {
        std::lock_guard lock(state_mutex_);
        release_resources_locked();
    }

    void release_resources_locked() noexcept {
        current_surface_.reset();
        surfaces_.clear();
        context_.reset();
        selected_modifier_ = DRM_FORMAT_MOD_INVALID;
    }

    kmediavlc_player* player_ = nullptr;
    libvlc_media_player_t* media_player_ = nullptr;
    bool installed_ = false;
    std::recursive_mutex state_mutex_;
    std::shared_ptr<LinuxEglContext> context_;
    std::vector<std::shared_ptr<LinuxSurface>> surfaces_;
    std::shared_ptr<LinuxSurface> current_surface_;
    std::uint64_t selected_modifier_ = DRM_FORMAT_MOD_INVALID;
    bool render_lock_held_ = false;
    EGLDisplay previous_display_ = EGL_NO_DISPLAY;
    EGLContext previous_context_ = EGL_NO_CONTEXT;
    EGLSurface previous_draw_ = EGL_NO_SURFACE;
    EGLSurface previous_read_ = EGL_NO_SURFACE;
    kmediavlc_source_dynamic_range source_dynamic_range_ = KMEDIAVLC_SOURCE_DYNAMIC_RANGE_UNKNOWN;
};

} // namespace

std::unique_ptr<PlatformRenderer> create_platform_renderer(kmediavlc_player* player) {
    return std::make_unique<LinuxDmaBufRenderer>(player);
}

} // namespace kmediavlc
