package com.goppo.chat

import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import com.getcapacitor.Bridge
import com.getcapacitor.Logger

/**
 * Makes the official `Capacitor.Plugins.GoppoNative` proxy available to the REMOTE website.
 *
 * Capacitor injects its native bridge and the plugin method list into pages of the configured origin, but the website
 * does not bundle `@capacitor/core`, so only a deprecated "legacy" proxy would exist. This injects, at document start
 * and for the website origin only, the official `@capacitor/core` runtime plus `registerPlugin("GoppoNative")`
 * (assets/goppo-bridge.js, built by `npm run prepare:bridge`). It is the same `registerPlugin` path an ordinary
 * Capacitor app uses, and it runs before platform.js.
 *
 * It MUST be installed after Capacitor's own script (which begins with `window.Capacitor = {...}`), i.e. after
 * `super.onCreate()`. Scripts run in registration order. The first navigation was already started by Capacitor, so the
 * caller restarts it when this returns true.
 */
object GoppoBridgeShim {
    private const val ASSET = "goppo-bridge.js"

    /** @return true if the script was registered (the caller must then restart the first page load). */
    fun install(bridge: Bridge): Boolean {
        val policy = GoppoRuntime.configure(bridge.serverUrl) ?: return false
        if (!WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT)) {
            // Very old WebView: the website still works as in a browser (platform.js falls back); no native bridge.
            Logger.warn("Goppo: WebView lacks DOCUMENT_START_SCRIPT; native bridge not injected")
            return false
        }
        try {
            val js = bridge.context.assets.open(ASSET).bufferedReader().use { it.readText() }
            WebViewCompat.addDocumentStartJavaScript(bridge.webView, js, setOf(policy.appOriginString))
            return true
        } catch (e: Exception) {
            Logger.error("Goppo: could not inject the native bridge script", e)
            return false
        }
    }
}
