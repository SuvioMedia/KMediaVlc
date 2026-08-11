// SPDX-License-Identifier: LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.android;

import android.app.Activity;
import android.graphics.Color;
import android.graphics.PixelFormat;
import android.graphics.Rect;
import android.os.Bundle;
import android.os.Looper;
import android.view.Gravity;
import android.view.Surface;
import android.view.SurfaceHolder;
import android.view.SurfaceView;
import android.view.WindowManager;
import android.widget.FrameLayout;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/** Debug-only host for the real SurfaceView topology consumed by Android libVLC. */
public final class VlcAndroidSurfaceTestActivity extends Activity {
    static final int SURFACE_WIDTH = 320;
    static final int SURFACE_HEIGHT = 180;

    private FrameLayout root;
    private volatile SurfaceView videoView;
    private volatile SurfaceView subtitleView;
    private volatile CountDownLatch surfacesReady = new CountDownLatch(2);

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        root = new FrameLayout(this);
        root.setBackgroundColor(Color.BLACK);
        setContentView(root);
        replaceSurfaces();
    }

    void replaceSurfaces() {
        if (Looper.myLooper() != Looper.getMainLooper()) {
            throw new IllegalStateException("SurfaceView replacement must run on the UI thread.");
        }
        CountDownLatch ready = new CountDownLatch(2);
        surfacesReady = ready;
        root.removeAllViews();

        SurfaceView nextVideo = createSurfaceView(false, ready);
        SurfaceView nextSubtitles = createSurfaceView(true, ready);
        FrameLayout.LayoutParams layout =
                new FrameLayout.LayoutParams(SURFACE_WIDTH, SURFACE_HEIGHT, Gravity.CENTER);
        root.addView(nextVideo, layout);
        root.addView(nextSubtitles, layout);
        videoView = nextVideo;
        subtitleView = nextSubtitles;
    }

    boolean awaitSurfaces(long timeoutMillis) throws InterruptedException {
        return surfacesReady.await(timeoutMillis, TimeUnit.MILLISECONDS)
                && getVideoSurface().isValid()
                && getSubtitleSurface().isValid();
    }

    Surface getVideoSurface() {
        return videoView.getHolder().getSurface();
    }

    Surface getSubtitleSurface() {
        return subtitleView.getHolder().getSurface();
    }

    Rect getSurfaceRectOnScreen(long timeoutMillis) throws InterruptedException {
        AtomicReference<Rect> result = new AtomicReference<>();
        CountDownLatch complete = new CountDownLatch(1);
        runOnUiThread(
                () -> {
                    int[] location = new int[2];
                    videoView.getLocationOnScreen(location);
                    result.set(
                            new Rect(
                                    location[0],
                                    location[1],
                                    location[0] + videoView.getWidth(),
                                    location[1] + videoView.getHeight()));
                    complete.countDown();
                });
        if (!complete.await(timeoutMillis, TimeUnit.MILLISECONDS)) {
            throw new IllegalStateException("Timed out reading the SurfaceView screen bounds.");
        }
        return result.get();
    }

    private SurfaceView createSurfaceView(boolean subtitles, CountDownLatch ready) {
        SurfaceView view = new SurfaceView(this);
        SurfaceHolder holder = view.getHolder();
        holder.setFixedSize(SURFACE_WIDTH, SURFACE_HEIGHT);
        holder.setFormat(subtitles ? PixelFormat.TRANSLUCENT : PixelFormat.OPAQUE);
        if (subtitles) view.setZOrderMediaOverlay(true);
        holder.addCallback(
                new SurfaceHolder.Callback() {
                    @Override
                    public void surfaceCreated(SurfaceHolder ignored) {
                        ready.countDown();
                    }

                    @Override
                    public void surfaceChanged(
                            SurfaceHolder ignored, int format, int width, int height) {}

                    @Override
                    public void surfaceDestroyed(SurfaceHolder ignored) {}
                });
        return view;
    }
}
