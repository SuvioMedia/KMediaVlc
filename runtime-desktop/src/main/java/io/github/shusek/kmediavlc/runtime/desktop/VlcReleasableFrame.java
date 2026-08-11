// SPDX-License-Identifier: LGPL-2.1-or-later

package io.github.shusek.kmediavlc.runtime.desktop;

/** Frame whose native buffer ownership is transferred exactly once to the consumer. */
public interface VlcReleasableFrame extends AutoCloseable {
    long serial();

    long generation();

    @Override
    void close();
}
