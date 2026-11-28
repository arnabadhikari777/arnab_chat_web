package com.goppo.chat.net

import android.content.Context
import android.webkit.CookieManager
import com.goppo.chat.GoppoRuntime
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID
import javax.net.ssl.HttpsURLConnection

/**
 * HTTPS client of the bundled app. The pages come from the APK (origin https://localhost), so they cannot call the website
 * directly (different origin, SameSite cookies, no CORS). Every API request therefore goes through here, to ONE fixed origin
 * (the backend URL from goppo-backend.json), over HTTPS with normal certificate checks, with the session cookie the server
 * gave at login and the CSRF token from /api/auth/csrf - the same protections the website pages use. No password is stored.
 * Never call on the main thread.
 */
object ApiClient {
    class Result(val code: Int, val body: String) {
        val ok get() = code in 200..299
        val signedOut get() = code == 401 || code in 300..399
    }

    private const val MAX_JSON = 4_000_000
    @Volatile private var csrf: String? = null

    fun forgetCsrf() { csrf = null }

    private fun origin(context: Context): String? = GoppoRuntime.ensureConfigured(context)?.appOriginString

    /** Paths the pages may ask for through the bridge: the app's own API, auth and language files. Nothing else. */
    fun allowedPath(path: String): Boolean =
        path.startsWith("/") && !path.startsWith("//") && !path.contains("..") && !path.contains("\\") &&
            (path.startsWith("/api/") || path == "/logout" || path.startsWith("/i18n/"))

    fun json(context: Context, method: String, path: String, body: String?): Result {
        val o = origin(context) ?: return Result(0, "")
        if (!allowedPath(path)) return Result(0, "")
        return try {
            val needCsrf = method != "GET"
            var token = if (needCsrf) (csrf ?: fetchCsrf(o)) else null
            if (needCsrf && token == null) return Result(0, "")
            var r = call(o, method, path, body, "application/json", token)
            if (needCsrf && r.code == 400 && r.body.contains("CSRF", ignoreCase = true)) {       // token rotated (new session): once more
                csrf = null; token = fetchCsrf(o) ?: return r
                r = call(o, method, path, body, "application/json", token)
            }
            if (path == "/api/auth/login" || path == "/logout") csrf = null                       // a new session has a new token
            r
        } catch (e: Exception) { Result(0, "") }
    }

    private fun fetchCsrf(o: String): String? {
        val r = call(o, "GET", "/api/auth/csrf", null, null, null)
        if (!r.ok) return null
        val t = Regex("\"token\"\\s*:\\s*\"([^\"]+)\"").find(r.body)?.groupValues?.get(1)
        csrf = t
        return t
    }

    private fun call(o: String, method: String, path: String, body: String?, contentType: String?, token: String?): Result {
        val conn = URL(o + path).openConnection() as HttpsURLConnection
        try {
            conn.requestMethod = method; conn.connectTimeout = 10_000; conn.readTimeout = 25_000; conn.instanceFollowRedirects = false
            conn.setRequestProperty("Accept", "application/json"); conn.setRequestProperty("User-Agent", "GoppoAndroid-native")
            conn.setRequestProperty("Referer", "$o/"); conn.setRequestProperty("Origin", o)       // Flask-WTF checks the referrer on HTTPS
            CookieManager.getInstance().getCookie(o)?.let { conn.setRequestProperty("Cookie", it) }
            if (token != null) conn.setRequestProperty("X-CSRFToken", token)
            if (body != null) {
                conn.doOutput = true; conn.setRequestProperty("Content-Type", contentType ?: "application/json")
                conn.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            }
            val code = conn.responseCode
            saveCookies(o, conn)
            val stream = if (code >= 400) conn.errorStream else conn.inputStream
            return Result(code, stream?.use { read(it) } ?: "")
        } finally { conn.disconnect() }
    }

    private fun saveCookies(o: String, conn: HttpURLConnection) {
        val cm = CookieManager.getInstance()
        conn.headerFields["Set-Cookie"]?.forEach { cm.setCookie(o, it) }
        cm.flush()                                                              // survive a process kill
    }

    private fun read(input: InputStream): String {
        val out = ByteArrayOutputStream(); val buf = ByteArray(8192)
        while (out.size() < MAX_JSON) { val n = input.read(buf); if (n < 0) break; out.write(buf, 0, n) }
        return out.toString("UTF-8")
    }

    /** Streams [file] as the multipart field "file" (never loads a whole video into memory). */
    fun upload(context: Context, path: String, fields: Map<String, String>, file: File, fileName: String, mime: String): Result {
        val o = origin(context) ?: return Result(0, "")
        if (!allowedPath(path)) return Result(0, "")
        return try {
            val token = csrf ?: fetchCsrf(o) ?: return Result(0, "")
            val r = upload1(o, path, fields, file, fileName, mime, token)
            if (r.code == 400 && r.body.contains("CSRF", ignoreCase = true)) { csrf = null; upload1(o, path, fields, file, fileName, mime, fetchCsrf(o) ?: return r) } else r
        } catch (e: Exception) { Result(0, "") }
    }

    private fun upload1(o: String, path: String, fields: Map<String, String>, file: File, fileName: String, mime: String, token: String): Result {
        val boundary = "----goppo" + UUID.randomUUID().toString().replace("-", "")
        val safeName = fileName.replace("\"", "").replace("\r", "").replace("\n", "")
        val head = StringBuilder().also { sb ->
            fields.forEach { (k, v) -> sb.append("--$boundary\r\nContent-Disposition: form-data; name=\"$k\"\r\n\r\n$v\r\n") }
            sb.append("--$boundary\r\nContent-Disposition: form-data; name=\"file\"; filename=\"$safeName\"\r\nContent-Type: $mime\r\n\r\n")
        }.toString().toByteArray(Charsets.UTF_8)
        val tail = "\r\n--$boundary--\r\n".toByteArray(Charsets.UTF_8)
        val conn = URL(o + path).openConnection() as HttpsURLConnection
        try {
            conn.requestMethod = "POST"; conn.connectTimeout = 15_000; conn.readTimeout = 120_000; conn.instanceFollowRedirects = false
            conn.setFixedLengthStreamingMode(head.size + file.length() + tail.size)
            conn.setRequestProperty("Content-Type", "multipart/form-data; boundary=$boundary"); conn.setRequestProperty("Accept", "application/json")
            conn.setRequestProperty("X-CSRFToken", token); conn.setRequestProperty("Referer", "$o/"); conn.setRequestProperty("Origin", o)
            conn.setRequestProperty("User-Agent", "GoppoAndroid-native")
            CookieManager.getInstance().getCookie(o)?.let { conn.setRequestProperty("Cookie", it) }
            conn.doOutput = true
            conn.outputStream.use { out -> out.write(head); file.inputStream().use { it.copyTo(out, 16_384) }; out.write(tail) }
            val code = conn.responseCode
            saveCookies(o, conn)
            return Result(code, (if (code >= 400) conn.errorStream else conn.inputStream)?.use { read(it) } ?: "")
        } finally { conn.disconnect() }
    }

    /** Hosts a media file may be downloaded from: the Cloudinary delivery network the server stores originals on. */
    fun downloadHostAllowed(host: String?) = host != null && (host == "res.cloudinary.com" || host.endsWith(".cloudinary.com"))

    class DownloadResult(val code: Int, val bytes: Long, val error: String?)

    /** Streams [url] into [dest] (a .part file). No cookies are sent to the CDN. HTTPS only, at most 3 redirects, host allow-listed. */
    fun download(url: String, dest: File, maxBytes: Long, shouldCancel: () -> Boolean): DownloadResult {
        var current = url
        for (hop in 0..3) {
            val u = URL(current)
            if (u.protocol != "https" || !downloadHostAllowed(u.host)) return DownloadResult(0, 0, "host not allowed")
            val conn = u.openConnection() as HttpsURLConnection
            try {
                conn.instanceFollowRedirects = false; conn.connectTimeout = 15_000; conn.readTimeout = 30_000
                conn.setRequestProperty("User-Agent", "GoppoAndroid-native")
                val code = conn.responseCode
                if (code in 301..308 && code != 304) { current = URL(u, conn.getHeaderField("Location") ?: return DownloadResult(code, 0, "redirect")).toString(); continue }
                if (code != 200) return DownloadResult(code, 0, "http $code")
                val declared = conn.contentLengthLong
                if (declared > maxBytes) return DownloadResult(code, 0, "too large")
                var total = 0L
                conn.inputStream.use { input -> dest.outputStream().use { out ->
                    val buf = ByteArray(16_384)
                    while (true) {
                        if (shouldCancel()) return DownloadResult(code, total, "cancelled")
                        val n = input.read(buf); if (n < 0) break
                        total += n; if (total > maxBytes) return DownloadResult(code, total, "too large")
                        out.write(buf, 0, n)
                    }
                } }
                if (declared > 0 && total != declared) return DownloadResult(code, total, "incomplete")   // connection closed early
                return DownloadResult(code, total, null)
            } catch (e: Exception) { return DownloadResult(0, 0, e.javaClass.simpleName) } finally { conn.disconnect() }
        }
        return DownloadResult(0, 0, "too many redirects")
    }
}
