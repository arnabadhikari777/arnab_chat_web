package com.goppo.chat.net

import android.content.Context
import android.webkit.CookieManager
import com.goppo.chat.GoppoRuntime
import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.URL
import javax.net.ssl.HttpsURLConnection

/**
 * Minimal HTTPS client for the few things the phone must do while no page is open (check a call before accepting it,
 * decline a call, acknowledge a message). It talks ONLY to the website origin from `server.url`, over HTTPS with normal
 * certificate validation, and reuses the WebView's own session cookie: there is no second login and no stored password.
 * Never call on the main thread.
 */
object SiteClient {
    class Result(val code: Int, val body: String) {
        val ok: Boolean get() = code in 200..299
        /** 401/403, or a redirect to the login page: the person is signed out on this phone. */
        val signedOut: Boolean get() = code == 401 || code == 403 || code in 300..399
    }

    private const val CSRF_PAGE = "/settings/"
    private const val MAX_BYTES = 262_144
    private val CSRF_META = Regex("<meta\\s+name=\"csrf-token\"\\s+content=\"([^\"]+)\"")

    fun get(context: Context, path: String): Result = request(context, "GET", path, null, withCookies = true, csrf = false)

    /** POST JSON with the session cookie and the CSRF token the website's own pages use. */
    fun postJson(context: Context, path: String, json: String): Result = request(context, "POST", path, json, withCookies = true, csrf = true)

    /** POST JSON with no cookie and no CSRF (the delivery-receipt endpoint is authenticated by its signed token). */
    fun postAnonymous(context: Context, path: String, json: String): Result = request(context, "POST", path, json, withCookies = false, csrf = false)

    private fun request(context: Context, method: String, path: String, body: String?, withCookies: Boolean, csrf: Boolean): Result {
        val origin = GoppoRuntime.ensureConfigured(context)?.appOriginString ?: return Result(0, "")
        if (!path.startsWith("/") || path.startsWith("//")) return Result(0, "")
        return try {
            val token = if (csrf) fetchCsrf(origin) else null
            if (csrf && token == null) return Result(401, "")
            call(origin, method, path, body, withCookies, token)
        } catch (e: Exception) {
            Result(0, "")          // offline, DNS, TLS failure ...: callers treat 0 as "unknown", never as "ringing"
        }
    }

    private fun fetchCsrf(origin: String): String? {
        val page = call(origin, "GET", CSRF_PAGE, null, withCookies = true, csrf = null)
        if (!page.ok) return null
        return CSRF_META.find(page.body)?.groupValues?.get(1)
    }

    private fun call(origin: String, method: String, path: String, body: String?, withCookies: Boolean, csrf: String?): Result {
        val conn = URL(origin + path).openConnection() as HttpsURLConnection     // https only: the origin is validated as https
        try {
            conn.requestMethod = method
            conn.connectTimeout = 6_000
            conn.readTimeout = 8_000
            conn.instanceFollowRedirects = false
            conn.setRequestProperty("Accept", "application/json, text/html;q=0.5")
            conn.setRequestProperty("User-Agent", "GoppoAndroid-native")
            if (withCookies) CookieManager.getInstance().getCookie(origin)?.let { conn.setRequestProperty("Cookie", it) }
            if (csrf != null) conn.setRequestProperty("X-CSRFToken", csrf)
            if (body != null) {
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", "application/json")
                conn.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            }
            val code = conn.responseCode
            if (withCookies) {
                conn.headerFields["Set-Cookie"]?.forEach { CookieManager.getInstance().setCookie(origin, it) }
            }
            val stream = if (code >= 400) conn.errorStream else conn.inputStream
            return Result(code, stream?.use { readLimited(it) } ?: "")
        } finally {
            conn.disconnect()
        }
    }

    private fun readLimited(input: java.io.InputStream): String {
        val out = ByteArrayOutputStream()
        val buf = ByteArray(8192)
        while (out.size() < MAX_BYTES) {
            val n = input.read(buf)
            if (n < 0) break
            out.write(buf, 0, n)
        }
        return out.toString("UTF-8")
    }

    /** Asks the server whether a call can still be answered by this person. */
    sealed class CallCheck {
        object Ringing : CallCheck()
        data class Over(val status: String) : CallCheck()
        object SignedOut : CallCheck()
        object Unknown : CallCheck()        // no network: the website checks again when it accepts
    }

    fun checkCall(context: Context, callId: String): CallCheck {
        val r = get(context, "/api/calling/state/$callId")
        if (r.code == 404) return CallCheck.Over("not_found")          // not a call of this person: never accept
        if (r.signedOut) return CallCheck.SignedOut
        if (!r.ok) return CallCheck.Unknown
        val status = Regex("\"status\"\\s*:\\s*\"([a-z_]+)\"").find(r.body)?.groupValues?.get(1) ?: return CallCheck.Unknown
        return if (status == "initiated" || status == "ringing") CallCheck.Ringing else CallCheck.Over(status)
    }
}
