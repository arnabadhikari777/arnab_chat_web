package com.goppo.chat

import android.graphics.Bitmap
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import com.getcapacitor.Bridge
import com.getcapacitor.BridgeWebViewClient
import com.goppo.chat.notify.MessageNotifier
import com.goppo.chat.push.SitePaths

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
        // The website's own links to pages that have a bundled twin open the local page (works offline, from the local database).
        if (isMainFrame && policy.isAppOrigin(url)) {
            val uri = runCatching { java.net.URI(url) }.getOrNull()
            val raw = uri?.rawPath ?: ""
            val isBundledFile = policy.isLocalOrigin(url) && (raw == "/" || raw.endsWith(".html"))     // already a bundled page
            if (uri != null && !isBundledFile) {
                LocalRoutes.toLocal(raw + (uri.rawQuery?.let { q -> "?$q" } ?: ""))?.let { local -> view.loadUrl(policy.localOriginString + "/" + local); return true }
            }
        }
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
    override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? {
        val url = request.url.toString()
        if (policy.isLocalOrigin(url)) {
            val path = request.url.path ?: ""
            if (path.startsWith(MEDIA_PREFIX)) return serveMedia(view.context, path.removePrefix(MEDIA_PREFIX), request.requestHeaders?.get("Range"))
            return super.shouldInterceptRequest(view, request)          // Capacitor serves the bundled pages from the APK
        }
        return if (policy.isBackendOrigin(url)) null else super.shouldInterceptRequest(view, request)
    }

    /**
     * Serves ONE downloaded media file of the signed-in account to the bundled pages: addressed by media id, looked up in the
     * local database, and only if the file really lives inside that account's private media folder. Nothing else on the phone
     * can be reached through this URL, and other origins never get here (only the local origin is routed to this method).
     */
    private fun serveMedia(ctx: android.content.Context, idPart: String, range: String?): WebResourceResponse {
        fun notFound() = WebResourceResponse("text/plain", "utf-8", 404, "Not Found", mapOf("Cache-Control" to "no-store"), java.io.ByteArrayInputStream(ByteArray(0)))
        val id = idPart.toLongOrNull() ?: return notFound()
        val store = com.goppo.chat.local.Session.store(ctx) ?: return notFound()
        val row = store.attachmentRow(id) ?: return notFound()
        val file = row.localPath?.let { store.insideMedia(java.io.File(it)) }?.takeIf { it.isFile } ?: return notFound()
        val mime = when (file.extension.lowercase()) {
            "jpg", "jpeg" -> "image/jpeg"; "png" -> "image/png"; "gif" -> "image/gif"; "webp" -> "image/webp"
            "mp4", "m4v" -> "video/mp4"; "webm" -> "video/webm"; "mov" -> "video/quicktime"
            else -> if (row.type == "video") "video/mp4" else if (row.type == "image") "image/jpeg" else "application/octet-stream"
        }
        val len = file.length()
        val headers = mutableMapOf("Cache-Control" to "private, no-store", "Accept-Ranges" to "bytes", "X-Content-Type-Options" to "nosniff")
        val r = RangeHeader.parse(range, len)
        if (r == null) { headers["Content-Length"] = len.toString(); return WebResourceResponse(mime, null, 200, "OK", headers, file.inputStream()) }
        val raf = java.io.RandomAccessFile(file, "r"); raf.seek(r.first)
        val stream = object : java.io.InputStream() {
            var left = r.second - r.first + 1
            override fun read(): Int { if (left <= 0) return -1; left--; return raf.read() }
            override fun read(b: ByteArray, off: Int, n: Int): Int { if (left <= 0) return -1; val k = raf.read(b, off, minOf(n.toLong(), left).toInt()); if (k > 0) left -= k; return k }
            override fun close() { raf.close() }
        }
        headers["Content-Range"] = "bytes ${r.first}-${r.second}/$len"; headers["Content-Length"] = (r.second - r.first + 1).toString()
        return WebResourceResponse(mime, null, 206, "Partial Content", headers, stream)
    }

    override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) {
        super.onPageStarted(view, url, favicon)       // Capacitor clears listeners of the previous page here
        GoppoRuntime.state.onPageStarted()
        GoppoRuntime.lifecycle.onPageStarted()
        val path = when {
            policy.isLocalOrigin(url) -> runCatching { java.net.URI(url) }.getOrNull()?.let { LocalRoutes.toSite(it.rawPath, it.rawQuery) }   // chat.html?c=12 -> /chat/12
            policy.isAppOrigin(url) -> runCatching { java.net.URI(url).rawPath }.getOrNull()
            else -> null
        }
        GoppoRuntime.currentPath = path
        // Opening a chat clears its notification (and the group summary when it was the last one).
        SitePaths.conversationId(path)?.let { MessageNotifier.cancelConversation(view.context, it) }
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
            // A bundled page cannot fail to load; a hosted page (people, profile, registration ...) can: show the bundled offline page.
            if (connectivity && !policy.isLocalOrigin(request.url.toString())) view.loadUrl(policy.localOriginString + "/offline.html")
        }
    }

    /** Deliberately not forwarding to Capacitor: it would replace an HTTP 404/500 page of the website with the offline page. */
    override fun onReceivedHttpError(view: WebView, request: WebResourceRequest, errorResponse: WebResourceResponse) {
        if (request.isForMainFrame) onFirstPageDone()
    }

    private companion object { const val MEDIA_PREFIX = "/__goppo_media__/" }

    // No onReceivedSslError override on purpose: the default cancels the load. Certificate validation stays on.
}
