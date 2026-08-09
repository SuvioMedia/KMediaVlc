// SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

package io.github.shusek.kmediavlc.runtime.desktop;

/** Frame whose native buffer ownership is transferred exactly once to the consumer. */
public interface VlcReleasableFrame extends AutoCloseable {
    long serial();

    long generation();

    @Override
    void close();
}
