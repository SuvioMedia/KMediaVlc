// SPDX-License-Identifier: LGPL-2.1-or-later

import com.android.build.api.dsl.LibraryExtension
import groovy.json.JsonSlurper
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.security.MessageDigest
import java.util.zip.ZipFile
import org.gradle.api.DefaultTask
import org.gradle.api.file.DirectoryProperty
import org.gradle.api.file.RegularFileProperty
import org.gradle.api.provider.Property
import org.gradle.api.publish.maven.MavenPublication
import org.gradle.api.publish.maven.tasks.PublishToMavenLocal
import org.gradle.api.publish.maven.tasks.PublishToMavenRepository
import org.gradle.api.tasks.Exec
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
private object AndroidLegalEvidence {
private const val VLC_REVISION = "b5536cdea24b313ba9215eacfbd7fa3295d7f3ee"
private const val LLVM_PROJECT_REVISION = "386af4a5c64ab75eaee2448dc38f2e34a40bfed0"
private const val LLVM_ANDROID_REVISION = "1dab3288f660d43a6cb2479107e2b54b3ab0a2a1"
private const val NDK_SOURCE_CANDIDATE_STATUS =
    "exact-source-revisions-recorded-source-package-pending"
private val REVIEW_STATES =
    setOf("candidate-linked-member-review-pending", "approved")
private val CANDIDATE_LICENSES =
    setOf(
        "Apache-2.0",
        "Apache-2.0 WITH LLVM-exception",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "BSL-1.0",
        "CC0-1.0",
        "FTL",
        "IJG",
        "ISC",
        "LGPL-2.0-or-later",
        "LGPL-2.1-only",
        "LGPL-2.1-or-later",
        "Libpng-2.0",
        "LicenseRef-Public-Domain",
        "MIT",
        "TU-Berlin-1.0",
        "Unicode-DFS-2016",
        "Zlib",
    )

data class Bundle(
    val reviewStatus: String,
    val effectiveLicenseSpdx: String?,
    val ndkSourceStatus: String,
    val files: Set<String>,
)

private fun sha256(file: File): String {
    val digest = MessageDigest.getInstance("SHA-256")
    file.inputStream().buffered().use { source ->
        val buffer = ByteArray(1024 * 1024)
        while (true) {
            val count = source.read(buffer)
            if (count < 0) break
            digest.update(buffer, 0, count)
        }
    }
    return digest.digest().joinToString("") { "%02x".format(it.toInt() and 0xff) }
}

private fun safeLegalPath(value: Any?): String {
    require(value is String && value == value.replace('\\', '/')) {
        "Android legal evidence contains a non-canonical path."
    }
    val parts = value.split('/')
    require(
        parts.isNotEmpty() &&
            parts.none { it.isEmpty() || it == "." || it == ".." } &&
            parts.all { it.matches(Regex("[A-Za-z0-9][A-Za-z0-9._+-]*")) },
    ) {
        "Android legal evidence contains an unsafe path: $value"
    }
    return value
}

fun read(payloadRoot: File, staticPolicy: File): Bundle {
    val legalRoot = payloadRoot.resolve("legal")
    val manifestFile = legalRoot.resolve("android-static-legal.json")
    require(
        legalRoot.isDirectory &&
            !Files.isSymbolicLink(legalRoot.toPath()) &&
            manifestFile.isFile &&
            !Files.isSymbolicLink(manifestFile.toPath()),
    ) {
        "Android native payload legal evidence is missing or symbolic."
    }
    legalRoot.walkTopDown().forEach { path ->
        require(!Files.isSymbolicLink(path.toPath())) {
            "Android native payload legal evidence contains a symbolic path."
        }
    }
    val manifest = JsonSlurper().parse(manifestFile) as? Map<*, *>
        ?: error("Android legal evidence manifest root must be an object.")
    require((manifest["schemaVersion"] as? Number)?.toInt() == 1) {
        "Android legal evidence schema is unsupported."
    }
    require(manifest["vlcRevision"] == VLC_REVISION && manifest["ndkRevision"] == "29.0.14206865") {
        "Android legal evidence identity differs from the native runtime."
    }
    val reviewStatus = requireNotNull(manifest["reviewStatus"] as? String) {
        "Android legal evidence review state is missing."
    }
    require(reviewStatus in REVIEW_STATES) {
        "Android legal evidence review state is invalid."
    }
    val effectiveLicense = manifest["effectiveLicenseSpdx"] as? String
    if (reviewStatus == "approved") {
        require(!effectiveLicense.isNullOrBlank()) {
            "Approved Android legal evidence requires an effective SPDX expression."
        }
    } else {
        require(manifest["effectiveLicenseSpdx"] == null) {
            "Candidate Android legal evidence must not declare an effective license."
        }
    }
    val candidateLicenses = manifest["candidateLicenseInventorySpdx"] as? List<*>
    require(
        candidateLicenses != null &&
            candidateLicenses == candidateLicenses.filterIsInstance<String>().distinct().sorted() &&
            candidateLicenses.toSet() == CANDIDATE_LICENSES,
    ) {
        "Android legal evidence candidate SPDX inventory is incomplete."
    }
    val policy = manifest["staticComponentPolicy"] as? Map<*, *>
    require(
        policy?.get("path") == "compliance/policy/android-static-components.json" &&
            policy["sha256"] == sha256(staticPolicy),
    ) {
        "Android legal evidence is not bound to the current static component policy."
    }
    val audits = manifest["abiAudits"] as? List<*>
    require(audits?.size == 2) { "Android legal evidence must bind both ABI audits." }
    val targetAbis =
        mapOf(
            "android-arm64-v8a" to "arm64-v8a",
            "android-armeabi-v7a" to "armeabi-v7a",
        )
    val auditTargets =
        audits.map { entry ->
            val value = entry as? Map<*, *>
                ?: error("Android legal evidence ABI audit entry is invalid.")
            val target = value["target"] as? String
            val abi = targetAbis[target]
            val libvlcHash = value["libvlcSha256"] as? String
            val packagedLibvlc = payloadRoot.resolve("jni/$abi/libvlc.so")
            require(
                (value["reportSha256"] as? String)?.matches(Regex("[0-9a-f]{64}")) == true &&
                    libvlcHash?.matches(Regex("[0-9a-f]{64}")) == true &&
                    abi != null &&
                    packagedLibvlc.isFile &&
                    !Files.isSymbolicLink(packagedLibvlc.toPath()) &&
                    sha256(packagedLibvlc) == libvlcHash,
            ) {
                "Android legal evidence ABI audit does not bind the packaged libvlc.so."
            }
            target
        }.toSet()
    require(auditTargets == targetAbis.keys) {
        "Android legal evidence ABI audit targets are incomplete."
    }

    val topFiles = manifest["files"] as? List<*>
    require(topFiles?.size == 88) { "Android legal evidence must contain exactly 88 files." }
    val fileEntries = linkedMapOf<String, Map<*, *>>()
    topFiles.forEach { raw ->
        val entry = raw as? Map<*, *>
            ?: error("Android legal evidence file entry is invalid.")
        val path = safeLegalPath(entry["path"])
        require(fileEntries.put(path, entry) == null) {
            "Android legal evidence contains a duplicate file: $path"
        }
        val expectedHash = entry["sha256"] as? String
        val expectedSize = (entry["size"] as? Number)?.toLong()
        val file = legalRoot.resolve(path)
        require(
            file.isFile &&
                !Files.isSymbolicLink(file.toPath()) &&
                expectedHash?.matches(Regex("[0-9a-f]{64}")) == true &&
                expectedSize != null && expectedSize > 0L &&
                file.length() == expectedSize && sha256(file) == expectedHash,
        ) {
            "Android legal evidence file differs from its manifest: $path"
        }
    }
    require(
        fileEntries.keys.count { it.startsWith("contrib/") } == 83 &&
            fileEntries.keys.count { it.startsWith("ndk/") } == 5,
    ) {
        "Android legal evidence contrib/NDK split is invalid."
    }

    val components = manifest["components"] as? List<*>
    require(components?.size == 55) { "Android legal evidence component closure is incomplete." }
    val componentIds = mutableListOf<String>()
    val componentFiles = mutableSetOf<String>()
    val ndkSourceStatuses = mutableListOf<String>()
    components.forEach { raw ->
        val component = raw as? Map<*, *>
            ?: error("Android legal evidence component entry is invalid.")
        val id = component["id"] as? String
        require(id?.matches(Regex("[a-z0-9][a-z0-9-]+")) == true) {
            "Android legal evidence component identifier is unsafe."
        }
        val kind = component["kind"]
        require(kind in setOf("VLC_CONTRIB", "NDK_TOOLCHAIN")) {
            "Android legal evidence component kind is invalid: $id"
        }
        require((component["version"] as? String)?.isNotBlank() == true) {
            "Android legal evidence component version is missing: $id"
        }
        val componentReview = component["licenseReviewStatus"]
        require(
            componentReview ==
                if (reviewStatus == "approved") "approved" else "pending-linked-member-review",
        ) {
            "Android legal evidence component review state is invalid: $id"
        }
        val licenses = component["candidateLicenseSpdx"] as? List<*>
        require(
            licenses != null &&
                licenses == licenses.filterIsInstance<String>().distinct().sorted() &&
                licenses.all { it in CANDIDATE_LICENSES },
        ) {
            "Android legal evidence component candidate SPDX set is invalid: $id"
        }
        val sourceArchives = component["sourceArchives"] as? List<*>
        val sourceInputs = component["sourceInputs"] as? List<*>
        val sourceStatus = component["sourceStatus"]
        if (kind == "VLC_CONTRIB") {
            require(
                sourceStatus == "source-archive-hashes-recorded" &&
                    !sourceArchives.isNullOrEmpty() &&
                    sourceInputs?.isEmpty() == true &&
                    component["binaryProvenance"] == null,
            ) {
                "Android legal evidence contrib source hashes are incomplete: $id"
            }
            sourceArchives.forEach { source ->
                val entry = source as? Map<*, *>
                    ?: error("Android legal evidence source archive entry is invalid.")
                require(
                    (entry["path"] as? String)?.matches(
                        Regex("vlc-contrib-tarballs/[A-Za-z0-9][A-Za-z0-9.+_-]+\\.tar\\.(gz|xz|bz2)"),
                    ) == true &&
                        (entry["sha256"] as? String)?.matches(Regex("[0-9a-f]{64}")) == true &&
                        ((entry["size"] as? Number)?.toLong() ?: 0L) > 0L,
                ) {
                    "Android legal evidence source archive hash is invalid: $id"
                }
            }
        } else {
            require(
                id == "android-ndk-llvm-runtime" &&
                sourceArchives?.isEmpty() == true &&
                    sourceStatus in
                    setOf(NDK_SOURCE_CANDIDATE_STATUS, "corresponding-source-mapped") &&
                    sourceInputs?.size == 2,
            ) {
                "Android legal evidence NDK source state is invalid."
            }
            val bySourceId =
                sourceInputs.associate { rawSource ->
                    val source = rawSource as? Map<*, *>
                        ?: error("Android NDK source input entry is invalid.")
                    val sourceId = source["id"] as? String
                        ?: error("Android NDK source input identifier is missing.")
                    sourceId to source
                }
            require(bySourceId.keys == setOf("llvm-android-build", "llvm-project")) {
                "Android NDK source input closure is incomplete."
            }
            val llvmAndroid = bySourceId.getValue("llvm-android-build")
            require(
                llvmAndroid["repository"] ==
                    "https://android.googlesource.com/toolchain/llvm_android" &&
                    llvmAndroid["revision"] == LLVM_ANDROID_REVISION &&
                    llvmAndroid["tree"] == "9cf89bb8f12fb9e993e81d2ee2d43f2bc8819d53" &&
                    llvmAndroid["role"] == "android-runtime-build-and-patch-set" &&
                    llvmAndroid["requiredPaths"] ==
                    listOf(
                        "do_build.py",
                        "patches",
                        "src/llvm_android/android_version.py",
                        "src/llvm_android/builders.py",
                    ),
            ) {
                "Android NDK llvm_android source identity differs from r29."
            }
            val llvmProject = bySourceId.getValue("llvm-project")
            require(
                llvmProject["repository"] ==
                    "https://android.googlesource.com/toolchain/llvm-project" &&
                    llvmProject["revision"] == LLVM_PROJECT_REVISION &&
                    llvmProject["tree"] == "a49e40b73bcc972355bbf00df0d85d00312a625f" &&
                    llvmProject["role"] == "linked-runtime-source" &&
                    llvmProject["requiredPaths"] ==
                    listOf(
                        "compiler-rt/lib/builtins",
                        "libcxx",
                        "libcxxabi",
                        "libunwind",
                        "runtimes",
                    ),
            ) {
                "Android NDK LLVM source identity differs from r29."
            }
            val provenance = component["binaryProvenance"] as? Map<*, *>
                ?: error("Android NDK binary provenance is missing.")
            val prebuilt = provenance["prebuilt"] as? Map<*, *>
                ?: error("Android NDK host prebuilt provenance is missing.")
            val hostTag = prebuilt["hostTag"] as? String
            val expectedPrebuilt =
                when (hostTag) {
                    "darwin-x86_64" ->
                        listOf(
                            "https://android.googlesource.com/platform/prebuilts/clang/host/darwin-x86",
                            "c547cdbfbec71e85920c1f0976e18defc01a0b5b",
                            "2ede290b28d234595fcc23207c633961690c57ba",
                        )
                    "linux-x86_64" ->
                        listOf(
                            "https://android.googlesource.com/platform/prebuilts/clang/host/linux-x86",
                            "be61f23178d3459a558b45dd0df4304b0fda6b26",
                            "568b941cf0c249b9c2a1f853e94a29f0e6291c59",
                        )
                    else -> emptyList()
                }
            require(
                provenance["releaseName"] == "r29" &&
                    provenance["clangVersion"] == "21.0.0" &&
                    provenance["clangRevision"] == "r563880c" &&
                    provenance["ndkRepository"] ==
                    "https://android.googlesource.com/platform/ndk" &&
                    provenance["ndkTag"] == "ndk-r29" &&
                    provenance["ndkTagObject"] ==
                    "5199c56421d79df5099aad8e32e32c101ff85cca" &&
                    provenance["ndkCommit"] ==
                    "196e0661200bad5361340700fea67be12e1f1684" &&
                    provenance["manifestRepository"] ==
                    "https://android.googlesource.com/platform/manifest" &&
                    provenance["manifestTagObject"] ==
                    "5d4df6d77b33dc6d31576a66a8ff283c8825493f" &&
                    provenance["manifestCommit"] ==
                    "82eb8adcaafe02dce4e462db2379fad3ea0b54d8" &&
                    expectedPrebuilt.size == 3 &&
                    prebuilt["repository"] == expectedPrebuilt[0] &&
                    prebuilt["tagObject"] == expectedPrebuilt[1] &&
                    prebuilt["commit"] == expectedPrebuilt[2],
            ) {
                "Android NDK release/prebuilt provenance differs from r29."
            }
            ndkSourceStatuses.add(sourceStatus as String)
        }
        val files = component["files"] as? List<*>
        require(!files.isNullOrEmpty()) { "Android legal evidence component has no files: $id" }
        files.forEach { file ->
            val entry = file as? Map<*, *>
                ?: error("Android legal evidence component file entry is invalid.")
            val path = safeLegalPath(entry["path"])
            require(entry == fileEntries[path] && componentFiles.add(path)) {
                "Android legal evidence component file map is inconsistent: $path"
            }
        }
        componentIds.add(id)
    }
    require(
        componentIds == componentIds.distinct().sorted() &&
            componentFiles == fileEntries.keys &&
            ndkSourceStatuses.size == 1,
    ) {
        "Android legal evidence component/file closure is not canonical."
    }
    val actualFiles =
        legalRoot.walkTopDown()
            .filter(File::isFile)
            .map { it.relativeTo(legalRoot).invariantSeparatorsPath }
            .toSet()
    require(actualFiles == fileEntries.keys + "android-static-legal.json") {
        "Android legal evidence directory contains missing or extra files."
    }
    return Bundle(reviewStatus, effectiveLicense, ndkSourceStatuses.single(), actualFiles)
}
}

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

    @get:InputFile
    @get:PathSensitive(PathSensitivity.NONE)
    abstract val staticPolicy: RegularFileProperty

    @TaskAction
    fun verify() {
        if (!payload.isPresent) return
        val abis = setOf("arm64-v8a", "armeabi-v7a")
        val libraries = setOf("libkmediavlc_android.so", "libvlc.so")
        val root = payload.get().asFile
        require(root.isDirectory && !Files.isSymbolicLink(root.toPath())) {
            "Android native payload must be a real directory."
        }
        val legalBundle = AndroidLegalEvidence.read(root, staticPolicy.get().asFile)
        val expectedFiles =
            abis.flatMap { abi ->
                libraries.map { library -> "jni/$abi/$library" }
            }.toSet() +
                "android-runtime.properties" +
                legalBundle.files.map { "legal/$it" }
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

    @get:Optional
    @get:InputDirectory
    @get:PathSensitive(PathSensitivity.RELATIVE)
    abstract val payload: DirectoryProperty

    @get:InputFile
    @get:PathSensitive(PathSensitivity.NONE)
    abstract val staticPolicy: RegularFileProperty

    @get:InputDirectory
    @get:PathSensitive(PathSensitivity.RELATIVE)
    abstract val legalDirectory: DirectoryProperty

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
            val licenseFiles =
                requireNotNull(legalDirectory.get().asFile.listFiles()) {
                    "Android AAR license directory cannot be listed."
                }
            require(
                licenseFiles.isNotEmpty() &&
                    licenseFiles.all { it.isFile && !Files.isSymbolicLink(it.toPath()) },
            ) {
                "Android AAR licenses must be a non-empty flat regular-file inventory."
            }
            val expectedLicenseAssets =
                licenseFiles.map { "assets/kmediavlc/legal/LICENSES/${it.name}" }.toSet()
            val expectedLegal =
                setOf(
                    "assets/kmediavlc/legal/LICENSE",
                    "assets/kmediavlc/legal/NOTICE",
                    "assets/kmediavlc/legal/THIRD_PARTY_NOTICES.md",
                    "assets/kmediavlc/legal/ANDROID.md",
                ) +
                    expectedLicenseAssets +
                    if (expectNative.get()) {
                        AndroidLegalEvidence.read(payload.get().asFile, staticPolicy.get().asFile)
                            .files
                            .map { "assets/kmediavlc/legal/ANDROID_STATIC/$it" }
                            .toSet()
                    } else {
                        emptySet()
                    }
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
val androidCorrespondingSourceArchive =
    rootProject.providers
        .gradleProperty("kmediaVlcAndroidCorrespondingSourceArchive")
        .map(rootProject::file)
val androidVlcSourceDirectory =
    rootProject.providers.gradleProperty("kmediaVlcAndroidVlcSourceDirectory").map(rootProject::file)
val androidLibvlcjniSourceDirectory =
    rootProject.providers
        .gradleProperty("kmediaVlcAndroidLibvlcjniSourceDirectory")
        .map(rootProject::file)
val androidContribTarballsDirectory =
    rootProject.providers
        .gradleProperty("kmediaVlcAndroidContribTarballsDirectory")
        .map(rootProject::file)
val androidArm64LinkAudit =
    rootProject.providers.gradleProperty("kmediaVlcAndroidArm64LinkAudit").map(rootProject::file)
val androidArmv7LinkAudit =
    rootProject.providers.gradleProperty("kmediaVlcAndroidArmv7LinkAudit").map(rootProject::file)
val androidNdkSourceArchive =
    rootProject.providers.gradleProperty("kmediaVlcAndroidNdkSourceArchive").map(rootProject::file)
val androidLlvmProjectSourceDirectory =
    rootProject.providers
        .gradleProperty("kmediaVlcAndroidLlvmProjectSourceDirectory")
        .map(rootProject::file)
val androidLlvmAndroidSourceDirectory =
    rootProject.providers
        .gradleProperty("kmediaVlcAndroidLlvmAndroidSourceDirectory")
        .map(rootProject::file)
val recipeRevision = rootProject.providers.gradleProperty("recipeRevision")
val androidNdkSourceVerificationInputs =
    listOf(
        androidNdkSourceArchive.isPresent,
        androidLlvmProjectSourceDirectory.isPresent,
        androidLlvmAndroidSourceDirectory.isPresent,
        recipeRevision.isPresent,
    )
val androidNdkSourceVerificationConfigured = androidNdkSourceVerificationInputs.all { it }
require(
    androidNdkSourceVerificationInputs.none { it } || androidNdkSourceVerificationConfigured,
) {
    "Android NDK source verification requires its archive, both exact Git checkouts, and recipeRevision together."
}
val androidCorrespondingSourceVerificationInputs =
    listOf(
        androidCorrespondingSourceArchive.isPresent,
        androidVlcSourceDirectory.isPresent,
        androidLibvlcjniSourceDirectory.isPresent,
        androidContribTarballsDirectory.isPresent,
        androidArm64LinkAudit.isPresent,
        androidArmv7LinkAudit.isPresent,
    )
val androidCorrespondingSourceVerificationConfigured =
    androidCorrespondingSourceVerificationInputs.all { it }
require(
    androidCorrespondingSourceVerificationInputs.none { it } ||
        androidCorrespondingSourceVerificationConfigured,
) {
    "Android corresponding-source verification requires its archive, both source checkouts, contrib tarballs, and both ABI audits together."
}
require(
    !androidCorrespondingSourceVerificationConfigured ||
        (nativePayload.isPresent && androidNdkSourceVerificationConfigured),
) {
    "Android corresponding-source verification also requires the native payload and complete NDK source verification inputs."
}
val checkoutRevision =
    rootProject.providers.exec {
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
val pythonExecutable =
    rootProject.providers
        .gradleProperty("kmediaVlcPythonExecutable")
        .orElse(if (System.getProperty("os.name").startsWith("Windows")) "python" else "python3")
val generatedAssets = layout.buildDirectory.dir("generated/androidRuntimeAssets")
val publicationVersionValue = project.version.toString()

extensions.configure<LibraryExtension> {
    namespace = "io.github.shusek.kmediavlc.runtime.android"
    compileSdk = 36
    enableKotlin = false
    defaultConfig {
        minSdk = 28
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
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
    androidTestImplementation(libs.androidx.test.core)
    androidTestImplementation(libs.androidx.test.ext.junit)
    androidTestImplementation(libs.androidx.test.runner)
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
            from(it.resolve("legal")) { into("legal/ANDROID_STATIC") }
        }
    }

val verifyNativePayload =
    tasks.register<VerifyVlcAndroidPayload>("verifyNativePayload") {
        payload.set(layout.dir(nativePayload))
        staticPolicy.set(
            rootProject.layout.projectDirectory.file(
                "compliance/policy/android-static-components.json",
            ),
        )
    }

tasks.named("preBuild") { dependsOn(prepareAndroidAssets, verifyNativePayload) }

val verifyAndroidAar =
    tasks.register<VerifyVlcAndroidAar>("verifyAndroidAar") {
        dependsOn("bundleReleaseAar")
        aar.set(layout.buildDirectory.file("outputs/aar/runtime-android-release.aar"))
        expectNative.set(nativePayload.isPresent)
        payload.set(layout.dir(nativePayload))
        staticPolicy.set(
            rootProject.layout.projectDirectory.file(
                "compliance/policy/android-static-components.json",
            ),
        )
        legalDirectory.set(rootProject.layout.projectDirectory.dir("LICENSES"))
    }

val androidJavadocJar =
    tasks.register<Jar>("androidJavadocJar") {
        archiveClassifier.set("javadoc")
        isPreserveFileTimestamps = false
        isReproducibleFileOrder = true
        from(layout.projectDirectory.dir("src/javadoc"))
    }

val verifyAndroidNdkSourceArchive =
    tasks.register<Exec>("verifyAndroidNdkSourceArchive") {
        group = "verification"
        description = "Verifies the Android NDK runtime source archive against both exact Git trees."
        onlyIf { androidNdkSourceVerificationConfigured }
        if (androidNdkSourceVerificationConfigured) {
            inputs.file(androidNdkSourceArchive)
            inputs.dir(androidLlvmProjectSourceDirectory)
            inputs.dir(androidLlvmAndroidSourceDirectory)
            inputs.file(
                rootProject.layout.projectDirectory.file(
                    "compliance/policy/android-static-components.json",
                ),
            )
            inputs.file(
                rootProject.layout.projectDirectory.file(
                    "scripts/verify_android_ndk_source_archive.py",
                ),
            )
            inputs.property("publicationVersion", publicationVersionValue)
            inputs.property("recipeRevision", recipeRevision)
        }
        doFirst {
            require(androidNdkSourceVerificationConfigured) {
                "Android NDK source verification inputs are incomplete."
            }
            commandLine(
                pythonExecutable.get(),
                rootProject.file("scripts/verify_android_ndk_source_archive.py").absolutePath,
                "--root",
                rootProject.projectDir.absolutePath,
                "--archive",
                androidNdkSourceArchive.get().absolutePath,
                "--llvm-project",
                androidLlvmProjectSourceDirectory.get().absolutePath,
                "--llvm-android",
                androidLlvmAndroidSourceDirectory.get().absolutePath,
                "--version",
                publicationVersionValue,
                "--tested-commit",
                recipeRevision.get(),
            )
        }
    }

val verifyAndroidCorrespondingSourceArchive =
    tasks.register<Exec>("verifyAndroidCorrespondingSourceArchive") {
        group = "verification"
        description =
            "Verifies complete Android corresponding source against Git, contrib, NDK, and ABI evidence."
        dependsOn(verifyNativePayload, verifyAndroidNdkSourceArchive)
        onlyIf { androidCorrespondingSourceVerificationConfigured }
        doNotTrackState(
            "The verifier performs exact Git-object, archive, and SHA-256 checks on external inputs.",
        )
        doFirst {
            require(androidCorrespondingSourceVerificationConfigured) {
                "Android corresponding-source verification inputs are incomplete."
            }
            commandLine(
                pythonExecutable.get(),
                rootProject.file(
                    "scripts/verify_android_corresponding_source_archive.py",
                ).absolutePath,
                "--root",
                rootProject.projectDir.absolutePath,
                "--archive",
                androidCorrespondingSourceArchive.get().absolutePath,
                "--vlc",
                androidVlcSourceDirectory.get().absolutePath,
                "--libvlcjni",
                androidLibvlcjniSourceDirectory.get().absolutePath,
                "--contrib-tarballs",
                androidContribTarballsDirectory.get().absolutePath,
                "--ndk-source-archive",
                androidNdkSourceArchive.get().absolutePath,
                "--llvm-project",
                androidLlvmProjectSourceDirectory.get().absolutePath,
                "--llvm-android",
                androidLlvmAndroidSourceDirectory.get().absolutePath,
                "--legal-manifest",
                nativePayload.get().resolve("legal/android-static-legal.json").absolutePath,
                "--arm64-audit",
                androidArm64LinkAudit.get().absolutePath,
                "--armv7-audit",
                androidArmv7LinkAudit.get().absolutePath,
                "--version",
                publicationVersionValue,
                "--tested-commit",
                recipeRevision.get(),
            )
        }
    }

tasks.named("check") {
    dependsOn(
        verifyNativePayload,
        verifyAndroidAar,
        verifyAndroidNdkSourceArchive,
        verifyAndroidCorrespondingSourceArchive,
    )
}

fun requirePublicationPayload() {
    require(nativePayload.isPresent) {
        "Publishing requires -PkmediaVlcAndroidNativePayloadDirectory."
    }
    val values = readClosedProperties(nativePayload.get().resolve("android-runtime.properties"))
    require(values == expectedManifest("true")) {
        "Publishing requires a release-eligible Android payload with the exact pinned manifest."
    }
    val legalBundle =
        AndroidLegalEvidence.read(
            nativePayload.get(),
            rootProject.file("compliance/policy/android-static-components.json"),
        )
    require(legalBundle.reviewStatus == "approved" && !legalBundle.effectiveLicenseSpdx.isNullOrBlank()) {
        "Publishing requires approved hash-bound Android legal evidence."
    }
    require(legalBundle.ndkSourceStatus == "corresponding-source-mapped") {
        "Publishing requires the NDK runtime source package to match its recorded revisions."
    }
    require(androidNdkSourceVerificationConfigured) {
        "Publishing requires the independently verified Android NDK source archive and exact Git checkouts."
    }
    require(androidCorrespondingSourceVerificationConfigured) {
        "Publishing requires independently verified complete Android corresponding source."
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
}

tasks.withType<PublishToMavenRepository>().configureEach {
    dependsOn(
        verifyNativePayload,
        verifyAndroidAar,
        verifyAndroidNdkSourceArchive,
        verifyAndroidCorrespondingSourceArchive,
    )
    doFirst { requirePublicationPayload() }
}
tasks.withType<PublishToMavenLocal>().configureEach {
    dependsOn(
        verifyNativePayload,
        verifyAndroidAar,
        verifyAndroidNdkSourceArchive,
        verifyAndroidCorrespondingSourceArchive,
    )
    doFirst { requirePublicationPayload() }
}

afterEvaluate {
    publishing {
        publications {
            create<MavenPublication>("release") {
                from(components["release"])
                artifact(androidJavadocJar)
                androidCorrespondingSourceArchive.orNull?.let { archive ->
                    artifact(archive) {
                        classifier = "corresponding-source"
                        extension = "tar.gz"
                    }
                }
                androidNdkSourceArchive.orNull?.let { archive ->
                    artifact(archive) {
                        classifier = "android-ndk-source"
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
