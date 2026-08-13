// SPDX-License-Identifier: LGPL-2.1-or-later

import org.gradle.api.publish.maven.MavenPublication
import org.gradle.api.publish.maven.tasks.PublishToMavenRepository
import org.gradle.api.publish.tasks.GenerateModuleMetadata
import org.gradle.jvm.tasks.Jar

plugins {
    base
    `maven-publish`
}

val publicationVersionValue = providers.gradleProperty("publicationVersion").orElse("0.1.0-SNAPSHOT").get()
val xcframeworkArchive =
    providers.gradleProperty("kmediaVlcIosXcframeworkArchive").map(rootProject::file)
val correspondingSourceArchive =
    providers.gradleProperty("kmediaVlcIosCorrespondingSourceArchive").map(rootProject::file)
val podspec = providers.gradleProperty("kmediaVlcIosPodspec").map(rootProject::file)
val recipeRevision = providers.gradleProperty("recipeRevision")
val pythonExecutable =
    providers.gradleProperty("kmediaVlcPythonExecutable").orElse("python3")

val immutableVersion =
    Regex("^(0|[1-9]\\d*)\\.(0|[1-9]\\d*)\\.(0|[1-9]\\d*)(?:-[0-9A-Za-z-]+(?:\\.[0-9A-Za-z-]+)*)?$")

val iosSourcesJar =
    tasks.register<Jar>("iosSourcesJar") {
        archiveClassifier.set("sources")
        from(rootProject.layout.projectDirectory) {
            include(
                "LICENSE",
                "NOTICE",
                "THIRD_PARTY_NOTICES.md",
                "docs/IOS.md",
                "native/include/kmediavlc_client.h",
                "scripts/build_vlc_ios.sh",
                "scripts/build_kmediavlc_ios_bridge.sh",
                "scripts/stage_vlc_ios_frameworks.py",
                "scripts/assemble_ios_xcframeworks.py",
                "scripts/verify_ios_xcframework_archive.py",
                "build-recipes/ios.json",
                "build-recipes/vlc-apple.conf",
                "build-recipes/vlc-apple-native.ini",
                "build-recipes/vlc-contrib-utfcpp-rules.mak",
                "build-recipes/patches/fribidi-meson-native-generator.patch",
                "build-recipes/patches/vlc-ios-meson-native-compiler.patch",
                "compliance/policy/ios-binary-components.json",
                "compliance/policy/ios-playback-modules.json",
            )
        }
    }

val iosJavadocJar =
    tasks.register<Jar>("iosJavadocJar") {
        archiveClassifier.set("javadoc")
    }

val validatePublication =
    tasks.register("validateIosPublication") {
        group = "verification"
        description = "Validates the immutable Maven-delivered iOS XCFramework payload."
        inputs.property("publicationVersion", publicationVersionValue)
        xcframeworkArchive.orNull?.let(inputs::file)
        correspondingSourceArchive.orNull?.let(inputs::file)
        podspec.orNull?.let(inputs::file)
        inputs.property("recipeRevision", recipeRevision)
        doLast {
            check(immutableVersion.matches(publicationVersionValue) && !publicationVersionValue.contains("SNAPSHOT", true)) {
                "The iOS runtime requires an immutable non-SNAPSHOT SemVer publication version."
            }
            val runtime = xcframeworkArchive.orNull
                ?: error("Set kmediaVlcIosXcframeworkArchive for the iOS runtime publication.")
            check(runtime.isFile && runtime.length() > 0L && runtime.extension == "zip") {
                "The iOS XCFramework publication must be a non-empty ZIP file."
            }
            val source = correspondingSourceArchive.orNull
                ?: error("Set kmediaVlcIosCorrespondingSourceArchive for the iOS runtime publication.")
            check(source.isFile && source.length() > 0L && source.name.endsWith(".tar.gz")) {
                "The iOS corresponding-source publication must be a non-empty tar.gz file."
            }
            val generatedPodspec = podspec.orNull
                ?: error("Set kmediaVlcIosPodspec for independent iOS archive verification.")
            check(generatedPodspec.isFile && generatedPodspec.name == "KMediaVlc.podspec") {
                "The generated iOS podspec is missing."
            }
            check(Regex("^[0-9a-f]{40}$").matches(recipeRevision.get())) {
                "The iOS publication requires the exact forty-character recipeRevision."
            }
        }
    }

val verifyIosXcframeworkArchive =
    tasks.register<Exec>("verifyIosXcframeworkArchive") {
        group = "verification"
        description = "Independently reopens and verifies the Maven-delivered iOS XCFramework graph."
        dependsOn(validatePublication)
        inputs.file(xcframeworkArchive)
        inputs.file(podspec)
        inputs.property("publicationVersion", publicationVersionValue)
        inputs.property("recipeRevision", recipeRevision)
        doFirst {
            commandLine(
                pythonExecutable.get(),
                rootProject.file("scripts/verify_ios_xcframework_archive.py").absolutePath,
                "--archive",
                xcframeworkArchive.get().absolutePath,
                "--podspec",
                podspec.get().absolutePath,
                "--expected-version",
                publicationVersionValue,
                "--expected-revision",
                recipeRevision.get(),
                "--allow-audit-candidate",
            )
        }
    }

tasks.named("check") {
    dependsOn(iosSourcesJar, iosJavadocJar)
}

tasks.withType<PublishToMavenRepository>().configureEach {
    dependsOn(verifyIosXcframeworkArchive)
}

tasks.withType<GenerateModuleMetadata>().configureEach {
    enabled = false
}

publishing {
    publications {
        create<MavenPublication>("maven") {
            xcframeworkArchive.orNull?.let { archive ->
                artifact(archive) {
                    extension = "zip"
                }
            }
            artifact(iosSourcesJar)
            artifact(iosJavadocJar)
            correspondingSourceArchive.orNull?.let { archive ->
                artifact(archive) {
                    classifier = "corresponding-source"
                    extension = "tar.gz"
                }
            }
            groupId = "io.github.shusek"
            artifactId = "kmedia-vlc-runtime-ios"
            version = publicationVersionValue
            pom {
                name.set("KMediaVlc Runtime for iOS")
                description.set("Maven-delivered libVLC 4 XCFramework runtime for iOS devices and Apple Silicon simulators.")
                inceptionYear.set("2026")
                url.set("https://github.com/SuvioMedia/KMediaVlc")
                licenses {
                    license {
                        name.set("GNU Lesser General Public License, version 2.1 or later")
                        url.set("https://www.gnu.org/licenses/old-licenses/lgpl-2.1.html")
                        distribution.set("repo")
                    }
                }
                developers {
                    developer {
                        id.set("Shusek")
                        name.set("Shusek")
                        url.set("https://github.com/Shusek")
                    }
                }
                scm {
                    connection.set("scm:git:https://github.com/SuvioMedia/KMediaVlc.git")
                    developerConnection.set("scm:git:ssh://git@github.com/SuvioMedia/KMediaVlc.git")
                    url.set("https://github.com/SuvioMedia/KMediaVlc")
                    tag.set("v$publicationVersionValue")
                }
            }
        }
    }
    repositories {
        rootProject.providers.gradleProperty("releaseRepository").orNull?.let { path ->
            maven {
                name = "release"
                url = uri(path)
            }
        }
    }
}
