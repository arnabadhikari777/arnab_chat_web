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
 * Two first-party origins (Task 2, bundled UI): the LOCAL origin `https://localhost` (the pages inside the APK) and the BACKEND
 * origin (the website, from assets/goppo-backend.json: API calls and the few server-rendered pages that are not bundled).
 * Both stay inside the app; every other site goes to the system browser.
 */
class UrlPolicy(appUrl: String, localUrl: String = LOCAL_ORIGIN) {

    private data class Origin(val scheme: String, val host: String, val port: Int)

    private val appOrigin: Origin = parseOrigin(appUrl)
        ?.takeIf { it.scheme == "https" }
        ?: throw IllegalArgumentException("The app URL must be a valid https URL")

    val appOriginString: String =
        "https://" + appOrigin.host + (if (appOrigin.port != 443) ":${appOrigin.port}" else "")

    private val localOrigin: Origin = parseOrigin(localUrl)?.takeIf { it.scheme == "https" }
        ?: throw IllegalArgumentException("The local origin must be a valid https URL")

    val localOriginString: String = "https://" + localOrigin.host + (if (localOrigin.port != 443) ":${localOrigin.port}" else "")

    /** The bundled pages (served from the APK by Capacitor). */
    fun isLocalOrigin(raw: String?): Boolean = parseOrigin(raw) == localOrigin

    /** The website itself: https + same host + same port as the backend. */
    fun isBackendOrigin(raw: String?): Boolean = parseOrigin(raw) == appOrigin

    /** Either first-party origin. */
    fun isAppOrigin(raw: String?): Boolean {
        val o = parseOrigin(raw) ?: return false
        return o == appOrigin || o == localOrigin
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
        if (host == appOrigin.host || host == localOrigin.host) return UrlDecision.BLOCKED   // cleartext / other port of a first-party host
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
        const val LOCAL_ORIGIN = "https://localhost"
    }
}

/**
 * Hosted (server-rendered) paths that have a bundled twin. The website's own links ("/messages", "/chat/12", "/settings/",
 * a push notification's path ...) open the local page instead, so the chat works from the local database. Everything not
 * listed stays a hosted page (people, profiles, notices, registration, password reset ...), reachable while online.
 */
object LocalRoutes {
    private val CHAT = Regex("^/chat/(\\d{1,18})/?$")

    /** The bundled page for a website path, relative to the local origin ("chat.html?c=12"), or null. */
    fun toLocal(path: String?): String? {
        if (path == null || path.contains("hosted=1")) return null            // "?hosted=1" = the website's own page on purpose
        val p = path.substringBefore('?').substringBefore('#')
        CHAT.find(p)?.let { return "chat.html?c=" + it.groupValues[1] }
        return when (p.trimEnd('/').ifEmpty { "/" }) {
            "/", "/messages" -> "messages.html"
            "/settings" -> "settings.html"
            "/login" -> "login.html"
            else -> null
        }
    }

    /** The website path a bundled page stands for ("/chat.html" + "c=12" -> "/chat/12"); used for "is this chat open?" checks. */
    fun toSite(rawPath: String?, rawQuery: String?): String? = when (rawPath) {
        "/chat.html" -> Regex("(?:^|&)c=(\\d{1,18})(?:&|$)").find(rawQuery ?: "")?.let { "/chat/" + it.groupValues[1] }
        "/messages.html" -> "/messages"
        "/settings.html" -> "/settings/"
        else -> null
    }
}

/** HTTP Range header ("bytes=a-b", "bytes=a-", "bytes=-n") against a file of [length] bytes -> inclusive (first, last), or null = whole file. */
object RangeHeader {
    fun parse(header: String?, length: Long): Pair<Long, Long>? {
        if (header == null || length <= 0 || !header.startsWith("bytes=") || header.contains(",")) return null
        val (a, b) = header.removePrefix("bytes=").split("-", limit = 2).let { (it.getOrNull(0) ?: "") to (it.getOrNull(1) ?: "") }
        val first: Long; val last: Long
        if (a.isEmpty()) { val n = b.toLongOrNull() ?: return null; if (n <= 0) return null; first = maxOf(0L, length - n); last = length - 1 }
        else { first = a.toLongOrNull() ?: return null; last = if (b.isEmpty()) length - 1 else (b.toLongOrNull() ?: return null).coerceAtMost(length - 1) }
        return if (first in 0..last && first < length) first to last else null
    }
}
