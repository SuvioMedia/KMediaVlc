// SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

#import <Foundation/Foundation.h>
#import <UIKit/UIKit.h>

#import <KMediaVlc/kmediavlc_client.h>

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static kmediavlc_player *g_player = NULL;
static BOOL g_finished = NO;

typedef NS_ENUM(NSInteger, KMediaVlcSmokePhase) {
    KMediaVlcSmokePhaseVideo,
    KMediaVlcSmokePhaseAudio,
};

static KMediaVlcSmokePhase g_phase = KMediaVlcSmokePhaseVideo;
static uint64_t g_video_generation = 0;
static uint64_t g_audio_generation = 0;
static uint64_t g_last_frame_serial = 0;
static uint64_t g_first_frame_hash = 0;
static NSUInteger g_video_frame_count = 0;
static BOOL g_distinct_video_frame = NO;
static BOOL g_seek_requested = NO;
static BOOL g_seek_observed = NO;
static int64_t g_last_video_position = 0;
static int64_t g_video_duration = 0;
static int64_t g_audio_progress = 0;
static int64_t g_audio_duration = 0;

static NSURL *result_url(void) {
    NSURL *documents = [[[NSFileManager defaultManager]
        URLsForDirectory:NSDocumentDirectory
               inDomains:NSUserDomainMask] firstObject];
    return [documents URLByAppendingPathComponent:@"kmediavlc-smoke-result.txt"];
}

static void finish_smoke(NSString *result, int status) {
    if (g_finished) return;
    g_finished = YES;
    NSError *error = nil;
    NSString *line = [result stringByAppendingString:@"\n"];
    const char *console_result = result.UTF8String;
    if (console_result != NULL) {
        fprintf(stdout, "KMEDIAVLC_SMOKE %s\n", console_result);
        fflush(stdout);
    }
    [line writeToURL:result_url()
          atomically:YES
            encoding:NSUTF8StringEncoding
               error:&error];
    if (g_player != NULL) {
        kmediavlc_player_destroy(g_player);
        g_player = NULL;
    }
    dispatch_after(
        dispatch_time(DISPATCH_TIME_NOW, (int64_t)(250 * NSEC_PER_MSEC)),
        dispatch_get_main_queue(),
        ^{ exit(error == nil ? status : 70); });
}

static NSString *native_error(NSString *fallback) {
    if (g_player == NULL) return fallback;
    const char *error = kmediavlc_player_last_error(g_player);
    return error == NULL ? fallback : [NSString stringWithUTF8String:error];
}

static uint64_t sampled_frame_hash(
    const uint8_t *pixels,
    uint32_t width,
    uint32_t height,
    uint32_t stride) {
    uint64_t hash = UINT64_C(1469598103934665603);
    for (uint32_t y = 0; y < height; y += 12) {
        const uint8_t *row = pixels + (size_t)y * stride;
        for (uint32_t x = 0; x < width; x += 12) {
            const uint8_t *pixel = row + (size_t)x * 4;
            for (size_t channel = 0; channel < 4; channel++) {
                hash ^= pixel[channel];
                hash *= UINT64_C(1099511628211);
            }
        }
    }
    return hash;
}

static void drain_latest_frame(void) {
    if (g_finished || g_player == NULL || g_phase != KMediaVlcSmokePhaseVideo) return;
    kmediavlc_frame_info info = {0};
    info.struct_size = sizeof(info);
    info.bridge_abi_version = KMEDIAVLC_BRIDGE_ABI_VERSION;
    kmediavlc_frame *frame = kmediavlc_player_acquire_latest_frame(g_player, &info);
    if (frame == NULL) return;
    size_t byte_count = 0;
    const void *pixels = kmediavlc_frame_cpu_pixels(frame, &byte_count);
    BOOL valid =
        info.bridge_abi_version == KMEDIAVLC_BRIDGE_ABI_VERSION &&
        info.output_generation == g_video_generation &&
        info.width == 320 && info.height == 180 &&
        info.pixel_format == KMEDIAVLC_RGBA8_SRGB &&
        info.handle_type == KMEDIAVLC_CPU_ADDRESS &&
        info.stride >= info.width * 4 &&
        byte_count >= (size_t)info.stride * info.height &&
        pixels != NULL;
    uint64_t content_hash = valid
        ? sampled_frame_hash(pixels, info.width, info.height, info.stride)
        : 0;
    kmediavlc_frame_release(frame, -1);
    if (!valid) {
        finish_smoke(
            [NSString stringWithFormat:
                @"FAIL invalid CPU_PULL frame abi=%u generation=%llu expected=%llu "
                 "width=%u height=%u format=%d handle=%d stride=%u bytes=%zu pixels=%d",
                info.bridge_abi_version,
                (unsigned long long)info.output_generation,
                (unsigned long long)g_video_generation,
                info.width,
                info.height,
                (int)info.pixel_format,
                (int)info.handle_type,
                info.stride,
                byte_count,
                pixels != NULL],
            2);
        return;
    }
    if (info.serial != g_last_frame_serial) {
        g_last_frame_serial = info.serial;
        g_video_frame_count++;
        if (g_first_frame_hash == 0) {
            g_first_frame_hash = content_hash;
        } else if (content_hash != g_first_frame_hash) {
            g_distinct_video_frame = YES;
        }
    }
}

static void frame_available(void *opaque, uint64_t serial, uint64_t generation) {
    (void)opaque;
    (void)serial;
    dispatch_async(dispatch_get_main_queue(), ^{
        if (!g_finished && g_phase == KMediaVlcSmokePhaseVideo &&
            generation == g_video_generation) {
            drain_latest_frame();
        }
    });
}

static void playback_state_changed(
    void *opaque,
    kmediavlc_playback_state state,
    uint64_t generation) {
    (void)opaque;
    dispatch_async(dispatch_get_main_queue(), ^{
        if (g_finished || state != KMEDIAVLC_STATE_ERROR) return;
        uint64_t expected_generation = g_phase == KMediaVlcSmokePhaseVideo
            ? g_video_generation
            : g_audio_generation;
        if (generation == expected_generation) {
            finish_smoke(
                [@"FAIL " stringByAppendingString:native_error(@"libVLC entered the error state")],
                3);
        }
    });
}

static void write_u16(uint8_t *bytes, uint16_t value) {
    bytes[0] = (uint8_t)value;
    bytes[1] = (uint8_t)(value >> 8);
}

static void write_u32(uint8_t *bytes, uint32_t value) {
    bytes[0] = (uint8_t)value;
    bytes[1] = (uint8_t)(value >> 8);
    bytes[2] = (uint8_t)(value >> 16);
    bytes[3] = (uint8_t)(value >> 24);
}

static NSString *create_audio_fixture(void) {
    const uint32_t sample_rate = 8000;
    const uint32_t frame_count = sample_rate * 2;
    const uint32_t data_size = frame_count * 2;
    NSMutableData *data = [NSMutableData dataWithLength:44 + data_size];
    if (data == nil) return nil;
    uint8_t *bytes = data.mutableBytes;
    memcpy(bytes, "RIFF", 4);
    write_u32(bytes + 4, 36 + data_size);
    memcpy(bytes + 8, "WAVEfmt ", 8);
    write_u32(bytes + 16, 16);
    write_u16(bytes + 20, 1);
    write_u16(bytes + 22, 1);
    write_u32(bytes + 24, sample_rate);
    write_u32(bytes + 28, sample_rate * 2);
    write_u16(bytes + 32, 2);
    write_u16(bytes + 34, 16);
    memcpy(bytes + 36, "data", 4);
    write_u32(bytes + 40, data_size);
    for (uint32_t frame = 0; frame < frame_count; frame++) {
        int16_t sample = ((frame / 9) % 2 == 0) ? 1024 : -1024;
        write_u16(bytes + 44 + frame * 2, (uint16_t)sample);
    }
    NSString *path = [NSTemporaryDirectory() stringByAppendingPathComponent:@"audio.wav"];
    return [data writeToFile:path atomically:YES] ? path : nil;
}

static BOOL read_snapshot(kmediavlc_player_snapshot *snapshot) {
    memset(snapshot, 0, sizeof(*snapshot));
    snapshot->struct_size = sizeof(*snapshot);
    snapshot->bridge_abi_version = KMEDIAVLC_BRIDGE_ABI_VERSION;
    if (!kmediavlc_player_get_snapshot(g_player, snapshot)) {
        finish_smoke(
            [@"FAIL " stringByAppendingString:native_error(@"snapshot failed")],
            8);
        return NO;
    }
    if (snapshot->bridge_abi_version != KMEDIAVLC_BRIDGE_ABI_VERSION ||
        snapshot->buffered_permille > 1000) {
        finish_smoke(@"FAIL invalid playback snapshot", 9);
        return NO;
    }
    return YES;
}

static void poll_playback(void);

static void schedule_poll(void) {
    dispatch_after(
        dispatch_time(DISPATCH_TIME_NOW, (int64_t)(50 * NSEC_PER_MSEC)),
        dispatch_get_main_queue(),
        ^{ poll_playback(); });
}

static void begin_audio_playback(void) {
    NSString *fixture = create_audio_fixture();
    if (fixture == nil || !kmediavlc_player_set_volume(g_player, 0.0f) ||
        !kmediavlc_player_open(g_player, fixture.UTF8String, NULL, 0, true)) {
        finish_smoke(
            [@"FAIL " stringByAppendingString:native_error(@"audio fixture open failed")],
            10);
        return;
    }
    kmediavlc_player_snapshot snapshot;
    if (!read_snapshot(&snapshot)) return;
    g_phase = KMediaVlcSmokePhaseAudio;
    g_audio_generation = snapshot.media_generation;
    if (g_audio_generation == 0 || g_audio_generation == g_video_generation) {
        finish_smoke(@"FAIL audio media generation did not advance", 11);
        return;
    }
    schedule_poll();
}

static void poll_video(kmediavlc_player_snapshot snapshot) {
    if (snapshot.media_generation != g_video_generation) {
        finish_smoke(@"FAIL video media generation changed", 12);
        return;
    }
    if (snapshot.state == KMEDIAVLC_STATE_ERROR) {
        finish_smoke(
            [@"FAIL " stringByAppendingString:native_error(@"video playback failed")],
            13);
        return;
    }
    if (snapshot.duration_microseconds > 0) {
        g_video_duration = snapshot.duration_microseconds;
        if (g_video_duration < 11500000 || g_video_duration > 12500000) {
            finish_smoke(@"FAIL video duration is outside the fixture contract", 14);
            return;
        }
    }
    if (snapshot.video_width != 0 &&
        (snapshot.video_width != 320 || snapshot.video_height != 180)) {
        finish_smoke(@"FAIL video dimensions are outside the fixture contract", 15);
        return;
    }
    if (snapshot.state != KMEDIAVLC_STATE_ENDED &&
        snapshot.position_microseconds < g_last_video_position) {
        finish_smoke(
            [NSString stringWithFormat:
                @"FAIL video position moved backwards previousUs=%lld currentUs=%lld "
                 "seekRequested=%d state=%d",
                (long long)g_last_video_position,
                (long long)snapshot.position_microseconds,
                g_seek_requested,
                (int)snapshot.state],
            16);
        return;
    }
    if (snapshot.state != KMEDIAVLC_STATE_ENDED) {
        g_last_video_position = snapshot.position_microseconds;
    }
    if (!g_seek_requested && g_video_frame_count >= 3 && g_distinct_video_frame &&
        snapshot.position_microseconds >= 250000) {
        if (!kmediavlc_player_seek(g_player, 7000000, false)) {
            finish_smoke(
                [@"FAIL " stringByAppendingString:native_error(@"video seek failed")],
                17);
            return;
        }
        g_seek_requested = YES;
    }
    if (g_seek_requested && snapshot.position_microseconds >= 6500000) {
        g_seek_observed = YES;
    }
    if (snapshot.state == KMEDIAVLC_STATE_ENDED) {
        if (g_video_duration == 0 || g_video_frame_count < 6 || !g_distinct_video_frame ||
            !g_seek_observed) {
            finish_smoke(@"FAIL incomplete timed video evidence", 18);
            return;
        }
        begin_audio_playback();
        return;
    }
    schedule_poll();
}

static void poll_audio(kmediavlc_player_snapshot snapshot) {
    if (snapshot.media_generation != g_audio_generation) {
        finish_smoke(@"FAIL audio media generation changed", 19);
        return;
    }
    if (snapshot.state == KMEDIAVLC_STATE_ERROR) {
        finish_smoke(
            [@"FAIL " stringByAppendingString:native_error(@"audio playback failed")],
            20);
        return;
    }
    if (snapshot.duration_microseconds > 0) {
        g_audio_duration = snapshot.duration_microseconds;
        if (g_audio_duration < 1500000 || g_audio_duration > 2500000) {
            finish_smoke(@"FAIL audio duration is outside the fixture contract", 21);
            return;
        }
    }
    if (snapshot.position_microseconds > g_audio_progress) {
        g_audio_progress = snapshot.position_microseconds;
    }
    if (snapshot.state == KMEDIAVLC_STATE_ENDED) {
        if (g_audio_duration == 0 || g_audio_progress < 500000) {
            finish_smoke(@"FAIL incomplete audio playback evidence", 22);
            return;
        }
        finish_smoke(
            [NSString stringWithFormat:
                @"PASS videoFrames=%lu videoDurationUs=%lld seekUs=%lld "
                 "audioDurationUs=%lld audioProgressUs=%lld",
                (unsigned long)g_video_frame_count,
                (long long)g_video_duration,
                (long long)g_last_video_position,
                (long long)g_audio_duration,
                (long long)g_audio_progress],
            0);
        return;
    }
    schedule_poll();
}

static void poll_playback(void) {
    if (g_finished || g_player == NULL) return;
    kmediavlc_player_snapshot snapshot;
    if (!read_snapshot(&snapshot)) return;
    if (g_phase == KMediaVlcSmokePhaseVideo) {
        poll_video(snapshot);
    } else {
        poll_audio(snapshot);
    }
}

@interface KMediaVlcSmokeDelegate : UIResponder <UIApplicationDelegate>
@property(nonatomic, strong) UIWindow *window;
@end

@implementation KMediaVlcSmokeDelegate

- (BOOL)application:(UIApplication *)application
    didFinishLaunchingWithOptions:(NSDictionary<UIApplicationLaunchOptionsKey, id> *)options {
    (void)application;
    (void)options;
    self.window = [[UIWindow alloc] initWithFrame:[UIScreen mainScreen].bounds];
    self.window.rootViewController = [[UIViewController alloc] init];
    self.window.rootViewController.view.backgroundColor = UIColor.blackColor;
    [self.window makeKeyAndVisible];

    NSString *frameworks = NSBundle.mainBundle.privateFrameworksPath;
    NSString *libvlc = [frameworks
        stringByAppendingPathComponent:@"KMediaVlcLibVlc.framework/KMediaVlcLibVlc"];
    NSString *fixture = [NSBundle.mainBundle pathForResource:@"kmediavlc-playback" ofType:@"mkv"];
    if (frameworks == nil || fixture == nil) {
        finish_smoke(@"FAIL application fixture setup", 4);
        return YES;
    }

    kmediavlc_player_config config = {0};
    config.struct_size = sizeof(config);
    config.bridge_abi_version = KMEDIAVLC_BRIDGE_ABI_VERSION;
    config.libvlc_path_utf8 = libvlc.UTF8String;
    config.plugin_directory_utf8 = frameworks.UTF8String;
    config.delivery_mode = KMEDIAVLC_CPU_PULL;
    config.request_hdr = false;
    config.sdr_white_nits = 203.0f;
    config.display_peak_nits = 203.0f;
    config.frame_available = frame_available;
    config.playback_state_changed = playback_state_changed;
    g_player = kmediavlc_player_create(&config);
    if (g_player == NULL) {
        finish_smoke(@"FAIL player creation", 5);
        return YES;
    }
    if (!kmediavlc_player_open(g_player, fixture.UTF8String, NULL, 0, true)) {
        finish_smoke([@"FAIL " stringByAppendingString:native_error(@"open failed")], 6);
        return YES;
    }
    kmediavlc_player_snapshot snapshot;
    if (!read_snapshot(&snapshot)) return YES;
    g_video_generation = snapshot.media_generation;
    if (g_video_generation == 0) {
        finish_smoke(@"FAIL video media generation is invalid", 23);
        return YES;
    }
    schedule_poll();
    dispatch_after(
        dispatch_time(DISPATCH_TIME_NOW, (int64_t)(35 * NSEC_PER_SEC)),
        dispatch_get_main_queue(),
        ^{
            if (g_finished || g_player == NULL) return;
            finish_smoke(
                [@"FAIL " stringByAppendingString:native_error(@"timed playback timeout")],
                7);
        });
    return YES;
}

@end

int main(int argc, char *argv[]) {
    @autoreleasepool {
        return UIApplicationMain(
            argc,
            argv,
            nil,
            NSStringFromClass(KMediaVlcSmokeDelegate.class));
    }
}
