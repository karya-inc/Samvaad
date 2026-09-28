plugins {
    alias(libs.plugins.kotlin.jvm)
}

dependencies {
    // api, not implementation: VoipSdkClient.callState exposes StateFlow in this module's own
    // public API, so consumers need kotlinx-coroutines-core on their compile classpath too.
    api(libs.kotlinx.coroutines.core)

    testImplementation(libs.junit)
}

kotlin {
    jvmToolchain(21)
}
