# SPDX-License-Identifier: LGPL-2.1-or-later

cmake_minimum_required(VERSION 3.25)
project(kmediavlc_bridge VERSION 0.1.0 LANGUAGES CXX)

option(KMEDIAVLC_BUILD_TEST_FIXTURES "Build hermetic native ABI fixtures" OFF)

set(KMEDIAVLC_IOS OFF)
if(APPLE AND CMAKE_SYSTEM_NAME STREQUAL "iOS")
    set(KMEDIAVLC_IOS ON)
endif()

set(KMEDIAVLC_VLC_SOURCE_DIR "" CACHE PATH "Exact pinned VideoLAN VLC source checkout")
if(NOT KMEDIAVLC_VLC_SOURCE_DIR)
    message(FATAL_ERROR "KMEDIAVLC_VLC_SOURCE_DIR is required")
endif()

set(KMEDIAVLC_PINNED_VLC_REVISION "b5536cdea24b313ba9215eacfbd7fa3295d7f3ee")
if(NOT EXISTS "${KMEDIAVLC_VLC_SOURCE_DIR}/include/vlc/libvlc_media_player.h")
    message(FATAL_ERROR "KMEDIAVLC_VLC_SOURCE_DIR does not contain LibVLC headers")
endif()
execute_process(
    COMMAND git -C "${KMEDIAVLC_VLC_SOURCE_DIR}" rev-parse HEAD
    RESULT_VARIABLE KMEDIAVLC_GIT_RESULT
    OUTPUT_VARIABLE KMEDIAVLC_VLC_REVISION
    OUTPUT_STRIP_TRAILING_WHITESPACE
)
if(NOT KMEDIAVLC_GIT_RESULT EQUAL 0 OR
   NOT KMEDIAVLC_VLC_REVISION STREQUAL KMEDIAVLC_PINNED_VLC_REVISION)
    message(FATAL_ERROR
        "LibVLC headers must come from ${KMEDIAVLC_PINNED_VLC_REVISION}; got ${KMEDIAVLC_VLC_REVISION}")
endif()

set(KMEDIAVLC_SOURCES
    src/kmediavlc_bridge.cpp
)
if(NOT KMEDIAVLC_IOS)
    find_package(JNI REQUIRED)
    list(APPEND KMEDIAVLC_SOURCES src/kmediavlc_jni.cpp)
endif()

if(KMEDIAVLC_IOS)
    # iOS clients call the stable C ABI directly. Native executable code is
    # embedded and signed by Xcode, so neither JNI nor the macOS CGL renderer
    # belongs in this slice. CPU pull remains available in the shared bridge.
    list(APPEND KMEDIAVLC_SOURCES src/platform_renderer_stub.cpp)
elseif(WIN32)
    list(APPEND KMEDIAVLC_SOURCES src/windows_d3d11_renderer.cpp)
elseif(APPLE)
    list(APPEND KMEDIAVLC_SOURCES src/macos_iosurface_renderer.cpp)
elseif(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    list(APPEND KMEDIAVLC_SOURCES
        src/linux_dmabuf_inspector.cpp
        src/linux_dmabuf_renderer.cpp
    )
else()
    list(APPEND KMEDIAVLC_SOURCES src/platform_renderer_stub.cpp)
endif()

add_library(kmediavlc_bridge SHARED ${KMEDIAVLC_SOURCES})
target_compile_features(kmediavlc_bridge PRIVATE cxx_std_20)
target_compile_definitions(kmediavlc_bridge PRIVATE KMEDIAVLC_BUILDING_BRIDGE=1)
target_include_directories(kmediavlc_bridge PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/include"
    "${CMAKE_CURRENT_SOURCE_DIR}/src"
    "${KMEDIAVLC_VLC_SOURCE_DIR}/include"
)
if(NOT KMEDIAVLC_IOS)
    target_include_directories(kmediavlc_bridge PRIVATE ${JNI_INCLUDE_DIRS})
endif()
set_target_properties(kmediavlc_bridge PROPERTIES
    CXX_EXTENSIONS OFF
    OUTPUT_NAME "kmediavlc_bridge"
)

if(KMEDIAVLC_IOS)
    target_compile_options(kmediavlc_bridge PRIVATE -Wall -Wextra -Wpedantic -Wconversion)
    target_link_libraries(kmediavlc_bridge PRIVATE ${CMAKE_DL_LIBS})
elseif(MSVC)
    set_property(TARGET kmediavlc_bridge PROPERTY
        MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>DLL"
    )
    target_compile_options(kmediavlc_bridge PRIVATE /W4 /permissive- /EHsc /utf-8)
    target_compile_definitions(kmediavlc_bridge PRIVATE
        WIN32_LEAN_AND_MEAN
        NOMINMAX
        _CRT_SECURE_NO_WARNINGS
    )
    target_link_libraries(kmediavlc_bridge PRIVATE d3d11 d3dcompiler dxgi dxguid)
elseif(APPLE)
    find_library(KMEDIAVLC_CORE_FOUNDATION CoreFoundation REQUIRED)
    find_library(KMEDIAVLC_CORE_VIDEO CoreVideo REQUIRED)
    find_library(KMEDIAVLC_IO_SURFACE IOSurface REQUIRED)
    find_library(KMEDIAVLC_OPEN_GL OpenGL REQUIRED)
    target_compile_definitions(kmediavlc_bridge PRIVATE
        GL_SILENCE_DEPRECATION
        KMEDIAVLC_MACOS=1
    )
    target_compile_options(kmediavlc_bridge PRIVATE -Wall -Wextra -Wpedantic -Wconversion)
    target_link_libraries(kmediavlc_bridge PRIVATE
        ${KMEDIAVLC_CORE_FOUNDATION}
        ${KMEDIAVLC_CORE_VIDEO}
        ${KMEDIAVLC_IO_SURFACE}
        ${KMEDIAVLC_OPEN_GL}
        ${CMAKE_DL_LIBS}
    )
elseif(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    find_package(PkgConfig REQUIRED)
    pkg_check_modules(KMEDIAVLC_LINUX_GRAPHICS REQUIRED IMPORTED_TARGET
        egl
        glesv2
        gbm
        libdrm
    )
    target_compile_definitions(kmediavlc_bridge PRIVATE _FILE_OFFSET_BITS=64)
    target_compile_options(kmediavlc_bridge PRIVATE -Wall -Wextra -Wpedantic -Wconversion)
    target_link_options(kmediavlc_bridge PRIVATE
        -Wl,-z,relro
        -Wl,-z,now
        -Wl,-z,noexecstack
        -Wl,--build-id=sha1
    )
    target_link_libraries(kmediavlc_bridge PRIVATE
        PkgConfig::KMEDIAVLC_LINUX_GRAPHICS
        ${CMAKE_DL_LIBS}
    )
elseif(CMAKE_CXX_COMPILER_ID MATCHES "Clang|GNU")
    target_compile_options(kmediavlc_bridge PRIVATE -Wall -Wextra -Wpedantic -Wconversion)
    target_link_libraries(kmediavlc_bridge PRIVATE ${CMAKE_DL_LIBS})
endif()

install(TARGETS kmediavlc_bridge
    RUNTIME DESTINATION .
    LIBRARY DESTINATION .
)

if(KMEDIAVLC_BUILD_TEST_FIXTURES)
    add_library(kmediavlc_fake_libvlc SHARED tests/fake_libvlc.cpp)
    target_compile_features(kmediavlc_fake_libvlc PRIVATE cxx_std_20)
    target_include_directories(kmediavlc_fake_libvlc PRIVATE "${KMEDIAVLC_VLC_SOURCE_DIR}/include")
    set_target_properties(kmediavlc_fake_libvlc PROPERTIES
        CXX_EXTENSIONS OFF
        OUTPUT_NAME "kmediavlc_fake_libvlc"
    )
    if(CMAKE_CXX_COMPILER_ID MATCHES "Clang|GNU")
        target_compile_options(kmediavlc_fake_libvlc PRIVATE -Wall -Wextra -Wpedantic -Wconversion)
    endif()
endif()
