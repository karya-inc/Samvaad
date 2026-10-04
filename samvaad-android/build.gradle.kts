plugins {
    alias(libs.plugins.android.library)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.compose.compiler)
    alias(libs.plugins.maven.publish)
}

mavenPublishing {
    coordinates("io.github.karya-inc", "samvaad-android", "0.1.0")

    publishToMavenCentral(automaticRelease = true)
    signAllPublications()

    pom {
        name.set("Samvaad Android")
        description.set("A VoIP/conferencer calling library for Android: provider-agnostic call lifecycle, a foreground-service/Binder layer, duration tracking, and network quality reporting.")
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

android {
    namespace = "com.daiatech.samvaad.android"
    compileSdk = 35

    defaultConfig {
        minSdk = 23

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        consumerProguardFiles("consumer-rules.pro")
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        compose = true
    }

    testOptions {
        unitTests {
            isIncludeAndroidResources = true
            all {
                // Required for Robolectric on JDK 17+ -- these modules aren't opened by default
                // since Gradle 7.5, and Robolectric needs reflective access into them.
                it.jvmArgs(
                    "--add-opens=java.base/java.lang=ALL-UNNAMED",
                    "--add-opens=java.base/java.util=ALL-UNNAMED",
                    "--add-opens=java.base/java.io=ALL-UNNAMED",
                    "--add-opens=java.base/java.net=ALL-UNNAMED",
                    "--add-opens=java.base/java.security=ALL-UNNAMED",
                    "--add-opens=java.base/java.text=ALL-UNNAMED",
                    "--add-opens=java.base/jdk.internal.access=ALL-UNNAMED",
                )
            }
        }
    }
}

dependencies {
    api(project(":samvaad-core"))

    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.kotlinx.coroutines.core)
    implementation(libs.timber)

    // Compose
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.compose.material3)

    // Testing
    testImplementation(libs.junit)
    testImplementation(libs.mockito.kotlin)
    testImplementation(libs.robolectric)
    testImplementation(libs.kotlinx.coroutines.test)
    testImplementation(libs.androidx.test.core)
    androidTestImplementation(libs.androidx.junit)
    androidTestImplementation(libs.androidx.espresso.core)
    debugImplementation(libs.androidx.compose.ui.tooling)
}
