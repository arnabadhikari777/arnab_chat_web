package com.goppo.chat.notify

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.BitmapShader
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Shader
import android.os.Handler
import android.os.Looper
import java.net.URL
import java.util.concurrent.Executors
import javax.net.ssl.HttpsURLConnection

/** Downloads a profile picture (https only, small, no redirects) and returns it as a circle. Result is delivered on the main thread. */
object AvatarLoader {
    private val io = Executors.newFixedThreadPool(2)
    private val main = Handler(Looper.getMainLooper())
    private const val MAX_BYTES = 1_500_000

    fun load(url: String?, sizePx: Int, callback: (Bitmap?) -> Unit) {
        if (url == null || !url.startsWith("https://")) { callback(null); return }
        io.execute {
            val bmp = try { fetch(url, sizePx) } catch (e: Exception) { null }
            main.post { callback(bmp) }
        }
    }

    private fun fetch(url: String, size: Int): Bitmap? {
        val conn = URL(url).openConnection() as HttpsURLConnection
        try {
            conn.connectTimeout = 4_000
            conn.readTimeout = 4_000
            conn.instanceFollowRedirects = false
            if (conn.responseCode != 200 || conn.contentLength > MAX_BYTES) return null
            val bytes = conn.inputStream.use { it.readBytes() }
            if (bytes.size > MAX_BYTES) return null
            val src = BitmapFactory.decodeByteArray(bytes, 0, bytes.size) ?: return null
            return circle(src, size)
        } finally {
            conn.disconnect()
        }
    }

    fun circle(src: Bitmap, size: Int): Bitmap {
        val side = minOf(src.width, src.height)
        val square = Bitmap.createBitmap(src, (src.width - side) / 2, (src.height - side) / 2, side, side)
        val scaled = Bitmap.createScaledBitmap(square, size, size, true)
        val out = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
        val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply { shader = BitmapShader(scaled, Shader.TileMode.CLAMP, Shader.TileMode.CLAMP) }
        Canvas(out).drawCircle(size / 2f, size / 2f, size / 2f, paint)
        return out
    }
}
