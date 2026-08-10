# SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

# Native entry points are resolved by their Java/JNI names.
-keep class io.github.shusek.kmediavlc.runtime.android.NativeBridge { *; }
-keep public class io.github.shusek.kmediavlc.runtime.android.** { public protected *; }
