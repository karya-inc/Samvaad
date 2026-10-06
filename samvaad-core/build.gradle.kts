plugins {
    alias(libs.plugins.kotlin.jvm)
    alias(libs.plugins.maven.publish)
}

mavenPublishing {
    coordinates("io.github.karya-inc", "samvaad-core", "0.1.0")

    publishToMavenCentral(automaticRelease = true)
    signAllPublications()

    pom {
        name.set("Samvaad Core")
        description.set("Framework-agnostic VoIP call-lifecycle interfaces that Samvaad's Android implementation builds on.")
        inceptionYear.set("2026")
        url.set("https://github.com/karya-inc/Samvaad")

        licenses {
            license {
                name.set("GNU General Public License v3.0")
                url.set("https://www.gnu.org/licenses/gpl-3.0.html")
                distribution.set("https://www.gnu.org/licenses/gpl-3.0.html")
            }
        }

        developers {
            developer {
                id.set("DeepanshuPratik")
                name.set("Deepanshu Pratik")
                email.set("deepanshu@karya.in")
            }
        }

        scm {
            url.set("https://github.com/karya-inc/Samvaad")
            connection.set("scm:git:git://github.com/karya-inc/Samvaad.git")
            developerConnection.set("scm:git:ssh://git@github.com/karya-inc/Samvaad.git")
        }
    }
}

dependencies {
    // api, not implementation: VoipSdkClient.callState exposes StateFlow in this module's own
    // public API, so consumers need kotlinx-coroutines-core on their compile classpath too.
    api(libs.kotlinx.coroutines.core)

    testImplementation(libs.junit)
}

java {
    sourceCompatibility = JavaVersion.VERSION_11
    targetCompatibility = JavaVersion.VERSION_11
}

kotlin {
    jvmToolchain(21)
    compilerOptions {
        // Pinned below JVM 17: at jvmTarget 17+, Kotlin emits a real PermittedSubclasses
        // attribute for `sealed interface VoipCallState`, which the old ASM bundled in AGP's
        // Dokka-based javadoc generation (used when samvaad-android publishes, since it depends
        // on this module) can't parse -- it only understands ASM9's PermittedSubclasses support.
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_11)
    }
}
