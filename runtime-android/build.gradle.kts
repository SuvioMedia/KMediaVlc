// SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

import com.android.build.api.dsl.LibraryExtension
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.util.zip.ZipFile
import org.gradle.api.DefaultTask
import org.gradle.api.file.DirectoryProperty
import org.gradle.api.file.RegularFileProperty
import org.gradle.api.provider.Property
import org.gradle.api.publish.maven.MavenPublication
import org.gradle.api.publish.maven.tasks.PublishToMavenLocal
import org.gradle.api.publish.maven.tasks.PublishToMavenRepository
import org.gradle.api.tasks.Input
import org.gradle.api.tasks.InputFile
import org.gradle.api.tasks.InputDirectory
import org.gradle.api.tasks.Optional
import org.gradle.api.tasks.PathSensitive
import org.gradle.api.tasks.PathSensitivity
import org.gradle.api.tasks.TaskAction
import org.gradle.api.tasks.bundling.Jar

private val VLC_REVISION = "b5536cdea24b313ba9215eacfbd7fa3295d7f3ee"
private val LIBVLCJNI_REVISION = "a8d53a9151d7e4a9a5dfd0a5eb1cd92669afdc21"
private val ANDROID_ABIS = setOf("arm64-v8a", "armeabi-v7a")
private val ANDROID_LIBRARIES = setOf("libkmediavlc_android.so", "libvlc.so")

private fun readClosedProperties(file: File): Map<String, String> {
    require(file.isFile && !Files.isSymbolicLink(file.toPath())) {
        "Android runtime manifest is missing or symbolic."
    }
    val values = linkedMapOf<String, String>()
    file.readLines(StandardCharsets.US_ASCII).forEach { line ->
        val separator = line.indexOf('=')
        require(separator > 0 && separator == line.lastIndexOf('=') && separator < line.lastIndex) {
            "Android runtime manifest contains a malformed line."
        }
        val key = line.substring(0, separator)
        val value = line.substring(separator + 1)
        require(key.matches(Regex("[A-Za-z][A-Za-z0-9]*")) && values.put(key, value) == null) {
            "Android runtime manifest contains an invalid or duplicate key."
        }
    }
    return values
}

private fun expectedManifest(releaseEligible: String): Map<String, String> =
    linkedMapOf(
        "schemaVersion" to "1",
        "vlcRevision" to VLC_REVISION,
        "libvlcjniRevision" to LIBVLCJNI_REVISION,
        "bridgeAbi" to "1",
        "renderEngine" to "ANATIVEWINDOW",
        "minSdk" to "28",
        "abis" to "arm64-v8a,armeabi-v7a",
        "libraries" to "libkmediavlc_android.so,libvlc.so",
        "staticCpp" to "true",
        "releaseEligible" to releaseEligible,
    )

abstract class VerifyVlcAndroidPayload : DefaultTask() {
    @get:Optional
    @get:InputDirectory
    @get:PathSensitive(PathSensitivity.RELATIVE)
    abstract val payload: DirectoryProperty

    @TaskAction
    fun verify() {
        if (!payload.isPresent) return
        val abis = setOf("arm64-v8a", "armeabi-v7a")
        val libraries = setOf("libkmediavlc_android.so", "libvlc.so")
        val root = payload.get().asFile
        require(root.isDirectory && !Files.isSymbolicLink(root.toPath())) {
            "Android native payload must be a real directory."
        }
        val expectedFiles =
            abis.flatMap { abi ->
                libraries.map { library -> "jni/$abi/$library" }
            }.toSet() + "android-runtime.properties"
        val actualFiles =
            root.walkTopDown()
                .filter(File::isFile)
                .map { it.relativeTo(root).invariantSeparatorsPath }
                .toSet()
        require(actualFiles == expectedFiles) {
            "Android native payload inventory differs: $actualFiles"
        }
        expectedFiles.forEach { relative ->
            val file = root.resolve(relative)
            require(file.length() > 0L && !Files.isSymbolicLink(file.toPath())) {
                "Android native payload contains an empty or symbolic file: $relative"
            }
        }
        val manifest = root.resolve("android-runtime.properties")
        val values = linkedMapOf<String, String>()
        manifest.readLines(StandardCharsets.US_ASCII).forEach { line ->
            val separator = line.indexOf('=')
            require(separator > 0 && separator == line.lastIndexOf('=') && separator < line.lastIndex) {
                "Android runtime manifest contains a malformed line."
            }
            val key = line.substring(0, separator)
            val value = line.substring(separator + 1)
            require(key.matches(Regex("[A-Za-z][A-Za-z0-9]*")) && values.put(key, value) == null) {
                "Android runtime manifest contains an invalid or duplicate key."
            }
        }
        val releaseEligible = values["releaseEligible"]
        require(releaseEligible == "true" || releaseEligible == "false") {
            "Android runtime release eligibility must be explicit."
        }
        val expected =
            linkedMapOf(
                "schemaVersion" to "1",
                "vlcRevision" to "b5536cdea24b313ba9215eacfbd7fa3295d7f3ee",
                "libvlcjniRevision" to "a8d53a9151d7e4a9a5dfd0a5eb1cd92669afdc21",
                "bridgeAbi" to "1",
                "renderEngine" to "ANATIVEWINDOW",
                "minSdk" to "28",
                "abis" to "arm64-v8a,armeabi-v7a",
                "libraries" to "libkmediavlc_android.so,libvlc.so",
                "staticCpp" to "true",
                "releaseEligible" to releaseEligible,
            )
        require(values == expected) {
            "Android runtime manifest differs from the pinned contract."
        }
    }
}

abstract class VerifyVlcAndroidAar : DefaultTask() {
    @get:InputFile
    @get:PathSensitive(PathSensitivity.NONE)
    abstract val aar: RegularFileProperty

    @get:Input
    abstract val expectNative: Property<Boolean>

    @TaskAction
    fun verify() {
        ZipFile(aar.get().asFile).use { archive ->
            val abis = setOf("arm64-v8a", "armeabi-v7a")
            val libraries = setOf("libkmediavlc_android.so", "libvlc.so")
            val names = archive.entries().asSequence().map { it.name }.toList()
            require(names.size == names.toSet().size) { "Android AAR contains duplicate entries." }
            val native = names.filter { it.endsWith(".so") }.toSet()
            val expected =
                if (expectNative.get()) {
                    abis.flatMap { abi ->
                        libraries.map { library -> "jni/$abi/$library" }
                    }.toSet()
                } else {
                    emptySet()
                }
            require(native == expected) { "Android AAR native inventory differs: $native" }
            require(names.none {
                "/x86/" in it || "/x86_64/" in it || it.endsWith("libvlcjni.so") ||
                    it.endsWith("libc++_shared.so")
            }) {
                "Android AAR contains a forbidden ABI or wrapper/runtime library."
            }
            val expectedLegal =
                setOf(
                    "assets/kmediavlc/legal/LICENSE",
                    "assets/kmediavlc/legal/NOTICE",
                    "assets/kmediavlc/legal/THIRD_PARTY_NOTICES.md",
                    "assets/kmediavlc/legal/ANDROID.md",
                ) +
                    setOf(
                        "FFmpeg-LICENSE.txt",
                        "FLAC-COPYING-XIPH.txt",
                        "FreeType-FTL.txt",
                        "GSM-COPYRIGHT.txt",
                        "HarfBuzz-COPYING.txt",
                        "ISC-kmediavlc-client-api.txt",
                        "LGPL-2.1.txt",
                        "LGPL-3.0.txt",
                        "OpenJPEG-LICENSE.txt",
                        "Opus-COPYING.txt",
                        "SoXR-LICENCE.txt",
                        "SpeexDSP-COPYING.txt",
                        "libass-COPYING.txt",
                        "libjpeg-turbo-LICENSE.txt",
                        "libogg-COPYING.txt",
                        "libpng-LICENSE.txt",
                        "libssh2-COPYING.txt",
                        "libvorbis-COPYING.txt",
                        "libxml2-Copyright.txt",
                        "zlib-LICENSE.txt",
                    ).map { "assets/kmediavlc/legal/LICENSES/$it" }.toSet()
            val actualLegal =
                names.filter {
                    it.startsWith("assets/kmediavlc/legal/") && !it.endsWith('/')
                }.toSet()
            require(actualLegal == expectedLegal) { "Android AAR legal asset inventory differs." }
            require(
                names.contains("assets/kmediavlc/runtime/android-runtime.properties") ==
                    expectNative.get(),
            ) {
                "Android AAR runtime metadata does not match its native payload."
            }
        }
    }
}

plugins {
    alias(libs.plugins.android.library)
    `maven-publish`
}

val nativePayload =
    providers.gradleProperty("kmediaVlcAndroidNativePayloadDirectory").map(rootProject::file)
val correspondingSourceArchive =
    rootProject.providers.gradleProperty("correspondingSourceArchive").map(rootProject::file)
val generatedAssets = layout.buildDirectory.dir("generated/androidRuntimeAssets")
val publicationVersionValue = project.version.toString()

extensions.configure<LibraryExtension> {
    namespace = "io.github.shusek.kmediavlc.runtime.android"
    compileSdk = 37
    enableKotlin = false
    defaultConfig {
        minSdk = 28
        consumerProguardFiles("consumer-rules.pro")
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    sourceSets.named("main") {
        jniLibs.directories.add(
            (nativePayload.orNull?.resolve("jni")
                ?: layout.buildDirectory.dir("empty-android-jni").get().asFile).absolutePath,
        )
        assets.directories.add(generatedAssets.get().asFile.absolutePath)
    }
    publishing {
        singleVariant("release") { withSourcesJar() }
    }
}

dependencies {
    testImplementation(platform(libs.junit.bom))
    testImplementation(libs.junit.jupiter)
    testRuntimeOnly(libs.junit.platform.launcher)
}

tasks.withType<Test>().configureEach { useJUnitPlatform() }

val prepareAndroidAssets =
    tasks.register<Sync>("prepareAndroidAssets") {
        into(generatedAssets.map { it.dir("kmediavlc") })
        from(rootProject.layout.projectDirectory.file("LICENSE")) { into("legal") }
        from(rootProject.layout.projectDirectory.file("NOTICE")) { into("legal") }
        from(rootProject.layout.projectDirectory.file("THIRD_PARTY_NOTICES.md")) { into("legal") }
        from(rootProject.layout.projectDirectory.file("docs/ANDROID.md")) {
            into("legal")
            rename { "ANDROID.md" }
        }
        from(rootProject.layout.projectDirectory.dir("LICENSES")) { into("legal/LICENSES") }
        nativePayload.orNull?.let {
            from(it.resolve("android-runtime.properties")) { into("runtime") }
        }
    }

val verifyNativePayload =
    tasks.register<VerifyVlcAndroidPayload>("verifyNativePayload") {
        payload.set(layout.dir(nativePayload))
    }

tasks.named("preBuild") { dependsOn(prepareAndroidAssets, verifyNativePayload) }

val verifyAndroidAar =
    tasks.register<VerifyVlcAndroidAar>("verifyAndroidAar") {
        dependsOn("bundleReleaseAar")
        aar.set(layout.buildDirectory.file("outputs/aar/runtime-android-release.aar"))
        expectNative.set(nativePayload.isPresent)
    }

val androidJavadocJar =
    tasks.register<Jar>("androidJavadocJar") {
        archiveClassifier.set("javadoc")
        isPreserveFileTimestamps = false
        isReproducibleFileOrder = true
        from(layout.projectDirectory.dir("src/javadoc"))
    }

tasks.named("check") { dependsOn(verifyNativePayload, verifyAndroidAar) }

fun requirePublicationPayload() {
    require(nativePayload.isPresent) {
        "Publishing requires -PkmediaVlcAndroidNativePayloadDirectory."
    }
    val values = readClosedProperties(nativePayload.get().resolve("android-runtime.properties"))
    require(values == expectedManifest("true")) {
        "Publishing requires a release-eligible Android payload with the exact pinned manifest."
    }
    require(correspondingSourceArchive.isPresent) {
        "Publishing requires -PcorrespondingSourceArchive."
    }
    require(!publicationVersionValue.contains("SNAPSHOT", ignoreCase = true)) {
        "Publishing requires an immutable non-SNAPSHOT version."
    }
}

tasks.withType<PublishToMavenRepository>().configureEach {
    dependsOn(verifyNativePayload, verifyAndroidAar)
    doFirst { requirePublicationPayload() }
}
tasks.withType<PublishToMavenLocal>().configureEach {
    dependsOn(verifyNativePayload, verifyAndroidAar)
    doFirst { requirePublicationPayload() }
}

afterEvaluate {
    publishing {
        publications {
            create<MavenPublication>("release") {
                from(components["release"])
                artifact(androidJavadocJar)
                correspondingSourceArchive.orNull?.let { archive ->
                    artifact(archive) {
                        classifier = "corresponding-source"
                        extension = "tar.gz"
                    }
                }
                groupId = "io.github.shusek"
                artifactId = "kmedia-vlc-runtime-android"
                version = publicationVersionValue
                pom {
                    name.set("KMediaVlc Runtime for Android")
                    description.set("Bundled, source-audited libVLC 4 runtime with a narrow ANativeWindow JNI API.")
                    url.set("https://github.com/SuvioMedia/KMediaVlc")
                    licenses {
                        license {
                            name.set("KMediaVlc Proprietary License 1.0")
                            url.set("https://github.com/SuvioMedia/KMediaVlc/blob/main/LICENSE")
                            distribution.set("repo")
                        }
                        license {
                            name.set("GNU Lesser General Public License, version 2.1 or later")
                            url.set("https://www.gnu.org/licenses/old-licenses/lgpl-2.1.html")
                            distribution.set("repo")
                        }
                    }
                    developers { developer { id.set("Shusek"); name.set("Shusek") } }
                    scm { url.set("https://github.com/SuvioMedia/KMediaVlc") }
                }
            }
        }
    }
}

publishing.repositories {
    rootProject.providers.gradleProperty("releaseRepository").orNull?.let { repositoryPath ->
        maven {
            name = "release"
            url = uri(repositoryPath)
        }
    }
}
