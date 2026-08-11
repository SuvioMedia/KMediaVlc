// SPDX-License-Identifier: LGPL-2.1-or-later

#include "linux_dmabuf_inspector.hpp"

#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <GLES2/gl2ext.h>
#include <drm_fourcc.h>
#include <gbm.h>
#include <xf86drm.h>

#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

#include <algorithm>
#include <array>
#include <cstdint>
#include <limits>
#include <memory>
#include <string_view>
#include <vector>

#ifndef EGL_PLATFORM_GBM_KHR
#  define EGL_PLATFORM_GBM_KHR 0x31D7
#endif

namespace kmediavlc {
namespace {

constexpr std::uint32_t kOutputDrmFormat = DRM_FORMAT_ABGR8888;
constexpr std::uint32_t kMaximumDimension = 16'384;
constexpr EGLint kMaximumModifierCount = 4'096;

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

class LinuxDmaBufConsumer final {
public:
    ~LinuxDmaBufConsumer() {
        if (display_ != EGL_NO_DISPLAY) {
            if (eglGetCurrentDisplay() == display_ && eglGetCurrentContext() == context_) {
                eglMakeCurrent(display_, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
            }
            if (pbuffer_ != EGL_NO_SURFACE) eglDestroySurface(display_, pbuffer_);
            if (context_ != EGL_NO_CONTEXT) eglDestroyContext(display_, context_);
            eglTerminate(display_);
        }
        pbuffer_ = EGL_NO_SURFACE;
        context_ = EGL_NO_CONTEXT;
        display_ = EGL_NO_DISPLAY;
        if (gbm_ != nullptr) gbm_device_destroy(gbm_);
        gbm_ = nullptr;
        close_fd(render_node_fd_);
    }

    LinuxDmaBufConsumer(const LinuxDmaBufConsumer&) = delete;
    LinuxDmaBufConsumer& operator=(const LinuxDmaBufConsumer&) = delete;

    static std::unique_ptr<LinuxDmaBufConsumer> create(const std::string& render_node) {
        auto result = std::unique_ptr<LinuxDmaBufConsumer>(new LinuxDmaBufConsumer());
        if (!result->initialize(render_node)) return {};
        return result;
    }

    std::vector<std::uint64_t> modifiers() const {
        EGLint format_count = 0;
        if (query_dma_buf_formats_(display_, 0, nullptr, &format_count) != EGL_TRUE ||
            format_count <= 0 || format_count > kMaximumModifierCount) {
            return {};
        }
        std::vector<EGLint> formats(static_cast<std::size_t>(format_count));
        EGLint populated_formats = 0;
        if (query_dma_buf_formats_(
                display_, format_count, formats.data(), &populated_formats) != EGL_TRUE ||
            populated_formats <= 0 || populated_formats > format_count ||
            std::find(
                formats.begin(),
                formats.begin() + populated_formats,
                static_cast<EGLint>(kOutputDrmFormat)) == formats.begin() + populated_formats) {
            return {};
        }

        EGLint modifier_count = 0;
        if (query_dma_buf_modifiers_(
                display_,
                static_cast<EGLint>(kOutputDrmFormat),
                0,
                nullptr,
                nullptr,
                &modifier_count) != EGL_TRUE ||
            modifier_count <= 0 || modifier_count > kMaximumModifierCount) {
            return {};
        }
        std::vector<EGLuint64KHR> raw_modifiers(static_cast<std::size_t>(modifier_count));
        std::vector<EGLBoolean> external_only(static_cast<std::size_t>(modifier_count));
        EGLint populated_modifiers = 0;
        if (query_dma_buf_modifiers_(
                display_,
                static_cast<EGLint>(kOutputDrmFormat),
                modifier_count,
                raw_modifiers.data(),
                external_only.data(),
                &populated_modifiers) != EGL_TRUE ||
            populated_modifiers <= 0 || populated_modifiers > modifier_count) {
            return {};
        }

        std::vector<std::uint64_t> result;
        for (EGLint index = 0; index < populated_modifiers; ++index) {
            const auto position = static_cast<std::size_t>(index);
            const auto modifier = static_cast<std::uint64_t>(raw_modifiers[position]);
            if (external_only[position] == EGL_FALSE && modifier != DRM_FORMAT_MOD_INVALID) {
                result.push_back(modifier);
            }
        }
        std::sort(result.begin(), result.end());
        result.erase(std::unique(result.begin(), result.end()), result.end());
        return result;
    }

    bool inspect(
        int dma_buf_fd,
        int acquire_fence_fd,
        std::uint32_t width,
        std::uint32_t height,
        std::uint32_t stride,
        std::uint32_t fourcc,
        std::uint32_t offset,
        std::uint64_t modifier,
        LinuxDmaBufInspection& output) {
        output = {};
        if (dma_buf_fd < 0 || acquire_fence_fd < 0 || width == 0 || height == 0 ||
            width > kMaximumDimension || height > kMaximumDimension || stride == 0 ||
            width > static_cast<std::uint32_t>(std::numeric_limits<EGLint>::max()) ||
            height > static_cast<std::uint32_t>(std::numeric_limits<EGLint>::max()) ||
            stride > static_cast<std::uint32_t>(std::numeric_limits<EGLint>::max()) ||
            offset > static_cast<std::uint32_t>(std::numeric_limits<EGLint>::max()) ||
            fourcc != kOutputDrmFormat || modifier == DRM_FORMAT_MOD_INVALID) {
            close_fd(acquire_fence_fd);
            return false;
        }

        const EGLint acquire_attributes[]{
            EGL_SYNC_NATIVE_FENCE_FD_ANDROID,
            acquire_fence_fd,
            EGL_NONE,
        };
        const EGLSyncKHR acquire_sync = create_sync_(
            display_, EGL_SYNC_NATIVE_FENCE_ANDROID, acquire_attributes);
        // EGL_ANDROID_native_fence_sync consumes the descriptor on both the
        // success and error paths once it is passed to eglCreateSyncKHR.
        acquire_fence_fd = -1;
        if (acquire_sync == EGL_NO_SYNC_KHR) return false;
        const bool waited = wait_sync_(display_, acquire_sync, 0) == EGL_TRUE;
        destroy_sync_(display_, acquire_sync);
        if (!waited) return false;

        const EGLint image_attributes[]{
            EGL_WIDTH,
            static_cast<EGLint>(width),
            EGL_HEIGHT,
            static_cast<EGLint>(height),
            EGL_LINUX_DRM_FOURCC_EXT,
            static_cast<EGLint>(fourcc),
            EGL_DMA_BUF_PLANE0_FD_EXT,
            dma_buf_fd,
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
        EGLImageKHR image = create_image_(
            display_, EGL_NO_CONTEXT, EGL_LINUX_DMA_BUF_EXT, nullptr, image_attributes);
        if (image == EGL_NO_IMAGE_KHR) return false;

        GLuint texture = 0;
        GLuint framebuffer = 0;
        while (glGetError() != GL_NO_ERROR) {}
        glGenTextures(1, &texture);
        glBindTexture(GL_TEXTURE_2D, texture);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
        image_target_texture_(GL_TEXTURE_2D, image);
        glGenFramebuffers(1, &framebuffer);
        glBindFramebuffer(GL_FRAMEBUFFER, framebuffer);
        glFramebufferTexture2D(
            GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, texture, 0);
        const bool framebuffer_ready = texture != 0 && framebuffer != 0 &&
            glCheckFramebufferStatus(GL_FRAMEBUFFER) == GL_FRAMEBUFFER_COMPLETE &&
            glGetError() == GL_NO_ERROR;
        if (framebuffer_ready) {
            glReadPixels(
                static_cast<GLint>(width / 2U),
                static_cast<GLint>(height / 2U),
                1,
                1,
                GL_RGBA,
                GL_UNSIGNED_BYTE,
                output.rgba.data());
        }
        const bool read = framebuffer_ready && glGetError() == GL_NO_ERROR;
        glBindFramebuffer(GL_FRAMEBUFFER, 0);
        if (framebuffer != 0) glDeleteFramebuffers(1, &framebuffer);
        if (texture != 0) glDeleteTextures(1, &texture);
        destroy_image_(display_, image);
        if (!read || glGetError() != GL_NO_ERROR) return false;

        const EGLint release_attributes[]{
            EGL_SYNC_NATIVE_FENCE_FD_ANDROID,
            EGL_NO_NATIVE_FENCE_FD_ANDROID,
            EGL_NONE,
        };
        const EGLSyncKHR release_sync = create_sync_(
            display_, EGL_SYNC_NATIVE_FENCE_ANDROID, release_attributes);
        if (release_sync == EGL_NO_SYNC_KHR) return false;
        glFlush();
        int release_fence = duplicate_native_fence_(display_, release_sync);
        destroy_sync_(display_, release_sync);
        if (release_fence < 0 || fcntl(release_fence, F_SETFD, FD_CLOEXEC) == -1) {
            close_fd(release_fence);
            return false;
        }
        output.release_fence_fd = release_fence;
        return true;
    }

private:
    LinuxDmaBufConsumer() = default;

    bool initialize(const std::string& render_node) {
        if (render_node.empty() || render_node.front() != '/' || render_node.size() >= 4'096U) {
            return false;
        }
        int open_flags = O_RDWR | O_CLOEXEC;
#if defined(O_NOFOLLOW)
        open_flags |= O_NOFOLLOW;
#endif
        render_node_fd_ = open(render_node.c_str(), open_flags);
        if (render_node_fd_ < 0) return false;
        struct stat status {};
        if (fstat(render_node_fd_, &status) != 0 || !S_ISCHR(status.st_mode) ||
            drmGetNodeTypeFromFd(render_node_fd_) != DRM_NODE_RENDER) {
            return false;
        }
        gbm_ = gbm_create_device(render_node_fd_);
        if (gbm_ == nullptr) return false;

        const char* client_extensions = eglQueryString(EGL_NO_DISPLAY, EGL_EXTENSIONS);
        if (!has_extension(client_extensions, "EGL_KHR_platform_gbm") &&
            !has_extension(client_extensions, "EGL_MESA_platform_gbm")) {
            return false;
        }
        const auto get_platform_display =
            egl_function<PFNEGLGETPLATFORMDISPLAYEXTPROC>("eglGetPlatformDisplayEXT");
        if (get_platform_display == nullptr) return false;
        display_ = get_platform_display(EGL_PLATFORM_GBM_KHR, gbm_, nullptr);
        EGLint major = 0;
        EGLint minor = 0;
        if (display_ == EGL_NO_DISPLAY || eglInitialize(display_, &major, &minor) != EGL_TRUE ||
            eglBindAPI(EGL_OPENGL_ES_API) != EGL_TRUE) {
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
        if (eglChooseConfig(
                display_, config_attributes, &config, 1, &config_count) != EGL_TRUE ||
            config_count != 1 || config == nullptr) {
            return false;
        }
        const EGLint context_attributes[]{EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE};
        const EGLint pbuffer_attributes[]{EGL_WIDTH, 1, EGL_HEIGHT, 1, EGL_NONE};
        context_ = eglCreateContext(display_, config, EGL_NO_CONTEXT, context_attributes);
        pbuffer_ = eglCreatePbufferSurface(display_, config, pbuffer_attributes);
        if (context_ == EGL_NO_CONTEXT || pbuffer_ == EGL_NO_SURFACE ||
            eglMakeCurrent(display_, pbuffer_, pbuffer_, context_) != EGL_TRUE) {
            return false;
        }

        const char* display_extensions = eglQueryString(display_, EGL_EXTENSIONS);
        const char* gl_extensions = reinterpret_cast<const char*>(glGetString(GL_EXTENSIONS));
        if (!has_extension(display_extensions, "EGL_KHR_image_base") ||
            !has_extension(display_extensions, "EGL_EXT_image_dma_buf_import") ||
            !has_extension(display_extensions, "EGL_EXT_image_dma_buf_import_modifiers") ||
            !has_extension(display_extensions, "EGL_ANDROID_native_fence_sync") ||
            !has_extension(display_extensions, "EGL_KHR_wait_sync") ||
            !has_extension(gl_extensions, "GL_OES_EGL_image")) {
            return false;
        }

        create_image_ = egl_function<PFNEGLCREATEIMAGEKHRPROC>("eglCreateImageKHR");
        destroy_image_ = egl_function<PFNEGLDESTROYIMAGEKHRPROC>("eglDestroyImageKHR");
        query_dma_buf_formats_ =
            egl_function<PFNEGLQUERYDMABUFFORMATSEXTPROC>("eglQueryDmaBufFormatsEXT");
        query_dma_buf_modifiers_ =
            egl_function<PFNEGLQUERYDMABUFMODIFIERSEXTPROC>("eglQueryDmaBufModifiersEXT");
        create_sync_ = egl_function<PFNEGLCREATESYNCKHRPROC>("eglCreateSyncKHR");
        destroy_sync_ = egl_function<PFNEGLDESTROYSYNCKHRPROC>("eglDestroySyncKHR");
        wait_sync_ = egl_function<PFNEGLWAITSYNCKHRPROC>("eglWaitSyncKHR");
        duplicate_native_fence_ = egl_function<PFNEGLDUPNATIVEFENCEFDANDROIDPROC>(
            "eglDupNativeFenceFDANDROID");
        image_target_texture_ = egl_function<PFNGLEGLIMAGETARGETTEXTURE2DOESPROC>(
            "glEGLImageTargetTexture2DOES");
        return create_image_ != nullptr && destroy_image_ != nullptr &&
            query_dma_buf_formats_ != nullptr && query_dma_buf_modifiers_ != nullptr &&
            create_sync_ != nullptr && destroy_sync_ != nullptr && wait_sync_ != nullptr &&
            duplicate_native_fence_ != nullptr && image_target_texture_ != nullptr;
    }

    int render_node_fd_ = -1;
    gbm_device* gbm_ = nullptr;
    EGLDisplay display_ = EGL_NO_DISPLAY;
    EGLContext context_ = EGL_NO_CONTEXT;
    EGLSurface pbuffer_ = EGL_NO_SURFACE;
    PFNEGLCREATEIMAGEKHRPROC create_image_ = nullptr;
    PFNEGLDESTROYIMAGEKHRPROC destroy_image_ = nullptr;
    PFNEGLQUERYDMABUFFORMATSEXTPROC query_dma_buf_formats_ = nullptr;
    PFNEGLQUERYDMABUFMODIFIERSEXTPROC query_dma_buf_modifiers_ = nullptr;
    PFNEGLCREATESYNCKHRPROC create_sync_ = nullptr;
    PFNEGLDESTROYSYNCKHRPROC destroy_sync_ = nullptr;
    PFNEGLWAITSYNCKHRPROC wait_sync_ = nullptr;
    PFNEGLDUPNATIVEFENCEFDANDROIDPROC duplicate_native_fence_ = nullptr;
    PFNGLEGLIMAGETARGETTEXTURE2DOESPROC image_target_texture_ = nullptr;
};

} // namespace

std::vector<std::uint64_t> linux_dmabuf_consumer_modifiers(
    const std::string& render_node) {
    auto consumer = LinuxDmaBufConsumer::create(render_node);
    return consumer == nullptr ? std::vector<std::uint64_t>{} : consumer->modifiers();
}

bool inspect_linux_dmabuf_frame(
    const std::string& render_node,
    int dma_buf_fd,
    int acquire_fence_fd,
    std::uint32_t width,
    std::uint32_t height,
    std::uint32_t stride,
    std::uint32_t fourcc,
    std::uint32_t offset,
    std::uint64_t modifier,
    LinuxDmaBufInspection& output) {
    auto consumer = LinuxDmaBufConsumer::create(render_node);
    if (consumer == nullptr) {
        close_fd(acquire_fence_fd);
        return false;
    }
    return consumer->inspect(
        dma_buf_fd,
        acquire_fence_fd,
        width,
        height,
        stride,
        fourcc,
        offset,
        modifier,
        output);
}

} // namespace kmediavlc
