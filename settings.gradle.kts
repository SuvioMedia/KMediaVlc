// SPDX-License-Identifier: LGPL-2.1-or-later

pluginManagement {
    repositories {
        google()
        gradlePluginPortal()
        mavenCentral()
    }
}

dependencyResolutionManagement {
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "KMediaVlc"
include(":runtime-desktop")

val desktopOnly =
    providers.gradleProperty("kmediaVlcDesktopOnly").orNull?.let { configuredValue ->
        require(configuredValue == "true" || configuredValue == "false") {
            "kmediaVlcDesktopOnly must be either true or false."
        }
        configuredValue.toBoolean()
    } ?: false

if (!desktopOnly) {
    include(":runtime-android")
}
