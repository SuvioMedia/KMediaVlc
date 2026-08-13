# SPDX-License-Identifier: LGPL-2.1-or-later

cmake_minimum_required(VERSION 3.22)
project(kmediavlc_android LANGUAGES C CXX)

if(NOT ANDROID)
    message(FATAL_ERROR "The Android bridge must be cross-compiled with the Android NDK toolchain.")
endif()
if(NOT DEFINED KMEDIAVLC_VLC_SOURCE_DIR)
    message(FATAL_ERROR "KMEDIAVLC_VLC_SOURCE_DIR must identify the exact pinned VLC checkout.")
endif()
if(NOT EXISTS "${KMEDIAVLC_VLC_SOURCE_DIR}/include/vlc/libvlc_media_player.h")
    message(FATAL_ERROR "The pinned libVLC 4 headers are missing.")
endif()
if(NOT ANDROID_STL STREQUAL "c++_static")
    message(FATAL_ERROR "The closed Android payload requires ANDROID_STL=c++_static.")
endif()

option(KMEDIAVLC_ANDROID_FAKE_LIBVLC "Build the hermetic pinned-ABI libVLC fixture" OFF)
set(KMEDIAVLC_ANDROID_LIBVLC "" CACHE FILEPATH "Source-built libvlc.so for the selected ABI")

if(KMEDIAVLC_ANDROID_FAKE_LIBVLC)
    add_library(kmediavlc_android_fake_vlc SHARED tests/fake_libvlc.cpp)
    set_target_properties(kmediavlc_android_fake_vlc PROPERTIES OUTPUT_NAME vlc)
    target_include_directories(
        kmediavlc_android_fake_vlc
        PRIVATE "${KMEDIAVLC_VLC_SOURCE_DIR}/include"
    )
    target_compile_features(kmediavlc_android_fake_vlc PRIVATE cxx_std_20)
    target_compile_options(
        kmediavlc_android_fake_vlc
        PRIVATE -Wall -Wextra -Werror -Wpedantic -fvisibility=hidden
    )
    target_link_options(
        kmediavlc_android_fake_vlc
        PRIVATE -Wl,--no-undefined -Wl,-z,max-page-size=16384 -Wl,--gc-sections
    )
    set(kmediavlc_libvlc_target kmediavlc_android_fake_vlc)
else()
    if(NOT KMEDIAVLC_ANDROID_LIBVLC OR NOT EXISTS "${KMEDIAVLC_ANDROID_LIBVLC}")
        message(FATAL_ERROR "KMEDIAVLC_ANDROID_LIBVLC must identify source-built libvlc.so.")
    endif()
    add_library(kmediavlc_android_libvlc SHARED IMPORTED GLOBAL)
    set_target_properties(
        kmediavlc_android_libvlc
        PROPERTIES IMPORTED_LOCATION "${KMEDIAVLC_ANDROID_LIBVLC}"
    )
    set(kmediavlc_libvlc_target kmediavlc_android_libvlc)
endif()

add_library(kmediavlc_android SHARED kmediavlc_android_jni.cpp)
target_include_directories(
    kmediavlc_android
    PRIVATE "${KMEDIAVLC_VLC_SOURCE_DIR}/include"
)
target_compile_features(kmediavlc_android PRIVATE cxx_std_20)
target_compile_definitions(
    kmediavlc_android
    PRIVATE
        KMEDIAVLC_ANDROID_ABI="${ANDROID_ABI}"
        KMEDIAVLC_VLC_REVISION="e439692079a75cacb5f07310d1ec2dc20bfd1fe0"
)
target_compile_options(
    kmediavlc_android
    PRIVATE -Wall -Wextra -Werror -Wpedantic -fvisibility=hidden -fdata-sections -ffunction-sections
)
target_link_options(
    kmediavlc_android
    PRIVATE -Wl,--no-undefined -Wl,-z,max-page-size=16384 -Wl,--gc-sections
)
target_link_libraries(kmediavlc_android PRIVATE ${kmediavlc_libvlc_target} android log)
