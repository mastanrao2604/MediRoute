# ─── MediRoute ProGuard Rules ───────────────────────────────────────────────

# Preserve line numbers for crash reporting (Sentry)
-keepattributes SourceFile,LineNumberTable
-renamesourcefileattribute SourceFile

# ─── Capacitor WebView ───────────────────────────────────────────────────────
# Keep all @JavascriptInterface annotated methods reachable from JS
-keepclassmembers class * {
    @android.webkit.JavascriptInterface <methods>;
}
-keepattributes JavascriptInterface

# Keep Capacitor bridge classes
-keep class com.getcapacitor.** { *; }
-dontwarn com.getcapacitor.**

# ─── Cordova plugins (used by Capacitor) ─────────────────────────────────────
-keep class org.apache.cordova.** { *; }
-dontwarn org.apache.cordova.**

# ─── AndroidX / Jetpack ──────────────────────────────────────────────────────
-keep class androidx.** { *; }
-dontwarn androidx.**

# ─── FileProvider / Content URIs ─────────────────────────────────────────────
-keep class androidx.core.content.FileProvider { *; }

# ─── Google Play Services / Google Sign-In ───────────────────────────────────
# Required by @codetrix-studio/capacitor-google-auth native SDK
-keep class com.google.android.gms.** { *; }
-dontwarn com.google.android.gms.**
-keep class com.google.android.gms.auth.api.signin.** { *; }
-keep class com.google.android.gms.common.api.** { *; }

# ─── Capacitor Google Auth plugin ────────────────────────────────────────────
-keep class ee.forgr.** { *; }
-keep class com.codetrixstudio.** { *; }
-dontwarn com.codetrixstudio.**

# ─── Google API client (JSON/HTTP) ───────────────────────────────────────────
-keep class com.google.api.** { *; }
-dontwarn com.google.api.**
-keep class com.google.gson.** { *; }
-dontwarn com.google.gson.**
