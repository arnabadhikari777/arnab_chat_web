package com.goppo.chat

import android.annotation.SuppressLint
import android.content.pm.ActivityInfo
import android.content.pm.ApplicationInfo
import android.graphics.Color
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.webkit.WebSettings
import android.webkit.WebView
import androidx.activity.OnBackPressedCallback
import androidx.core.splashscreen.SplashScreen.Companion.installSplashScreen
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import com.getcapacitor.Bridge
import com.getcapacitor.BridgeActivity
import com.getcapacitor.JSObject
import kotlin.math.max

/**
 * The only activity. Loads the live website (Capacitor `server.url`) and wires the Android side:
 * navigation guard, WebView permissions, back button, lifecycle events, keyboard/safe-area insets, full-screen video.
 */
class MainActivity : BridgeActivity() {

    lateinit var mediaPermissions: MediaPermissionRequester
        private set

    private val ui = Handler(Looper.getMainLooper())
    private var chromeClient: GoppoChromeClient? = null
    private var fullscreenView: View? = null

    @Volatile private var firstPageDone = false
    private var backSeq = 0
    private var backPendingSeq = -1

    override fun onCreate(savedInstanceState: Bundle?) {
        val splash = installSplashScreen()
        val startedAt = SystemClock.uptimeMillis()
        splash.setKeepOnScreenCondition { !firstPageDone && SystemClock.uptimeMillis() - startedAt < SPLASH_MAX_MS }

        registerPlugin(GoppoNativePlugin::class.java)      // before super.onCreate, as Capacitor requires
        mediaPermissions = MediaPermissionRequester(this)  // registers its launcher before the activity is STARTED
        super.onCreate(savedInstanceState)

        val b: Bridge? = bridge
        if (b == null) { firstPageDone = true; return }     // WebView unavailable: Capacitor shows its own message

        configureWindow(b.webView)
        hardenWebView(b.webView)

        val policy = GoppoRuntime.configure(b.serverUrl)
        // Official GoppoNative proxy for the remote site (after Capacitor's own script), then restart the page load that
        // Capacitor already began so the very first page gets it too.
        if (GoppoBridgeShim.install(b)) {
            b.webView.stopLoading()
            b.webView.loadUrl(b.appUrl)
        }
        if (policy != null) {
            b.setWebViewClient(GoppoWebViewClient(b, policy) { firstPageDone = true })
            // Created here (state CREATED): Capacitor's client registers ActivityResult launchers in its constructor.
            val chrome = GoppoChromeClient(b, policy, this)
            chromeClient = chrome
            b.webView.webChromeClient = chrome
        }

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() = handleBack()
        })
    }

    // ------------------------------------------------------------------ window, insets, keyboard

    @Suppress("DEPRECATION")
    private fun configureWindow(webView: WebView) {
        // Edge-to-edge on every API level; the insets below keep the page clear of bars, cutout and keyboard.
        WindowCompat.setDecorFitsSystemWindows(window, false)
        window.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE)
        window.statusBarColor = Color.TRANSPARENT
        window.navigationBarColor = Color.TRANSPARENT
        window.decorView.setBackgroundColor(BRAND_COLOR)    // what shows behind the bars
        WindowInsetsControllerCompat(window, window.decorView).apply {
            isAppearanceLightStatusBars = false              // light icons on the teal bar
            isAppearanceLightNavigationBars = false
        }
        // Capacitor's own edge-to-edge handler ignores the keyboard; this one includes it, so the message box stays visible.
        ViewCompat.setOnApplyWindowInsetsListener(webView) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout())
            val ime = insets.getInsets(WindowInsetsCompat.Type.ime())
            val lp = v.layoutParams as ViewGroup.MarginLayoutParams
            lp.setMargins(bars.left, bars.top, bars.right, max(bars.bottom, ime.bottom))
            v.layoutParams = lp
            WindowInsetsCompat.CONSUMED
        }
        ViewCompat.requestApplyInsets(webView)
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun hardenWebView(webView: WebView) {
        val s = webView.settings
        s.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
        s.allowFileAccess = false
        // Inspecting the WebView with chrome://inspect works for debug builds only, never for a release APK.
        val debuggable = (applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) != 0
        WebView.setWebContentsDebuggingEnabled(debuggable)
    }

    // ------------------------------------------------------------------ full-screen video

    fun showFullscreenView(view: View) {
        val decor = window.decorView as ViewGroup
        view.setBackgroundColor(Color.BLACK)
        decor.addView(view, ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        fullscreenView = view
        requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_FULL_SENSOR   // rotate freely while a video is full screen
        WindowInsetsControllerCompat(window, decor).apply {
            systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            hide(WindowInsetsCompat.Type.systemBars())
        }
    }

    fun hideFullscreenView() {
        val view = fullscreenView ?: return
        (view.parent as? ViewGroup)?.removeView(view)
        fullscreenView = null
        requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_PORTRAIT
        WindowInsetsControllerCompat(window, window.decorView).show(WindowInsetsCompat.Type.systemBars())
        bridge?.webView?.let { ViewCompat.requestApplyInsets(it) }
    }

    // ------------------------------------------------------------------ back button

    private fun handleBack() {
        if (chromeClient?.exitFullscreen() == true) return               // 1. close full-screen video
        val webView = bridge?.webView
        if (webView == null) { finish(); return }
        val onWebsite = GoppoRuntime.policy?.isAppOrigin(webView.url) == true
        if (!onWebsite) {                                                // offline page etc.: nothing for the site to say
            applyBack(BackPolicy.decide(null, webView.canGoBack(), GoppoRuntime.state.callActive))
            return
        }
        // 2. Ask the website (app:back): it closes a dialog, protects a live call, or goes back in its own history.
        val seq = ++backSeq
        backPendingSeq = seq
        webView.evaluateJavascript(BACK_SCRIPT.replace("__SEQ__", seq.toString())) { result ->
            if (result != "\"pending\"") resolveBack(seq, null)          // no platform script on this page
        }
        ui.postDelayed({ resolveBack(seq, null) }, BACK_TIMEOUT_MS)      // no answer in time
    }

    /** Called by the plugin when the page answered app:back. [consumed] is null if the page could not handle it. */
    fun onBackAnswer(seq: Int, consumed: Boolean?) {
        runOnUiThread { resolveBack(seq, consumed) }
    }

    private fun resolveBack(seq: Int, consumed: Boolean?) {
        if (seq != backPendingSeq) return                                // late or duplicate answer
        backPendingSeq = -1
        val canGoBack = bridge?.webView?.canGoBack() == true
        applyBack(BackPolicy.decide(consumed, canGoBack, GoppoRuntime.state.callActive))
    }

    private fun applyBack(action: BackAction) {
        when (action) {
            BackAction.NONE -> Unit
            BackAction.WEBVIEW_BACK -> bridge?.webView?.goBack()
            BackAction.BACKGROUND -> moveTaskToBack(true)                // keep an active call alive
            BackAction.EXIT -> finish()
        }
    }

    // ------------------------------------------------------------------ lifecycle -> website

    fun onWebReady() {
        runOnUiThread { GoppoRuntime.lifecycle.onWebReady()?.let { sendCommand(it) } }
    }

    override fun onResume() {
        super.onResume()
        GoppoRuntime.lifecycle.onActivityResumed()?.let { sendCommand(it) }
    }

    override fun onPause() {
        super.onPause()
        GoppoRuntime.lifecycle.onActivityPaused()?.let { sendCommand(it) }
        // Deliberately nothing else: an active WebRTC call is not ended because the app went to the background.
    }

    override fun onDestroy() {
        ui.removeCallbacksAndMessages(null)
        super.onDestroy()
    }

    private fun sendCommand(command: String) {
        val plugin = bridge?.getPlugin("GoppoNative")?.instance as? GoppoNativePlugin
        plugin?.sendCommand(command, JSObject())
    }

    private companion object {
        const val BRAND_COLOR = 0xFF0B6E78.toInt()
        const val SPLASH_MAX_MS = 8_000L
        const val BACK_TIMEOUT_MS = 1_000L

        /** Asks platform.js to run its `app:back` handler and reports the answer to GoppoNative.backResult. */
        const val BACK_SCRIPT = "(function(){try{var P=window.GoppoPlatform,N=window.Capacitor&&window.Capacitor.Plugins&&" +
            "window.Capacitor.Plugins.GoppoNative;if(!P||!N||typeof P.receive!=='function')return'none';" +
            "P.receive('app:back',{}).then(function(r){var ok=!!(r&&r.handled&&r.result);" +
            "N.backResult({seq:__SEQ__,answered:ok,consumed:ok&&!!r.result.consumed})})" +
            ".catch(function(){N.backResult({seq:__SEQ__,answered:false,consumed:false})});return'pending'}catch(e){return'none'}})()"
    }
}
