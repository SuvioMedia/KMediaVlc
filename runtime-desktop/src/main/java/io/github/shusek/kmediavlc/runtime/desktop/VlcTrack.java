// SPDX-License-Identifier: MIT OR LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.desktop;

import java.util.Objects;

/**
 * One audio or subtitle track of the current media. {@code id} is libVLC's string id, which
 * {@link VlcDesktopPlayer#selectTrack} accepts. Texts libVLC does not know are empty. The codec is
 * VLC's fourcc; channels and sample rate are zero for subtitles or when unknown.
 */
public record VlcTrack(
        VlcTrackType type,
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

    public VlcTrack {
        Objects.requireNonNull(type, "type");
        Objects.requireNonNull(id, "id");
        if (id.isEmpty()) throw new IllegalArgumentException("id must not be empty.");
        name = Objects.requireNonNullElse(name, "");
        language = Objects.requireNonNullElse(language, "");
        description = Objects.requireNonNullElse(description, "");
        audioChannels = Math.max(0, audioChannels);
        audioSampleRate = Math.max(0, audioSampleRate);
        bitrate = Math.max(0, bitrate);
    }

    /** The fourcc as its four characters, for example {@code "mp4a"} or {@code "subt"}. */
    public String codecName() {
        if (codecFourcc == 0) return "";
        char[] characters = new char[4];
        for (int index = 0; index < 4; index++) {
            int value = (codecFourcc >>> (8 * index)) & 0xFF;
            characters[index] = value >= 0x20 && value < 0x7F ? (char) value : '?';
        }
        return new String(characters).trim();
    }
}
