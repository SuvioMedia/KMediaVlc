// SPDX-License-Identifier: MIT OR LGPL-2.1-or-later
package io.github.shusek.kmediavlc.runtime.desktop;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.channels.FileChannel;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.Optional;

/**
 * Verifies native libraries against manifest digests, including macOS libraries whose code
 * signature was replaced after the manifest was written. Kept identical to KMediaFfmpegRuntime's
 * {@code NativeLibraryDigest}; scripts/package_native_runtime.py writes the same digest.
 *
 * <p>macOS packaging and notarization re-sign every bundled Mach-O file. That rewrites the
 * embedded signature, its size in {@code LC_CODE_SIGNATURE}, the {@code __LINKEDIT} segment sizes,
 * and may move {@code LC_CODE_SIGNATURE} to the end of the load commands. The
 * signature-independent SHA-256 covers every other byte before the signature, so all code, data,
 * and remaining load commands stay pinned by the manifest. It requires no particular signer: the
 * operating system validates whichever signature is present when it maps the code.
 */
final class NativeLibraryDigest {
    private static final int MH_MAGIC_64 = 0xfeedfacf;
    private static final int LC_SEGMENT_64 = 0x19;
    private static final int LC_CODE_SIGNATURE = 0x1d;
    private static final int CSMAGIC_EMBEDDED_SIGNATURE = 0xfade0cc0;
    private static final int HEADER_SIZE = 32;
    private static final int SEGMENT_NAME_OFFSET = 8;
    private static final int SEGMENT_VMSIZE_OFFSET = 32;
    private static final int SEGMENT_FILEOFF_OFFSET = 40;
    private static final int SEGMENT_FILESIZE_OFFSET = 48;
    private static final int MAX_LOAD_COMMAND_BYTES = 1024 * 1024;
    private static final byte[] LINKEDIT = "__LINKEDIT".getBytes(java.nio.charset.StandardCharsets.US_ASCII);

    private NativeLibraryDigest() {}

    /**
     * Returns whether {@code file} has the exact {@code sha256}, or is a signed Mach-O file whose
     * signature-independent digest equals {@code signatureIndependentSha256} when that is given.
     */
    static boolean matches(Path file, String sha256, String signatureIndependentSha256)
            throws IOException {
        if (sha256(file).equals(sha256)) {
            return true;
        }
        if (signatureIndependentSha256 == null) {
            return false;
        }
        return signatureIndependentSha256(file)
                .map(signatureIndependentSha256::equals)
                .orElse(false);
    }

    /** Returns the SHA-256 of every byte of {@code file}. */
    static String sha256(Path file) throws IOException {
        MessageDigest digest = newDigest();
        try (FileChannel channel = FileChannel.open(file, StandardOpenOption.READ)) {
            update(digest, channel, 0, channel.size());
        }
        return HexFormat.of().formatHex(digest.digest());
    }

    /**
     * Returns the signature-independent SHA-256 of a signed thin 64-bit Mach-O file, or empty when
     * {@code file} is not a 64-bit Mach-O file.
     *
     * @throws IOException when the file is a 64-bit Mach-O file without one well-formed embedded
     *     code signature at the end of {@code __LINKEDIT}
     */
    static Optional<String> signatureIndependentSha256(Path file) throws IOException {
        try (FileChannel channel = FileChannel.open(file, StandardOpenOption.READ)) {
            long length = channel.size();
            if (length < HEADER_SIZE) {
                return Optional.empty();
            }
            ByteBuffer header = read(channel, 0, HEADER_SIZE);
            if (header.getInt(0) != MH_MAGIC_64) {
                return Optional.empty();
            }
            int commandCount = header.getInt(16);
            int commandBytes = header.getInt(20);
            if (commandCount <= 0
                    || commandBytes <= 0
                    || commandBytes > MAX_LOAD_COMMAND_BYTES
                    || HEADER_SIZE + (long) commandBytes > length) {
                throw malformed();
            }
            ByteBuffer commands = read(channel, HEADER_SIZE, commandBytes);
            int signature = -1;
            int linkedit = -1;
            int offset = 0;
            for (int index = 0; index < commandCount; index++) {
                if (offset > commandBytes - 8) {
                    throw malformed();
                }
                int command = commands.getInt(offset);
                int size = commands.getInt(offset + 4);
                if (size < 8 || size % 8 != 0 || size > commandBytes - offset) {
                    throw malformed();
                }
                if (command == LC_CODE_SIGNATURE) {
                    if (signature >= 0 || size != 16) {
                        throw malformed();
                    }
                    signature = offset;
                } else if (command == LC_SEGMENT_64 && isLinkedit(commands, offset, size)) {
                    if (linkedit >= 0) {
                        throw malformed();
                    }
                    linkedit = offset;
                }
                offset += size;
            }
            if (offset != commandBytes || signature < 0 || linkedit < 0) {
                throw malformed();
            }
            long signatureOffset = Integer.toUnsignedLong(commands.getInt(signature + 8));
            long signatureSize = Integer.toUnsignedLong(commands.getInt(signature + 12));
            long linkeditVmSize = commands.getLong(linkedit + SEGMENT_VMSIZE_OFFSET);
            long linkeditOffset = commands.getLong(linkedit + SEGMENT_FILEOFF_OFFSET);
            long linkeditSize = commands.getLong(linkedit + SEGMENT_FILESIZE_OFFSET);
            if (signatureOffset < HEADER_SIZE + (long) commandBytes
                    || linkeditOffset < 0
                    || linkeditSize < 0
                    || Long.compareUnsigned(linkeditVmSize, linkeditSize) < 0
                    || linkeditOffset > signatureOffset
                    || signatureSize != length - signatureOffset
                    || linkeditSize != length - linkeditOffset
                    || signatureSize < 12) {
                throw malformed();
            }
            ByteBuffer blob = read(channel, signatureOffset, 8).order(ByteOrder.BIG_ENDIAN);
            long blobSize = Integer.toUnsignedLong(blob.getInt(4));
            if (blob.getInt(0) != CSMAGIC_EMBEDDED_SIGNATURE || blobSize < 12 || blobSize > signatureSize) {
                throw malformed();
            }

            commands.putLong(linkedit + SEGMENT_VMSIZE_OFFSET, 0);
            commands.putLong(linkedit + SEGMENT_FILESIZE_OFFSET, 0);
            MessageDigest digest = newDigest();
            digest.update(header.array());
            offset = 0;
            for (int index = 0; index < commandCount; index++) {
                int size = commands.getInt(offset + 4);
                if (offset != signature) {
                    digest.update(commands.array(), offset, size);
                }
                offset += size;
            }
            ByteBuffer signatureCommand = ByteBuffer.allocate(12).order(ByteOrder.LITTLE_ENDIAN);
            signatureCommand.putInt(LC_CODE_SIGNATURE).putInt(16).putInt((int) signatureOffset);
            digest.update(signatureCommand.array());
            update(digest, channel, HEADER_SIZE + (long) commandBytes, signatureOffset);
            return Optional.of(HexFormat.of().formatHex(digest.digest()));
        }
    }

    private static boolean isLinkedit(ByteBuffer commands, int offset, int size) {
        if (size < SEGMENT_FILESIZE_OFFSET + 8) {
            return false;
        }
        for (int index = 0; index < 16; index++) {
            byte expected = index < LINKEDIT.length ? LINKEDIT[index] : 0;
            if (commands.get(offset + SEGMENT_NAME_OFFSET + index) != expected) {
                return false;
            }
        }
        return true;
    }

    private static ByteBuffer read(FileChannel channel, long position, int size) throws IOException {
        ByteBuffer buffer = ByteBuffer.allocate(size).order(ByteOrder.LITTLE_ENDIAN);
        while (buffer.hasRemaining()) {
            if (channel.read(buffer, position + buffer.position()) < 0) {
                throw malformed();
            }
        }
        return buffer.clear();
    }

    private static void update(MessageDigest digest, FileChannel channel, long start, long end)
            throws IOException {
        ByteBuffer buffer = ByteBuffer.allocate(64 * 1024);
        for (long position = start; position < end; ) {
            buffer.clear().limit((int) Math.min(buffer.capacity(), end - position));
            int count = channel.read(buffer, position);
            if (count < 0) {
                throw malformed();
            }
            digest.update(buffer.array(), 0, count);
            position += count;
        }
    }

    private static MessageDigest newDigest() {
        try {
            return MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException impossible) {
            throw new AssertionError(impossible);
        }
    }

    private static IOException malformed() {
        return new IOException("A Mach-O library has no well-formed embedded code signature.");
    }
}
