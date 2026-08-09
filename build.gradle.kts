// SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

import org.gradle.api.tasks.Exec

plugins {
    base
}

val publicationVersion = providers.gradleProperty("publicationVersion").orElse("0.1.0-SNAPSHOT")

allprojects {
    group = "io.github.shusek"
    version = publicationVersion.get()
}

val pythonExecutable =
    providers
        .gradleProperty("kmediaVlcPythonExecutable")
        .orElse(providers.environmentVariable("KMEDIAVLC_PYTHON"))
        .orElse(if (System.getProperty("os.name").startsWith("Windows")) "python" else "python3")

val operatingSystem = System.getProperty("os.name").lowercase()
val nativeArchitecture =
    providers.gradleProperty("kmediaVlcNativeArchitecture").orElse(System.getProperty("os.arch"))
val nativeTargetName =
    provider {
        val os = when {
            operatingSystem.contains("win") -> "windows"
            operatingSystem.contains("mac") -> "macos"
            else -> "linux"
        }
        val arch = when (nativeArchitecture.get().lowercase()) {
            "amd64", "x86_64" -> "x86_64"
            "aarch64", "arm64" -> "aarch64"
            else -> error("Unsupported KMediaVlc native architecture: ${nativeArchitecture.get()}")
        }
        "$os-$arch"
    }
val nativeBuildDirectory = layout.buildDirectory.dir(nativeTargetName.map { "native/$it" })
val cmakeExecutable = providers.gradleProperty("kmediaVlcCMakeExecutable").orElse("cmake")
val vlcSourceDirectory = providers.gradleProperty("kmediaVlcVlcSourceDir").map(::file)
val nativeBuildType = providers.gradleProperty("kmediaVlcNativeBuildType").orElse("RelWithDebInfo")
val nativeBridgeBinary =
    nativeBuildDirectory.zip(nativeBuildType) { directory, buildType ->
        when {
            operatingSystem.contains("win") -> directory.file("$buildType/kmediavlc_bridge.dll")
            operatingSystem.contains("mac") -> directory.file("libkmediavlc_bridge.dylib")
            else -> directory.file("libkmediavlc_bridge.so")
        }
    }

val configureNativeBridge =
    tasks.register<Exec>("configureNativeBridge") {
        group = "build"
        description = "Configures the JNI/GPU bridge against the exact pinned VLC source checkout."
        inputs.dir(layout.projectDirectory.dir("native"))
        inputs.dir(vlcSourceDirectory)
        outputs.file(nativeBuildDirectory.map { it.file("CMakeCache.txt") })
        doFirst {
            require(vlcSourceDirectory.isPresent) {
                "configureNativeBridge requires -PkmediaVlcVlcSourceDir."
            }
            val arguments =
                mutableListOf(
                    cmakeExecutable.get(),
                    "-S",
                    layout.projectDirectory.dir("native").asFile.absolutePath,
                    "-B",
                    nativeBuildDirectory.get().asFile.absolutePath,
                    "-DKMEDIAVLC_VLC_SOURCE_DIR=${vlcSourceDirectory.get().absolutePath}",
                )
            if (operatingSystem.contains("win")) {
                arguments += listOf(
                    "-G",
                    "Visual Studio 17 2022",
                    "-A",
                    if (nativeTargetName.get().endsWith("aarch64")) "ARM64" else "x64",
                )
            } else {
                arguments += "-DCMAKE_BUILD_TYPE=${nativeBuildType.get()}"
            }
            commandLine(arguments)
        }
    }

tasks.register<Exec>("buildNativeBridge") {
    group = "build"
    description = "Builds the KMediaVlc bridge after the pinned-header ABI gate succeeds."
    dependsOn(configureNativeBridge)
    inputs.dir(layout.projectDirectory.dir("native"))
    outputs.file(nativeBridgeBinary)
    doFirst {
        commandLine(
            cmakeExecutable.get(),
            "--build",
            nativeBuildDirectory.get().asFile.absolutePath,
            "--config",
            nativeBuildType.get(),
            "--parallel",
        )
    }
}

val verifySourceCompliance =
    tasks.register<Exec>("verifySourceCompliance") {
        group = "verification"
        description = "Runs the fail-closed source, license, and native payload policy audit."
        workingDir(layout.projectDirectory)
        commandLine(
            pythonExecutable.get(),
            "scripts/verify_source_compliance.py",
            "--root",
            layout.projectDirectory.asFile.absolutePath,
        )
    }

val testPackagingPolicy =
    tasks.register<Exec>("testPackagingPolicy") {
        group = "verification"
        description = "Exercises the fail-closed native payload and licensing packager."
        workingDir(layout.projectDirectory)
        commandLine(
            pythonExecutable.get(),
            "-m",
            "unittest",
            "discover",
            "-s",
            "scripts/tests",
            "-p",
            "test_*.py",
        )
    }

tasks.named("check") {
    dependsOn(":runtime-desktop:check")
    dependsOn(verifySourceCompliance)
    dependsOn(testPackagingPolicy)
}

tasks.register("complianceCheck") {
    group = "verification"
    description = "Runs JVM tests and every repository-level licensing gate."
    dependsOn(":runtime-desktop:check")
    dependsOn(verifySourceCompliance)
    dependsOn(testPackagingPolicy)
}
