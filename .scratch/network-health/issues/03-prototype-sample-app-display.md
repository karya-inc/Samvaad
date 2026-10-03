Type: prototype
Blocked by: 02
Status: resolved

## Question

With network quality reaching `MainActivity` in the shape decided by [ticket 02](./02-surface-through-conferencer-binding.md), prototype how it actually reads in the sample app's `JoinMeetingScreen` (the `Ongoing` state, alongside the duration/mic/recording row).

The library stays UI-free (settled destination decision) — this is purely the sample app's own Compose UI. Rough, reactable concrete UI: a traffic-light-style indicator for `GOOD`/`POOR`/`BAD`, and a decision on what (if anything) renders when the value is `null` (no reading available — see [CONTEXT.md](../../../CONTEXT.md)'s "No reading available" entry: could mean "not computed yet" or "not supported," and the UI can't tell which).

## Answer

Built directly in `MainActivity.kt`'s `JoinMeetingScreen`, in the `Ongoing` branch: a new `NetworkQualityIndicator` composable -- a small colored dot (green/yellow/red) plus a text label ("Good"/"Poor"/"Bad"), always visible during a call. `null` renders as a neutral gray dash ("—"), deliberately not distinguishing "not computed yet" from "not supported" in the UI either, since the type itself doesn't (see CONTEXT.md).

Separately satisfies the "flag for bad network" half of the original ask: an extra red warning line ("⚠ Bad network connection") appears only when quality is exactly `BAD`, so the signal isn't relying on color alone.

NOT visually verified on a real device/emulator -- no device was available in this environment (confirmed via `adb devices`). Verified: full build (`assembleDebug`) succeeds and the full test suite passes, but someone should eyeball this on an actual device/emulator before considering the sample app's UI itself done.

