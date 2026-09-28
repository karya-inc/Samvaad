package com.daiatech.samvaad.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import com.daiatech.samvaad.android.SamvaadGreeting

/**
 * Sample app for manually exercising the Samvaad library during development -- not published,
 * not part of the library's public surface. Its only job is to give this project something
 * Android Studio can actually Run/install, since :samvaad-android and :samvaad-core are both
 * libraries and produce no APK of their own.
 */
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    SamvaadGreeting(name = "Samvaad")
                }
            }
        }
    }
}
