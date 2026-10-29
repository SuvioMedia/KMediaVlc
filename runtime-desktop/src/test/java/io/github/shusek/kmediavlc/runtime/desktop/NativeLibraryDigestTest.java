// SPDX-License-Identifier: LGPL-2.1-or-later
package io.github.shusek.kmediavlc.runtime.desktop;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Optional;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

final class NativeLibraryDigestTest {
    /** The same library digested by KMediaFfmpegRuntime and scripts/package_native_runtime.py. */
    static final String EXPECTED_DIGEST =
            "0f884355e9c78c6a4762fc0e6f06816a1ecb8c7fbaa6f3bb1897272ff94f3b5e";

    @TempDir Path directory;

    @Test
    void reSigningKeepsTheSignatureIndependentDigest() throws IOException {
        Path linkerSigned = write("linker.dylib", MachO.library().build());
        Path reSigned = write("resigned.dylib", MachO.library().reSigned().build());

        assertNotEquals(NativeLibraryDigest.sha256(linkerSigned), NativeLibraryDigest.sha256(reSigned));
        assertEquals(
                NativeLibraryDigest.signatureIndependentSha256(linkerSigned),
                NativeLibraryDigest.signatureIndependentSha256(reSigned));
        assertEquals(Optional.of(EXPECTED_DIGEST), NativeLibraryDigest.signatureIndependentSha256(reSigned));
        assertTrue(
                NativeLibraryDigest.matches(
                        reSigned, NativeLibraryDigest.sha256(linkerSigned), EXPECTED_DIGEST));
        assertFalse(NativeLibraryDigest.matches(reSigned, NativeLibraryDigest.sha256(linkerSigned), null));
    }

    @Test
    void codeAndLoadCommandChangesChangeTheDigest() throws IOException {
        Path code = write("code.dylib", MachO.library().reSigned().code((byte) 0x7f).build());
        Path rpath = write("rpath.dylib", MachO.library().rpath("@executable_path").build());

        assertNotEquals(Optional.of(EXPECTED_DIGEST), NativeLibraryDigest.signatureIndependentSha256(code));
        assertNotEquals(Optional.of(EXPECTED_DIGEST), NativeLibraryDigest.signatureIndependentSha256(rpath));
        assertFalse(NativeLibraryDigest.matches(code, "0".repeat(64), EXPECTED_DIGEST));
    }

    @Test
    void otherFilesUseOnlyTheExactDigest() throws IOException {
        Path library = write("libexample.so", "\u007fELF not a Mach-O file".getBytes(StandardCharsets.US_ASCII));

        assertEquals(Optional.empty(), NativeLibraryDigest.signatureIndependentSha256(library));
        assertTrue(NativeLibraryDigest.matches(library, NativeLibraryDigest.sha256(library), EXPECTED_DIGEST));
        assertFalse(NativeLibraryDigest.matches(library, "0".repeat(64), EXPECTED_DIGEST));
    }

    @Test
    void rejectsMachOFilesWithoutOneTrailingSignature() throws IOException {
        Path unsigned = write("unsigned.dylib", MachO.library().unsigned().build());
        Path trailing = write("trailing.dylib", MachO.library().trailingBytes(16).build());
        Path foreignBlob = write("blob.dylib", MachO.library().signatureMagic(0x12345678).build());

        for (Path file : new Path[] {unsigned, trailing, foreignBlob}) {
            assertThrows(IOException.class, () -> NativeLibraryDigest.signatureIndependentSha256(file));
            assertThrows(IOException.class, () -> NativeLibraryDigest.matches(file, "0".repeat(64), EXPECTED_DIGEST));
        }
    }

    private Path write(String name, byte[] bytes) throws IOException {
        Path file = directory.resolve(name);
        Files.write(file, bytes);
        return file;
    }

    /** Builds a minimal arm64 dylib layout: __TEXT, __LINKEDIT, code signature and an rpath. */
    static final class MachO {
        private static final int TEXT_SIZE = 0x4000;
        private static final int LINKEDIT_CONTENT = 0x100;
        private int signatureSize = 0x90;
        private boolean signatureLast;
        private boolean signed = true;
        private byte code = 0x11;
        private String rpath = "@loader_path";
        private int trailingBytes;
        private int signatureMagic = 0xfade0cc0;

        static MachO library() {
            return new MachO();
        }

        /** Mimics codesign: a larger signature, moved to the end of the load commands. */
        MachO reSigned() {
            signatureSize = 0x2a0;
            signatureLast = true;
            return this;
        }

        MachO unsigned() {
            signed = false;
            return this;
        }

        MachO code(byte value) {
            code = value;
            return this;
        }

        MachO rpath(String value) {
            rpath = value;
            return this;
        }

        MachO trailingBytes(int count) {
            trailingBytes = count;
            return this;
        }

        MachO signatureMagic(int magic) {
            signatureMagic = magic;
            return this;
        }

        byte[] build() {
            byte[] rpathBytes = rpath.getBytes(StandardCharsets.US_ASCII);
            int rpathSize = (12 + rpathBytes.length + 1 + 7) & ~7;
            int commandCount = signed ? 4 : 3;
            int commandBytes = 72 + 72 + rpathSize + (signed ? 16 : 0);
            int signatureOffset = TEXT_SIZE + LINKEDIT_CONTENT;
            int length = signatureOffset + (signed ? signatureSize : 0) + trailingBytes;
            ByteBuffer file = ByteBuffer.allocate(length).order(ByteOrder.LITTLE_ENDIAN);
            file.putInt(0xfeedfacf).putInt(0x0100000c).putInt(0).putInt(6);
            file.putInt(commandCount).putInt(commandBytes).putInt(0x00100085).putInt(0);
            segment(file, "__TEXT", 0, TEXT_SIZE, 0, TEXT_SIZE, 5);
            int linkeditSize = signatureOffset + (signed ? signatureSize : 0) - TEXT_SIZE;
            if (signed && !signatureLast) {
                signature(file, signatureOffset);
            }
            segment(file, "__LINKEDIT", TEXT_SIZE, (linkeditSize + 0x3fff) & ~0x3fff, TEXT_SIZE, linkeditSize, 1);
            file.putInt(0x8000001c).putInt(rpathSize).putInt(12).put(rpathBytes);
            file.position(file.position() + rpathSize - 12 - rpathBytes.length);
            if (signed && signatureLast) {
                signature(file, signatureOffset);
            }
            for (int index = file.position(); index < TEXT_SIZE; index++) {
                file.put(index, code);
            }
            for (int index = TEXT_SIZE; index < signatureOffset; index++) {
                file.put(index, (byte) index);
            }
            if (signed) {
                file.order(ByteOrder.BIG_ENDIAN).position(signatureOffset);
                file.putInt(signatureMagic).putInt(signatureSize).putInt(0);
                for (int index = signatureOffset + 12; index < signatureOffset + signatureSize; index++) {
                    file.put(index, (byte) signatureSize);
                }
            }
            return file.array();
        }

        private void signature(ByteBuffer file, int signatureOffset) {
            file.putInt(0x1d).putInt(16).putInt(signatureOffset).putInt(signatureSize);
        }

        private static void segment(
                ByteBuffer file, String name, long vmAddress, long vmSize, long fileOffset, long fileSize, int protection) {
            byte[] segmentName = new byte[16];
            byte[] ascii = name.getBytes(StandardCharsets.US_ASCII);
            System.arraycopy(ascii, 0, segmentName, 0, ascii.length);
            file.putInt(0x19).putInt(72).put(segmentName);
            file.putLong(vmAddress).putLong(vmSize).putLong(fileOffset).putLong(fileSize);
            file.putInt(protection).putInt(protection).putInt(0).putInt(0);
        }
    }
}
