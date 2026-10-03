package com.daiatech.samvaad.app

import android.app.Application
import dagger.hilt.android.HiltAndroidApp
import timber.log.Timber

@HiltAndroidApp
class SamvaadSampleApp : Application() {
    override fun onCreate() {
        super.onCreate()
        // Unconditional, not gated behind a debug check -- this sample has no meaningful release
        // flavor (its whole purpose is to be inspected), and without a planted tree every
        // Timber.d/w/e call across this app and the library is a silent no-op: confirmed on a real
        // device, where none of this session's Timber logging ever appeared until this was added.
        Timber.plant(Timber.DebugTree())
    }
}
