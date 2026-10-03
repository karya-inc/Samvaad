Type: prototype
Blocked by: 02

## Question

With network quality reaching `MainActivity` in the shape decided by [ticket 02](./02-surface-through-conferencer-binding.md), prototype how it actually reads in the sample app's `JoinMeetingScreen` (the `Ongoing` state, alongside the duration/mic/recording row).

The library stays UI-free (settled destination decision) — this is purely the sample app's own Compose UI. Rough, reactable concrete UI: a traffic-light-style indicator for `GOOD`/`POOR`/`BAD`, and a decision on what (if anything) renders when the value is `null` (no reading available — see [CONTEXT.md](../../../CONTEXT.md)'s "No reading available" entry: could mean "not computed yet" or "not supported," and the UI can't tell which).
