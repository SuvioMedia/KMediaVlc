// SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

import org.gradle.api.publish.maven.MavenPublication
import org.gradle.api.publish.maven.tasks.PublishToMavenLocal
import org.gradle.api.publish.maven.tasks.PublishToMavenRepository
import org.gradle.api.tasks.Exec
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

tasks.test {
    useJUnitPlatform()
    providers.gradleProperty("kmediaVlcNativeBridgePath").orNull?.let { bridgePath ->
        systemProperty("kmediavlc.test.nativeBridge", bridgePath)
    }
    providers.gradleProperty("kmediaVlcTestLibVlcPath").orNull?.let { libVlcPath ->
        systemProperty("kmediavlc.test.libVlc", libVlcPath)
    }
    providers.gradleProperty("kmediaVlcTestPluginDirectory").orNull?.let { pluginDirectory ->
        systemProperty("kmediavlc.test.plugins", pluginDirectory)
    }
    providers.gradleProperty("kmediaVlcTestHdrMedia").orNull?.let { hdrMedia ->
        systemProperty("kmediavlc.test.hdrMedia", hdrMedia)
    }
    providers.gradleProperty("kmediaVlcTestHttpsHdrMedia").orNull?.let { httpsHdrMedia ->
        systemProperty("kmediavlc.test.httpsHdrMedia", httpsHdrMedia)
    }
    if (providers.gradleProperty("kmediaVlcTestBundledRuntime").orNull == "true") {
        systemProperty("kmediavlc.test.bundledRuntime", "true")
    }
    if (providers.gradleProperty("kmediaVlcDebugCallbacks").orNull == "true") {
        environment("KMEDIAVLC_DEBUG_CALLBACKS", "1")
    }
}

tasks.withType<Jar>().configureEach {
    isPreserveFileTimestamps = false
    isReproducibleFileOrder = true
}

val nativeStagingDirectory = providers.gradleProperty("kmediaVlcNativeStagingDirectory").map(project::file)
val nativeInventory = providers.gradleProperty("kmediaVlcNativeInventory").map(project::file)
val nativeTarget = providers.gradleProperty("kmediaVlcNativeTarget")
val nativeSourceOffer = providers.gradleProperty("kmediaVlcSourceOffer")
val nativePackagingInputs =
    listOf(
        nativeStagingDirectory.isPresent,
        nativeInventory.isPresent,
        nativeTarget.isPresent,
        nativeSourceOffer.isPresent,
    )
val nativePackagingConfigured = nativePackagingInputs.all { it }
require(nativePackagingInputs.none { it } || nativePackagingConfigured) {
    "Native packaging requires staging directory, inventory, target, and source offer together."
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
            inputs.dir(nativeStagingDirectory)
            inputs.file(nativeInventory)
            inputs.file(rootProject.layout.projectDirectory.file("compliance/policy/release-policy.json"))
            inputs.file(rootProject.layout.projectDirectory.file("scripts/package_native_runtime.py"))
            inputs.property("nativeTarget", nativeTarget)
            inputs.property("nativeSourceOffer", nativeSourceOffer)
            inputs.property("recipeRevision", recipeRevision)
            outputs.dir(nativePayloadDirectory)
        }
        doFirst {
            require(nativePackagingConfigured) { "Native release packaging inputs are incomplete." }
            require(recipeRevision.isPresent) {
                "Native release packaging requires -PrecipeRevision with the immutable build commit."
            }
            project.delete(nativePayloadDirectory.get().asFile)
            commandLine(
                rootProject.providers
                    .gradleProperty("kmediaVlcPythonExecutable")
                    .orElse(rootProject.providers.environmentVariable("KMEDIAVLC_PYTHON"))
                    .orElse(if (System.getProperty("os.name").startsWith("Windows")) "python" else "python3")
                    .get(),
                rootProject.layout.projectDirectory.file("scripts/package_native_runtime.py").asFile.absolutePath,
                "--root",
                rootProject.layout.projectDirectory.asFile.absolutePath,
                "--staging",
                nativeStagingDirectory.get().absolutePath,
                "--inventory",
                nativeInventory.get().absolutePath,
                "--target",
                nativeTarget.get(),
                "--source-offer",
                nativeSourceOffer.get(),
                "--recipe-revision",
                recipeRevision.get(),
                "--output",
                nativePayloadDirectory.get().asFile.absolutePath,
            )
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
            }
        }
    }

val requireNativePayloadForPublication =
    tasks.register("requireNativePayloadForPublication") {
        group = "publishing"
        dependsOn(verifyRuntimeJar, validatePublicationVersion)
        doLast {
            require(nativePackagingConfigured) {
                "Publishing requires the complete audited native staging/inventory input set."
            }
            require(recipeRevision.isPresent) {
                "Publishing requires -PrecipeRevision with the immutable build commit."
            }
            require(recipeRevision.get().matches(Regex("[0-9a-f]{40}"))) {
                "recipeRevision must be an exact lowercase forty-character Git commit."
            }
            require(recipeRevision.get() == checkoutRevision.get()) {
                "recipeRevision must match the checked-out KMediaVlc commit."
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
                        name.set("KMediaVlc Proprietary License 1.0")
                        url.set("https://github.com/SuvioMedia/KMediaVlc/blob/main/LICENSE")
                        distribution.set("repo")
                    }
                    license {
                        name.set("GNU Lesser General Public License, version 2.1 or later (libVLC)")
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
