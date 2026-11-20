// SPDX-License-Identifier: MIT OR LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.android;

import java.nio.BufferUnderflowException;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Objects;

/**
 * One audio or subtitle track of the current media. {@link #getId()} is libVLC's string id, which
 * {@link VlcAndroidPlayer#selectTrack} accepts. Texts libVLC does not know are empty. The codec is
 * VLC's fourcc; channels and sample rate are zero for subtitles or when unknown.
 */
public final class VlcAndroidTrack {
    private static final int MAX_TRACKS = 256;

    private final VlcAndroidTrackType type;
    private final String id;
    private final String name;
    private final String language;
    private final String description;
    private final int codecFourcc;
    private final int audioChannels;
    private final int audioSampleRate;
    private final int bitrate;
    private final boolean selected;
    private final boolean stableId;

    VlcAndroidTrack(
            VlcAndroidTrackType type,
            String id,
            String name,
            String language,
            String description,
            int codecFourcc,
            int audioChannels,
            int audioSampleRate,
            int bitrate,
            boolean selected,
            boolean stableId) {
        this.type = Objects.requireNonNull(type, "type");
        if (id == null || id.isEmpty()) throw new IllegalArgumentException("id must not be empty.");
        this.id = id;
        this.name = name == null ? "" : name;
        this.language = language == null ? "" : language;
        this.description = description == null ? "" : description;
        this.codecFourcc = codecFourcc;
        this.audioChannels = Math.max(0, audioChannels);
        this.audioSampleRate = Math.max(0, audioSampleRate);
        this.bitrate = Math.max(0, bitrate);
        this.selected = selected;
        this.stableId = stableId;
    }

    public VlcAndroidTrackType getType() { return type; }
    public String getId() { return id; }
    public String getName() { return name; }
    public String getLanguage() { return language; }
    public String getDescription() { return description; }
    public int getCodecFourcc() { return codecFourcc; }
    public int getAudioChannels() { return audioChannels; }
    public int getAudioSampleRate() { return audioSampleRate; }
    public int getBitrate() { return bitrate; }
    public boolean isSelected() { return selected; }
    public boolean hasStableId() { return stableId; }

    /** The fourcc as its four characters, for example {@code "mp4a"} or {@code "subt"}. */
    public String getCodecName() {
        if (codecFourcc == 0) return "";
        char[] characters = new char[4];
        for (int index = 0; index < 4; index++) {
            int value = (codecFourcc >>> (8 * index)) & 0xFF;
            characters[index] = value >= 0x20 && value < 0x7F ? (char) value : '?';
        }
        return new String(characters).trim();
    }

    /** Decodes the bridge's big-endian track list; see the JNI tracks entry point. */
    static List<VlcAndroidTrack> decode(VlcAndroidTrackType type, byte[] encoded) {
        if (encoded == null) {
            throw new VlcAndroidException(
                    VlcAndroidException.Reason.NATIVE_CALL_FAILED,
                    "The Android VLC bridge returned no track list.");
        }
        try {
            ByteBuffer buffer = ByteBuffer.wrap(encoded);
            int count = buffer.getInt();
            if (count < 0 || count > MAX_TRACKS) throw new IllegalArgumentException("track count");
            List<VlcAndroidTrack> tracks = new ArrayList<>(count);
            for (int index = 0; index < count; index++) {
                int codec = buffer.getInt();
                int channels = buffer.getInt();
                int sampleRate = buffer.getInt();
                int bitrate = buffer.getInt();
                boolean selected = buffer.get() != 0;
                boolean stableId = buffer.get() != 0;
                String id = text(buffer);
                tracks.add(new VlcAndroidTrack(
                        type, id, text(buffer), text(buffer), text(buffer),
                        codec, channels, sampleRate, bitrate, selected, stableId));
            }
            if (buffer.hasRemaining()) throw new IllegalArgumentException("trailing bytes");
            return Collections.unmodifiableList(tracks);
        } catch (BufferUnderflowException | IllegalArgumentException failure) {
            throw new VlcAndroidException(
                    VlcAndroidException.Reason.NATIVE_CALL_FAILED,
                    "The Android VLC bridge returned a malformed track list.");
        }
    }

    private static String text(ByteBuffer buffer) {
        int length = buffer.getInt();
        if (length == -1) return "";
        if (length < 0 || length > buffer.remaining()) throw new IllegalArgumentException("text length");
        byte[] bytes = new byte[length];
        buffer.get(bytes);
        return new String(bytes, StandardCharsets.UTF_8).replace("\0", "");
    }

    @Override
    public String toString() {
        return "VlcAndroidTrack{" + type + ", " + id + ", " + name + ", " + language
                + (selected ? ", selected" : "") + "}";
    }
}
