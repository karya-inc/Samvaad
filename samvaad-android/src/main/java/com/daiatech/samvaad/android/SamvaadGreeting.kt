package com.daiatech.samvaad.android

import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier

/**
 * Sample composable provided by Samvaad library.
 */
@Composable
fun SamvaadGreeting(
    name: String,
    modifier: Modifier = Modifier
) {
    Text(
        text = "Hello $name from Samvaad!",
        modifier = modifier
    )
}
