package com.goppo.chat

import java.net.URI

/** What the app does with a URL the WebView wants to open. */
enum class UrlDecision {
    /** Same origin as the Goppo website: stays inside the app WebView. */
    INTERNAL,

    /** A normal http/https link to another site: handed to the Android system browser. */
    EXTERNAL_BROWSER,

    /** Unsafe or malformed (javascript:, file:, intent:, custom schemes, credentials in URL, ...): never opened. */
    BLOCKED,
}

/**
 * The single place that decides which URLs may load in the main WebView and which may be opened outside.
 * Pure Kotlin (java.net.URI only) so it is unit-tested on the JVM.
 *
 * The app origin comes from `capacitor.config.json` (`server.url`); nothing is hardcoded here.
 */
class UrlPolicy(appUrl: String) {

    private data class Origin(val scheme: String, val host: String, val port: Int)

    private val appOrigin: Origin = parseOrigin(appUrl)
        ?.takeIf { it.scheme == "https" }
        ?: throw IllegalArgumentException("The app URL must be a valid https URL")

    val appOriginString: String =
        "https://" + appOrigin.host + (if (appOrigin.port != 443) ":${appOrigin.port}" else "")

    /** True only for https + same host + same port as the website. */
    fun isAppOrigin(raw: String?): Boolean {
        val o = parseOrigin(raw) ?: return false
        return o == appOrigin
    }

    fun classify(raw: String?): UrlDecision {
        if (raw == null || raw.isBlank() || raw.length > MAX_URL_LENGTH) return UrlDecision.BLOCKED
        if (raw.any { it.isISOControl() }) return UrlDecision.BLOCKED
        val uri = parse(raw) ?: return UrlDecision.BLOCKED
        val scheme = uri.scheme?.lowercase() ?: return UrlDecision.BLOCKED

        // Pages created by the website itself.
        if (scheme == "about") return if (raw.equals("about:blank", ignoreCase = true)) UrlDecision.INTERNAL else UrlDecision.BLOCKED
        if (scheme == "blob") return if (isAppOrigin(raw.substring(5))) UrlDecision.INTERNAL else UrlDecision.BLOCKED

        if (scheme != "http" && scheme != "https") return UrlDecision.BLOCKED
        if (uri.rawUserInfo != null) return UrlDecision.BLOCKED
        val host = uri.host?.lowercase()
        if (host.isNullOrEmpty()) return UrlDecision.BLOCKED

        if (isAppOrigin(raw)) return UrlDecision.INTERNAL
        // Cleartext http to the app's own host is never allowed to load, and is not "another site" either.
        if (host == appOrigin.host) return UrlDecision.BLOCKED
        return UrlDecision.EXTERNAL_BROWSER
    }

    /** Normalised URL for the system browser, or null if the URL must not be opened externally. */
    fun externalUrlOrNull(raw: String?): String? =
        if (classify(raw) == UrlDecision.EXTERNAL_BROWSER) parse(raw!!)?.toASCIIString() else null

    private fun parse(raw: String): URI? = try { URI(raw.trim()) } catch (_: Exception) { null }

    private fun parseOrigin(raw: String?): Origin? {
        if (raw == null) return null
        val uri = parse(raw) ?: return null
        val scheme = uri.scheme?.lowercase() ?: return null
        if (scheme != "http" && scheme != "https") return null
        if (uri.rawUserInfo != null) return null
        val host = uri.host?.lowercase()?.takeIf { it.isNotEmpty() } ?: return null
        val port = if (uri.port == -1) (if (scheme == "https") 443 else 80) else uri.port
        return Origin(scheme, host, port)
    }

    companion object {
        const val MAX_URL_LENGTH = 2048
    }
}
