package com.goppo.chat

import android.graphics.Bitmap
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import com.getcapacitor.Bridge
import com.getcapacitor.BridgeWebViewClient

/**
 * Navigation guard for the main WebView.
 *  - same origin as the website  -> stays in the app
 *  - other http/https sites      -> opened in the system browser (main-frame navigations only)
 *  - everything else             -> dropped
 * Capacitor's default client would open ANY scheme through an Intent; this replaces that.
 */
class GoppoWebViewClient(
    bridge: Bridge,
    private val policy: UrlPolicy,
    private val onFirstPageDone: () -> Unit,
) : BridgeWebViewClient(bridge) {

    private fun decide(view: WebView, url: String?, isMainFrame: Boolean): Boolean {
        return when (policy.classify(url)) {
            UrlDecision.INTERNAL -> false
            UrlDecision.EXTERNAL_BROWSER -> {
                if (isMainFrame) policy.externalUrlOrNull(url)?.let { ExternalLinks.open(view.context, it) }
                true
            }
            UrlDecision.BLOCKED -> true
        }
    }

    override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean =
        decide(view, request.url.toString(), request.isForMainFrame)

    @Suppress("OVERRIDE_DEPRECATION", "DEPRECATION")
    override fun shouldOverrideUrlLoading(view: WebView, url: String): Boolean = decide(view, url, true)

    /** The live website is always fetched by the WebView's own network stack (real cookies, redirects, TLS checks); never proxied. */
    override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? =
        if (policy.isAppOrigin(request.url.toString())) null else super.shouldInterceptRequest(view, request)

    override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) {
        super.onPageStarted(view, url, favicon)       // Capacitor clears listeners of the previous page here
        GoppoRuntime.state.onPageStarted()
        GoppoRuntime.lifecycle.onPageStarted()
    }

    override fun onPageFinished(view: WebView, url: String?) {
        super.onPageFinished(view, url)
        onFirstPageDone()
    }

    /** Offline / DNS / TLS failure of the page itself: show the small offline page. HTTP error codes (404, 500) are left to the website. */
    override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
        onFirstPageDone()
        if (request.isForMainFrame) {
            val code = error.errorCode
            val connectivity = code == WebViewClient.ERROR_HOST_LOOKUP || code == WebViewClient.ERROR_CONNECT ||
                code == WebViewClient.ERROR_TIMEOUT || code == WebViewClient.ERROR_IO
            if (connectivity) super.onReceivedError(view, request, error)   // Capacitor loads server.errorPath
        }
    }

    /** Deliberately not forwarding to Capacitor: it would replace an HTTP 404/500 page of the website with the offline page. */
    override fun onReceivedHttpError(view: WebView, request: WebResourceRequest, errorResponse: WebResourceResponse) {
        if (request.isForMainFrame) onFirstPageDone()
    }

    // No onReceivedSslError override on purpose: the default cancels the load. Certificate validation stays on.
}
