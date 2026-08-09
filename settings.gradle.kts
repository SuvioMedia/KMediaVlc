// SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

pluginManagement {
    repositories {
        gradlePluginPortal()
        mavenCentral()
    }
}

dependencyResolutionManagement {
    repositories {
        mavenCentral()
    }
}

rootProject.name = "KMediaVlc"
include(":runtime-desktop")
