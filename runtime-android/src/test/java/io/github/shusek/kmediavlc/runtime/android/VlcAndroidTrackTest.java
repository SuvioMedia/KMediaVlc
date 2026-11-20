// SPDX-License-Identifier: LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.android;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import org.junit.jupiter.api.Test;

final class VlcAndroidTrackTest {
    @Test
    void decodesTheBridgeEncoding() throws IOException {
        byte[] encoded = encode(2, output -> {
            track(output, 0x6134706D, 6, 48_000, 384_000, true, true, "audio/0", "English 5.1", "en", null);
            track(output, 0x33636165, 2, 44_100, 0, false, false, "audio/1", "Polski 🎬", null, "Commentary");
        });

        var tracks = VlcAndroidTrack.decode(VlcAndroidTrackType.AUDIO, encoded);

        assertEquals(2, tracks.size());
        var first = tracks.get(0);
        assertEquals("audio/0", first.getId());
        assertEquals("English 5.1", first.getName());
        assertEquals("en", first.getLanguage());
        assertEquals("", first.getDescription());
        assertEquals("mp4a", first.getCodecName());
        assertEquals(6, first.getAudioChannels());
        assertEquals(48_000, first.getAudioSampleRate());
        assertEquals(384_000, first.getBitrate());
        assertTrue(first.isSelected());
        assertTrue(first.hasStableId());
        var second = tracks.get(1);
        assertEquals("Polski 🎬", second.getName());
        assertEquals("", second.getLanguage());
        assertEquals("Commentary", second.getDescription());
        assertEquals("eac3", second.getCodecName());
        assertFalse(second.isSelected());
    }

    @Test
    void rejectsMalformedLists() throws IOException {
        byte[] encoded = encode(1, output ->
                track(output, 0, 0, 0, 0, false, false, "spu/0", null, null, null));
        assertThrows(VlcAndroidException.class, () -> VlcAndroidTrack.decode(
                VlcAndroidTrackType.SUBTITLE, Arrays.copyOf(encoded, encoded.length - 1)));
        assertThrows(VlcAndroidException.class, () -> VlcAndroidTrack.decode(
                VlcAndroidTrackType.SUBTITLE, Arrays.copyOf(encoded, encoded.length + 1)));
        assertThrows(VlcAndroidException.class, () -> VlcAndroidTrack.decode(
                VlcAndroidTrackType.SUBTITLE, encode(257, output -> {})));
        assertThrows(VlcAndroidException.class, () -> VlcAndroidTrack.decode(
                VlcAndroidTrackType.SUBTITLE,
                encode(1, output -> track(output, 0, 0, 0, 0, false, false, null, null, null, null))));
        assertThrows(VlcAndroidException.class, () -> VlcAndroidTrack.decode(VlcAndroidTrackType.SUBTITLE, null));
    }

    private interface Writer {
        void write(DataOutputStream output) throws IOException;
    }

    private static byte[] encode(int count, Writer tracks) throws IOException {
        var bytes = new ByteArrayOutputStream();
        try (var output = new DataOutputStream(bytes)) {
            output.writeInt(count);
            tracks.write(output);
        }
        return bytes.toByteArray();
    }

    private static void track(
            DataOutputStream output,
            int codec,
            int channels,
            int rate,
            int bitrate,
            boolean selected,
            boolean stable,
            String... texts) throws IOException {
        output.writeInt(codec);
        output.writeInt(channels);
        output.writeInt(rate);
        output.writeInt(bitrate);
        output.writeByte(selected ? 1 : 0);
        output.writeByte(stable ? 1 : 0);
        for (String text : texts) {
            if (text == null) {
                output.writeInt(-1);
            } else {
                byte[] utf8 = text.getBytes(StandardCharsets.UTF_8);
                output.writeInt(utf8.length);
                output.write(utf8);
            }
        }
    }
}
