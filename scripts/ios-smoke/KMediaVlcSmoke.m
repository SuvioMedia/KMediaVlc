// SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

#import <Foundation/Foundation.h>
#import <UIKit/UIKit.h>

#import <KMediaVlc/kmediavlc_client.h>

#include <stdlib.h>

static kmediavlc_player *g_player = NULL;
static BOOL g_finished = NO;

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

static void drain_latest_frame(void) {
    if (g_finished || g_player == NULL) return;
    kmediavlc_frame_info info = {0};
    info.struct_size = sizeof(info);
    info.bridge_abi_version = KMEDIAVLC_BRIDGE_ABI_VERSION;
    kmediavlc_frame *frame = kmediavlc_player_acquire_latest_frame(g_player, &info);
    if (frame == NULL) return;
    size_t byte_count = 0;
    const void *pixels = kmediavlc_frame_cpu_pixels(frame, &byte_count);
    BOOL valid =
        info.bridge_abi_version == KMEDIAVLC_BRIDGE_ABI_VERSION &&
        info.width == 64 && info.height == 36 &&
        info.pixel_format == KMEDIAVLC_RGBA8_SRGB &&
        info.handle_type == KMEDIAVLC_CPU_ADDRESS &&
        info.stride >= info.width * 4 &&
        byte_count >= (size_t)info.stride * info.height &&
        pixels != NULL;
    kmediavlc_frame_release(frame, -1);
    if (valid) {
        finish_smoke(
            [NSString stringWithFormat:@"PASS width=%u height=%u bytes=%zu",
                                       info.width, info.height, byte_count],
            0);
    } else {
        finish_smoke(@"FAIL invalid CPU_PULL frame", 2);
    }
}

static void frame_available(void *opaque, uint64_t serial, uint64_t generation) {
    (void)opaque;
    (void)serial;
    (void)generation;
    dispatch_async(dispatch_get_main_queue(), ^{ drain_latest_frame(); });
}

static void playback_state_changed(
    void *opaque,
    kmediavlc_playback_state state,
    uint64_t generation) {
    (void)opaque;
    (void)generation;
    if (state != KMEDIAVLC_STATE_ERROR) return;
    dispatch_async(dispatch_get_main_queue(), ^{
        if (g_player == NULL) return;
        const char *error = kmediavlc_player_last_error(g_player);
        NSString *detail = error == NULL
            ? @"libVLC entered the error state"
            : [NSString stringWithUTF8String:error];
        finish_smoke([@"FAIL " stringByAppendingString:detail], 3);
    });
}

static NSString *create_fixture(void) {
    UIGraphicsImageRendererFormat *format = [UIGraphicsImageRendererFormat defaultFormat];
    format.scale = 1.0;
    format.opaque = YES;
    UIGraphicsImageRenderer *renderer =
        [[UIGraphicsImageRenderer alloc] initWithSize:CGSizeMake(64, 36) format:format];
    UIImage *image = [renderer imageWithActions:^(UIGraphicsImageRendererContext *context) {
        (void)context;
        [[UIColor colorWithRed:1.0 green:0.125 blue:0.031 alpha:1.0] setFill];
        UIRectFill(CGRectMake(0, 0, 64, 36));
    }];
    NSData *data = UIImagePNGRepresentation(image);
    NSString *path = [NSTemporaryDirectory() stringByAppendingPathComponent:@"frame.png"];
    return data != nil && [data writeToFile:path atomically:YES] ? path : nil;
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
    NSString *fixture = create_fixture();
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
        const char *error = kmediavlc_player_last_error(g_player);
        NSString *detail = error == NULL
            ? @"open failed"
            : [NSString stringWithUTF8String:error];
        finish_smoke([@"FAIL " stringByAppendingString:detail], 6);
        return YES;
    }
    dispatch_after(
        dispatch_time(DISPATCH_TIME_NOW, (int64_t)(30 * NSEC_PER_SEC)),
        dispatch_get_main_queue(),
        ^{
            if (g_finished || g_player == NULL) return;
            const char *error = kmediavlc_player_last_error(g_player);
            NSString *detail = error == NULL
                ? @"frame timeout"
                : [NSString stringWithUTF8String:error];
            finish_smoke([@"FAIL " stringByAppendingString:detail], 7);
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
