// SPDX-License-Identifier: LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.desktop;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.List;
import org.junit.jupiter.api.Test;

final class VlcTrackDecodingTest {
    @Test
    void decodesTheBridgeEncoding() throws IOException {
        byte[] encoded = encode(2, output -> {
            track(output, 0x74627573, 0, 0, 0, true, true, "spu/0", "Polski 🎬", "pl", null);
            track(output, 0x20617373, 0, 0, 0, false, false, "spu/1", null, null, "Forced");
        });

        var tracks = VlcDesktopPlayer.decodeTracks(VlcTrackType.SUBTITLE, encoded);

        assertEquals(
                List.of(
                        new VlcTrack(VlcTrackType.SUBTITLE, "spu/0", "Polski 🎬", "pl", "",
                                0x74627573, 0, 0, 0, true, true),
                        new VlcTrack(VlcTrackType.SUBTITLE, "spu/1", "", "", "Forced",
                                0x20617373, 0, 0, 0, false, false)),
                tracks);
        assertEquals("subt", tracks.get(0).codecName());
        assertEquals("ssa", tracks.get(1).codecName());
    }

    @Test
    void rejectsTruncatedOrOversizedLists() throws IOException {
        byte[] encoded = encode(1, output ->
                track(output, 0, 0, 0, 0, false, false, "audio/0", null, null, null));
        byte[] truncated = java.util.Arrays.copyOf(encoded, encoded.length - 1);
        assertThrows(java.nio.BufferUnderflowException.class,
                () -> VlcDesktopPlayer.decodeTracks(VlcTrackType.AUDIO, truncated));
        byte[] trailing = java.util.Arrays.copyOf(encoded, encoded.length + 1);
        assertThrows(IllegalArgumentException.class, () -> VlcDesktopPlayer.decodeTracks(VlcTrackType.AUDIO, trailing));
        assertThrows(IllegalArgumentException.class,
                () -> VlcDesktopPlayer.decodeTracks(VlcTrackType.AUDIO, encode(257, output -> {})));
        byte[] withoutId = encode(1, output ->
                track(output, 0, 0, 0, 0, false, false, null, null, null, null));
        assertThrows(IllegalArgumentException.class, () -> VlcDesktopPlayer.decodeTracks(VlcTrackType.AUDIO, withoutId));
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
