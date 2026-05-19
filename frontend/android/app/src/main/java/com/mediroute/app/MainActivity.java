package com.mediroute.app;

import android.content.pm.ApplicationInfo;
import android.os.Build;
import android.os.Bundle;
import android.webkit.WebView;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
  @Override
  public void onCreate(Bundle savedInstanceState) {
    // Chrome remote debugging for debuggable APK only (no BuildConfig — AGP 8 may omit it).
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.KITKAT) {
      boolean dbg = (getApplicationInfo().flags & ApplicationInfo.FLAG_DEBUGGABLE) != 0;
      WebView.setWebContentsDebuggingEnabled(dbg);
    }
    super.onCreate(savedInstanceState);
  }
}
