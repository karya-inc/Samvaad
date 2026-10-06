package com.daiatech.samvaad.app

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.testTagsAsResourceId
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import com.daiatech.samvaad.android.ConferencerBinding
import com.daiatech.samvaad.android.ConferencerUiState
import com.daiatech.samvaad.core.CallProvider
import com.daiatech.samvaad.core.CallRole
import com.daiatech.samvaad.core.NetworkQuality

/**
 * Sample app: enter a Daily.co meeting link, join it through the library's full stack
 * (ConferencerBinding -> SampleVoipCallService -> DailyCoVoipSdkClient), toggle the mic and
 * cloud recording mid-call, and see the service's own duration timer tick. Caller-only -- there's
 * no incoming-call concept here, just "enter a link and join".
 */
class MainActivity : ComponentActivity() {

    private lateinit var binding: ConferencerBinding<MeetingMetadata>
    private var pendingMeetingLink: String? = null

    private val requestPermissions = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions(),
    ) { _ ->
        // Re-check via checkSelfPermission rather than trusting the result map -- on some OEMs a
        // permission already granted before this launch doesn't appear in the callback's map at all.
        val link = pendingMeetingLink
        pendingMeetingLink = null
        if (link != null && hasRecordAudioPermission()) {
            binding.acceptOrInitiate(MeetingMetadata(meetingLink = link))
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        binding = ConferencerBinding(
            context = this,
            role = CallRole.CALLER,
            serviceClass = SampleVoipCallService::class.java,
            callId = { it.meetingLink },
            joinConfig = { it.meetingLink },
            provider = { CallProvider.DAILYCO },
        )
        // Reattach if this Activity instance is fresh but a call is still running in the
        // background service (rotation, or the Activity was recreated after process death).
        binding.attachIfRunning()

        setContent {
            MaterialTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    JoinMeetingScreen(binding = binding, onJoinRequested = ::requestJoin)
                }
            }
        }
    }

    private fun requestJoin(meetingLink: String) {
        val needed = buildList {
            if (!hasRecordAudioPermission()) add(Manifest.permission.RECORD_AUDIO)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU && !hasPostNotificationsPermission()) {
                add(Manifest.permission.POST_NOTIFICATIONS)
            }
        }
        if (needed.isEmpty()) {
            binding.acceptOrInitiate(MeetingMetadata(meetingLink = meetingLink))
        } else {
            pendingMeetingLink = meetingLink
            requestPermissions.launch(needed.toTypedArray())
        }
    }

    private fun hasRecordAudioPermission() =
        ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

    private fun hasPostNotificationsPermission() =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    override fun onDestroy() {
        // Never from a UI event (that's declineOrEnd()) -- this is this Activity instance's own
        // teardown. The call itself, if one is active, keeps running in the foreground service;
        // a future Activity instance's attachIfRunning() reattaches to it.
        binding.destroy()
        super.onDestroy()
    }
}

@OptIn(ExperimentalComposeUiApi::class)
@Composable
private fun JoinMeetingScreen(
    binding: ConferencerBinding<MeetingMetadata>,
    onJoinRequested: (String) -> Unit,
) {
    val uiState by binding.uiState.collectAsState()
    var meetingLink by remember { mutableStateOf("") }

    // testTagsAsResourceId: without this, Modifier.testTag() below is invisible outside a
    // ComposeTestRule -- it never reaches the Android accessibility tree, so a host-side
    // UiAutomator dump (what the qa/ E2E suite uses, since it drives real emulators over adb,
    // not an instrumented test) would see no resource-id at all and have to fall back to
    // fragile pixel-coordinate taps.
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp)
            .semantics { testTagsAsResourceId = true },
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text(text = "Samvaad sample", style = MaterialTheme.typography.headlineSmall)
        // Unambiguous for a test to assert on -- unlike the human-facing strings below, this
        // never changes wording and never needs updating just because someone tweaks copy.
        Text(
            text = uiState::class.simpleName.orEmpty(),
            modifier = Modifier.testTag("call_state_label"),
        )

        when (val state = uiState) {
            is ConferencerUiState.Idle -> {
                OutlinedTextField(
                    value = meetingLink,
                    onValueChange = { meetingLink = it },
                    label = { Text("Daily.co meeting link") },
                    modifier = Modifier.fillMaxWidth().testTag("meeting_link_field"),
                )
                Button(
                    onClick = { onJoinRequested(meetingLink) },
                    enabled = meetingLink.isNotBlank(),
                    modifier = Modifier.fillMaxWidth().testTag("join_button"),
                ) {
                    Text("Join meeting")
                }
            }

            is ConferencerUiState.Connecting -> {
                Text("Connecting to ${state.meta?.meetingLink.orEmpty()} ...")
                Button(
                    onClick = { binding.declineOrEnd() },
                    modifier = Modifier.fillMaxWidth().testTag("cancel_button"),
                ) {
                    Text("Cancel")
                }
            }

            is ConferencerUiState.Ongoing -> {
                val durationSeconds by state.durationSeconds.collectAsState()
                val networkQuality by state.networkQuality.collectAsState()
                // Keyed on `state`, not a bare `remember` -- reads the SDK's real current values
                // fresh every time a *new* Ongoing state is emitted (first entering the call, or
                // reattaching after a config change/process restart to a call already in
                // progress with mic/recording already toggled), rather than always resetting to
                // hardcoded defaults regardless of actual state.
                var micEnabled by remember(state) { mutableStateOf(binding.isMicrophoneEnabled()) }
                var recordingEnabled by remember(state) { mutableStateOf(binding.isRecordingEnabled()) }

                Text("In call — ${formatDuration(durationSeconds)}")

                NetworkQualityIndicator(networkQuality)
                if (networkQuality == NetworkQuality.BAD) {
                    Text(
                        "⚠ Bad network connection",
                        color = Color(0xFFC62828),
                        style = MaterialTheme.typography.bodyMedium,
                    )
                }

                Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    Text("Microphone")
                    Switch(
                        checked = micEnabled,
                        onCheckedChange = {
                            micEnabled = it
                            binding.toggleMic(it)
                        },
                        modifier = Modifier.testTag("mic_switch"),
                    )
                }

                Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    Text("Recording")
                    Switch(
                        checked = recordingEnabled,
                        onCheckedChange = {
                            recordingEnabled = it
                            binding.setRecordingEnabled(it)
                        },
                        modifier = Modifier.testTag("recording_switch"),
                    )
                }

                Button(
                    onClick = { binding.declineOrEnd() },
                    modifier = Modifier.fillMaxWidth().testTag("leave_button"),
                ) {
                    Text("Leave call")
                }
            }

            is ConferencerUiState.Disconnecting -> {
                Text("Leaving ...")
            }

            is ConferencerUiState.Error -> {
                Text("Error: ${state.message.orEmpty()}")
                Button(
                    onClick = { binding.declineOrEnd() },
                    modifier = Modifier.fillMaxWidth().testTag("dismiss_button"),
                ) {
                    Text("Dismiss")
                }
            }

            is ConferencerUiState.Incoming -> {
                // Callee-only state -- unreachable in this caller-only sample (CallRole.CALLER
                // never calls onIncomingCall), included only so the `when` stays exhaustive.
                Text("Incoming call (unused in this sample)")
            }
        }
    }
}

/**
 * Prototype for wayfinder ticket "Prototype the sample app display" (network-health map): a plain
 * dot + label, not a library component -- the library stays UI-free (destination decision), this
 * is purely the sample app's own Compose UI, deliberately simple since it's a demo.
 */
@Composable
private fun NetworkQualityIndicator(quality: NetworkQuality?) {
    val (color, label) = when (quality) {
        NetworkQuality.GOOD -> Color(0xFF2E7D32) to "Good"
        NetworkQuality.POOR -> Color(0xFFF9A825) to "Poor"
        NetworkQuality.BAD -> Color(0xFFC62828) to "Bad"
        // No reading available yet -- either the first poll hasn't landed, or this provider
        // doesn't implement the signal at all; the UI can't tell which (see CONTEXT.md), so it
        // shows a neutral dash rather than guessing.
        null -> Color.Gray to "—"
    }
    Row(
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Box(modifier = Modifier.size(12.dp).background(color = color, shape = CircleShape))
        Text("Network: $label")
    }
}

private fun formatDuration(totalSeconds: Long): String {
    val minutes = totalSeconds / 60
    val seconds = totalSeconds % 60
    return "%02d:%02d".format(minutes, seconds)
}
