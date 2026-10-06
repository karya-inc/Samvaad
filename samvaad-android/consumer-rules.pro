# Keep rules for consumers of this library.

# Keep Samvaad's own error hierarchy -- class names and fields -- so a consumer's R8/ProGuard-
# minified release build still shows real names in logs and crash reports instead of renamed
# ones. SamvaadError.errorCode (a plain string) survives obfuscation regardless, but the class
# names (e.g. for `is SamvaadError.ClientCreationFailed` checks, or just reading a stack trace)
# are worth protecting too.
-keep class com.daiatech.samvaad.core.SamvaadError { *; }
-keep class com.daiatech.samvaad.core.SamvaadError$* { *; }
-keep class com.daiatech.samvaad.android.SamvaadAndroidError { *; }
-keep class com.daiatech.samvaad.android.SamvaadAndroidError$* { *; }

