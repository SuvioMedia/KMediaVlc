// SPDX-License-Identifier: LGPL-2.1-or-later

import org.gradle.api.publish.maven.MavenPublication
import org.gradle.api.publish.maven.tasks.PublishToMavenLocal
import org.gradle.api.publish.maven.tasks.PublishToMavenRepository
import org.gradle.api.tasks.Exec
import org.gradle.api.tasks.testing.logging.TestExceptionFormat
import java.util.zip.ZipFile

plugins {
    `java-library`
    `maven-publish`
}

java {
    toolchain.languageVersion.set(JavaLanguageVersion.of(21))
    withSourcesJar()
    withJavadocJar()
}

tasks.withType<JavaCompile>().configureEach {
    options.release.set(21)
    options.encoding = "UTF-8"
}

dependencies {
    testImplementation(platform(libs.junit.bom))
    testImplementation(libs.junit.jupiter)
    testRuntimeOnly(libs.junit.platform.launcher)
}

val nativeBridgeTestPath = providers.gradleProperty("kmediaVlcNativeBridgePath")
val libVlcTestPath = providers.gradleProperty("kmediaVlcTestLibVlcPath")
val fakeLibVlcTestPath = providers.gradleProperty("kmediaVlcTestFakeLibVlcPath")
val pluginTestDirectory = providers.gradleProperty("kmediaVlcTestPluginDirectory")
val hdrTestMedia = providers.gradleProperty("kmediaVlcTestHdrMedia")
val linuxRenderNode = providers.gradleProperty("kmediaVlcTestLinuxRenderNode")

tasks.test {
    useJUnitPlatform()
    nativeBridgeTestPath.orNull?.let { bridgePath ->
        inputs.file(bridgePath).withPropertyName("nativeBridgeTestBinary")
        systemProperty("kmediavlc.test.nativeBridge", bridgePath)
    }
    libVlcTestPath.orNull?.let { libVlcPath ->
        inputs.file(libVlcPath).withPropertyName("libVlcTestBinary")
        systemProperty("kmediavlc.test.libVlc", libVlcPath)
    }
    fakeLibVlcTestPath.orNull?.let { libVlcPath ->
        inputs.file(libVlcPath).withPropertyName("fakeLibVlcTestBinary")
        systemProperty("kmediavlc.test.fakeLibVlc", libVlcPath)
    }
    pluginTestDirectory.orNull?.let { pluginDirectory ->
        inputs.dir(pluginDirectory).withPropertyName("libVlcTestPluginDirectory")
        systemProperty("kmediavlc.test.plugins", pluginDirectory)
    }
    hdrTestMedia.orNull?.let { hdrMedia ->
        inputs.file(hdrMedia).withPropertyName("hdrTestMedia")
        systemProperty("kmediavlc.test.hdrMedia", hdrMedia)
    }
    linuxRenderNode.orNull?.let { renderNode ->
        inputs.property("linuxRenderNode", renderNode)
        systemProperty("kmediavlc.test.linuxRenderNode", renderNode)
    }
    providers.gradleProperty("kmediaVlcTestHttpsHdrMedia").orNull?.let { httpsHdrMedia ->
        systemProperty("kmediavlc.test.httpsHdrMedia", httpsHdrMedia)
    }
    if (providers.gradleProperty("kmediaVlcTestBundledRuntime").orNull == "true") {
        systemProperty("kmediavlc.test.bundledRuntime", "true")
    }
    if (providers.gradleProperty("kmediaVlcDebugCallbacks").orNull == "true") {
        environment("KMEDIAVLC_DEBUG_CALLBACKS", "1")
        testLogging {
            exceptionFormat = TestExceptionFormat.FULL
            showExceptions = true
            showCauses = true
            showStackTraces = true
            showStandardStreams = true
        }
    }
    if (providers.gradleProperty("kmediaVlcAllowSoftwareGl").orNull == "true") {
        environment("KMEDIAVLC_ALLOW_SOFTWARE_GL", "1")
    }
    outputs.upToDateWhen { !nativeBridgeTestPath.isPresent }
    outputs.doNotCacheIf("Native bridge integration must execute on the current hardware") {
        nativeBridgeTestPath.isPresent
    }
}

tasks.withType<Jar>().configureEach {
    isPreserveFileTimestamps = false
    isReproducibleFileOrder = true
}

val nativeStagingDirectory = providers.gradleProperty("kmediaVlcNativeStagingDirectory").map(project::file)
val nativeInventory = providers.gradleProperty("kmediaVlcNativeInventory").map(project::file)
val nativeTarget = providers.gradleProperty("kmediaVlcNativeTarget")
val nativeMatrix = providers.gradleProperty("kmediaVlcNativeMatrix").map(project::file)
val nativeSourceOffer = providers.gradleProperty("kmediaVlcSourceOffer")
val legacyNativeInputs =
    listOf(nativeStagingDirectory.isPresent, nativeInventory.isPresent, nativeTarget.isPresent)
val legacyNativeConfigured = legacyNativeInputs.all { it } && nativeSourceOffer.isPresent
val matrixNativeConfigured = nativeMatrix.isPresent && nativeSourceOffer.isPresent
val nativePackagingConfigured = legacyNativeConfigured || matrixNativeConfigured
if (nativePackagingConfigured) {
    tasks.test {
        systemProperty("kmediavlc.test.bundledManifestMatrix", "true")
    }
}
require(!(nativeMatrix.isPresent && legacyNativeInputs.any { it })) {
    "Native matrix packaging and legacy single-target inputs are mutually exclusive."
}
require(
    nativePackagingConfigured ||
        (!nativeMatrix.isPresent && legacyNativeInputs.none { it } && !nativeSourceOffer.isPresent),
) {
    "Native packaging requires either the complete desktop matrix plus source offer, " +
        "or staging directory, inventory, target, and source offer together."
}
val nativePayloadDirectory = rootProject.layout.buildDirectory.dir("verified-native-payload")
val recipeRevision = providers.gradleProperty("recipeRevision")
val checkoutRevision =
    providers.exec {
        workingDir(rootProject.layout.projectDirectory)
        commandLine(
            "git",
            "-c",
            "safe.directory=${rootProject.layout.projectDirectory.asFile.absolutePath.replace('\\', '/')}",
            "rev-parse",
            "HEAD",
        )
        isIgnoreExitValue = false
    }.standardOutput.asText.map(String::trim)
val publicationVersionValue = project.version.toString()
val correspondingSourceArchive =
    rootProject.providers.gradleProperty("correspondingSourceArchive").map(rootProject::file)

val packageNativeRuntime =
    tasks.register<Exec>("packageNativeRuntime") {
        group = "publishing"
        description = "Builds the only publication-eligible payload through the license/inventory gate."
        onlyIf { nativePackagingConfigured }
        if (nativePackagingConfigured) {
            inputs.file(rootProject.layout.projectDirectory.file("compliance/policy/release-policy.json"))
            inputs.file(rootProject.layout.projectDirectory.file("scripts/package_native_runtime.py"))
            if (matrixNativeConfigured) {
                inputs.file(nativeMatrix)
                inputs.file(
                    rootProject.layout.projectDirectory.file("scripts/package_native_runtime_matrix.py"),
                )
            } else {
                inputs.dir(nativeStagingDirectory)
                inputs.file(nativeInventory)
                inputs.property("nativeTarget", nativeTarget)
            }
            inputs.property("nativeSourceOffer", nativeSourceOffer)
            inputs.property("recipeRevision", recipeRevision)
            outputs.dir(nativePayloadDirectory)
            outputs.upToDateWhen { false }
        }
        doFirst {
            require(nativePackagingConfigured) { "Native release packaging inputs are incomplete." }
            require(recipeRevision.isPresent) {
                "Native release packaging requires -PrecipeRevision with the immutable build commit."
            }
            project.delete(nativePayloadDirectory.get().asFile)
            val command =
                mutableListOf(
                rootProject.providers
                    .gradleProperty("kmediaVlcPythonExecutable")
                    .orElse(rootProject.providers.environmentVariable("KMEDIAVLC_PYTHON"))
                    .orElse(if (System.getProperty("os.name").startsWith("Windows")) "python" else "python3")
                    .get(),
                )
            if (matrixNativeConfigured) {
                command +=
                    listOf(
                        rootProject.layout.projectDirectory
                            .file("scripts/package_native_runtime_matrix.py")
                            .asFile.absolutePath,
                        "--root",
                        rootProject.layout.projectDirectory.asFile.absolutePath,
                        "--matrix",
                        nativeMatrix.get().absolutePath,
                    )
            } else {
                command +=
                    listOf(
                        rootProject.layout.projectDirectory
                            .file("scripts/package_native_runtime.py")
                            .asFile.absolutePath,
                        "--root",
                        rootProject.layout.projectDirectory.asFile.absolutePath,
                        "--staging",
                        nativeStagingDirectory.get().absolutePath,
                        "--inventory",
                        nativeInventory.get().absolutePath,
                        "--target",
                        nativeTarget.get(),
                    )
            }
            command +=
                listOf(
                    "--source-offer",
                    nativeSourceOffer.get(),
                    "--recipe-revision",
                    recipeRevision.get(),
                    "--output",
                    nativePayloadDirectory.get().asFile.absolutePath,
                )
            commandLine(command)
        }
    }

tasks.named<ProcessResources>("processResources") {
    duplicatesStrategy = DuplicatesStrategy.FAIL
    from(rootProject.layout.projectDirectory.file("LICENSE")) { into("META-INF") }
    from(rootProject.layout.projectDirectory.file("NOTICE")) { into("META-INF") }
    from(rootProject.layout.projectDirectory.file("THIRD_PARTY_NOTICES.md")) { into("META-INF") }
    from(rootProject.layout.projectDirectory.dir("LICENSES")) {
        include("*.txt")
        into("META-INF/LICENSES")
    }
    if (nativePackagingConfigured) {
        dependsOn(packageNativeRuntime)
        from(nativePayloadDirectory) {
            include("META-INF/kmediavlc/**")
        }
    }
}

listOf("sourcesJar", "javadocJar").forEach { taskName ->
    tasks.named<Jar>(taskName) {
        duplicatesStrategy = DuplicatesStrategy.EXCLUDE
        from(rootProject.layout.projectDirectory.file("LICENSE")) { into("META-INF") }
        from(rootProject.layout.projectDirectory.file("NOTICE")) { into("META-INF") }
        from(rootProject.layout.projectDirectory.file("THIRD_PARTY_NOTICES.md")) { into("META-INF") }
        from(rootProject.layout.projectDirectory.dir("LICENSES")) {
            include("*.txt")
            into("META-INF/LICENSES")
        }
    }
}

val verifyNoCheckedInNativePayload =
    tasks.register("verifyNoCheckedInNativePayload") {
        group = "verification"
        description = "Rejects native VLC payloads committed to the source repository."
        val forbidden =
            rootProject.fileTree(rootProject.layout.projectDirectory) {
                exclude("**/build/**", ".gradle/**")
                include(
                    "**/*.a",
                    "**/*.dll",
                    "**/*.dylib",
                    "**/*.exe",
                    "**/*.lib",
                    "**/*.o",
                    "**/*.obj",
                    "**/*.so",
                    "**/*.so.*",
                )
            }
        inputs.files(forbidden)
        doLast {
            require(forbidden.isEmpty) {
                "Native libraries must be supplied as an explicit release payload, never committed."
            }
        }
    }

val validatePublicationVersion =
    tasks.register("validatePublicationVersion") {
        group = "verification"
        val semVerNumber = "(?:0|[1-9][0-9]*)"
        val semVerPreReleaseIdentifier = "(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
        val semVer =
            Regex(
                "$semVerNumber\\.$semVerNumber\\.$semVerNumber" +
                    "(?:-$semVerPreReleaseIdentifier(?:\\.$semVerPreReleaseIdentifier)*)?",
            )
        inputs.property("publicationVersion", publicationVersionValue)
        doLast {
            require(semVer.matches(publicationVersionValue)) {
                "publicationVersion must be immutable SemVer; got $publicationVersionValue"
            }
        }
    }

val verifyRuntimeJar =
    tasks.register("verifyRuntimeJar") {
        group = "verification"
        dependsOn(tasks.named("jar"))
        val archive = tasks.named<Jar>("jar").flatMap { it.archiveFile }
        inputs.file(archive)
        doLast {
            ZipFile(archive.get().asFile).use { jar ->
                val names = jar.entries().asSequence().map { it.name }.toList()
                require(names.size == names.toSet().size) { "Runtime JAR contains duplicate entries." }
                val required =
                    setOf(
                        "META-INF/LICENSE",
                        "META-INF/NOTICE",
                        "META-INF/THIRD_PARTY_NOTICES.md",
                    )
                require(names.containsAll(required)) { "Runtime JAR is missing mandatory legal files." }
                val graalMetadataPath =
                    "META-INF/native-image/io.github.shusek/" +
                        "kmedia-vlc-runtime-desktop/reachability-metadata.json"
                val graalMetadataEntry = jar.getEntry(graalMetadataPath)
                require(graalMetadataEntry != null) {
                    "Runtime JAR is missing GraalVM JNI reachability metadata."
                }
                val graalMetadata =
                    jar.getInputStream(graalMetadataEntry).bufferedReader().use { reader -> reader.readText() }
                require(
                    graalMetadata.contains("VlcDesktopPlayer\$NativeEventSink") &&
                        graalMetadata.contains("onFrameAvailable") &&
                        graalMetadata.contains("onPlaybackStateChanged"),
                ) {
                    "GraalVM JNI reachability metadata is missing the native event callbacks."
                }
                val expectedLicenses =
                    rootProject.layout.projectDirectory
                        .dir("LICENSES")
                        .asFile
                        .listFiles { file -> file.isFile && file.extension == "txt" }
                        .orEmpty()
                        .map { "META-INF/LICENSES/${it.name}" }
                        .toSet()
                val packagedLicenses =
                    names.filter { it.startsWith("META-INF/LICENSES/") && !it.endsWith("/") }.toSet()
                require(packagedLicenses == expectedLicenses) {
                    "Runtime JAR legal inventory differs from the repository LICENSES directory."
                }
                val containsNative = names.any { it.startsWith("META-INF/kmediavlc/native/") }
                require(containsNative == nativePackagingConfigured) {
                    "Runtime JAR native resources do not match the explicit payload input."
                }
                if (matrixNativeConfigured) {
                    val requiredTargets =
                        setOf(
                            "linux-aarch64",
                            "linux-x86_64",
                            "macos-aarch64",
                            "windows-x86_64",
                        )
                    val packagedTargets =
                        names
                            .filter {
                                it.startsWith("META-INF/kmediavlc/native/") &&
                                    it.endsWith("/manifest.properties")
                            }.map { it.split('/')[3] }
                            .toSet()
                    require(packagedTargets == requiredTargets) {
                        "Runtime JAR desktop target matrix is partial: $packagedTargets"
                    }
                }
            }
        }
    }

val requireNativePayloadForPublication =
    tasks.register("requireNativePayloadForPublication") {
        group = "publishing"
        dependsOn(verifyRuntimeJar, validatePublicationVersion)
        doLast {
            require(matrixNativeConfigured) {
                "Desktop publication requires the complete audited Windows, Linux, and macOS matrix."
            }
            require(recipeRevision.isPresent) {
                "Publishing requires -PrecipeRevision with the immutable build commit."
            }
            require(recipeRevision.get().matches(Regex("[0-9a-f]{40}"))) {
                "recipeRevision must be an exact lowercase forty-character Git commit."
            }
            if (recipeRevision.get() != checkoutRevision.get()) {
                val ancestor =
                    providers.exec {
                        workingDir(rootProject.layout.projectDirectory)
                        commandLine(
                            "git",
                            "merge-base",
                            "--is-ancestor",
                            recipeRevision.get(),
                            checkoutRevision.get(),
                        )
                        isIgnoreExitValue = true
                    }.result.get().exitValue
                require(ancestor == 0) {
                    "The desktop runtime commit must be an ancestor of the release commit."
                }
                val behaviorPaths =
                    listOf(
                        "native",
                        "runtime-desktop/src",
                        "build-recipes/linux.json",
                        "build-recipes/macos.json",
                        "build-recipes/windows.json",
                        "patches/vlc",
                        "gradle/libs.versions.toml",
                        "compliance/policy/release-policy.json",
                        "compliance/policy/linux-playback-modules.json",
                        "compliance/policy/linux-binary-components.json",
                        "compliance/policy/macos-aarch64-playback-modules.json",
                        "compliance/policy/macos-aarch64-binary-components.json",
                        "compliance/policy/windows-x86_64-playback-modules.json",
                        "compliance/policy/windows-x86_64-binary-components.json",
                        "scripts/package_native_runtime.py",
                        "scripts/package_native_runtime_matrix.py",
                    )
                val changedBehaviorPaths =
                    providers
                        .exec {
                            workingDir(rootProject.layout.projectDirectory)
                            commandLine(
                                listOf(
                                    "git",
                                    "diff",
                                    "--name-only",
                                    "${recipeRevision.get()}..${checkoutRevision.get()}",
                                    "--",
                                ) + behaviorPaths,
                            )
                        }.standardOutput.asText
                        .get()
                        .lineSequence()
                        .filter(String::isNotBlank)
                        .toSet()
                val allowedJavaOnlyChanges =
                    setOf(
                        "runtime-desktop/src/main/java/io/github/shusek/kmediavlc/runtime/desktop/NativePayloadManifest.java",
                        "runtime-desktop/src/main/resources/META-INF/native-image/io.github.shusek/" +
                            "kmedia-vlc-runtime-desktop/reachability-metadata.json",
                        "runtime-desktop/src/test/java/io/github/shusek/kmediavlc/runtime/desktop/NativePayloadManifestTest.java",
                    )
                val unexpectedBehaviorPaths = changedBehaviorPaths - allowedJavaOnlyChanges
                require(unexpectedBehaviorPaths.isEmpty()) {
                    "Desktop runtime behavior or packaging policy changed after its source build: " +
                        unexpectedBehaviorPaths.sorted().joinToString()
                }
            }
            require(!publicationVersionValue.contains("SNAPSHOT", ignoreCase = true)) {
                "Publishing requires an immutable non-SNAPSHOT version."
            }
            val expectedSourceOffer =
                "https://github.com/SuvioMedia/KMediaVlc/releases/download/" +
                    "v$publicationVersionValue/kmedia-vlc-$publicationVersionValue-corresponding-source.tar.gz"
            require(nativeSourceOffer.get() == expectedSourceOffer) {
                "kmediaVlcSourceOffer must identify the corresponding-source asset of this exact release."
            }
            require(correspondingSourceArchive.isPresent) {
                "Publishing requires -PcorrespondingSourceArchive."
            }
            require(correspondingSourceArchive.get().isFile && correspondingSourceArchive.get().length() > 0L) {
                "The corresponding source archive must be a non-empty regular file."
            }
        }
    }

tasks.withType<PublishToMavenRepository>().configureEach {
    dependsOn(packageNativeRuntime)
    dependsOn(requireNativePayloadForPublication)
}
tasks.withType<PublishToMavenLocal>().configureEach {
    dependsOn(packageNativeRuntime)
    dependsOn(requireNativePayloadForPublication)
}

tasks.named("check") {
    dependsOn(verifyNoCheckedInNativePayload)
    dependsOn(validatePublicationVersion)
    dependsOn(verifyRuntimeJar)
}

publishing {
    publications {
        create<MavenPublication>("maven") {
            from(components["java"])
            correspondingSourceArchive.orNull?.let { archive ->
                artifact(archive) {
                    classifier = "corresponding-source"
                    extension = "tar.gz"
                }
            }
            groupId = "io.github.shusek"
            artifactId = "kmedia-vlc-runtime-desktop"
            version = publicationVersionValue
            pom {
                name.set("KMediaVlc Runtime for Desktop")
                description.set("Optional audited libVLC 4 runtime and stable frame-transport client for desktop.")
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
