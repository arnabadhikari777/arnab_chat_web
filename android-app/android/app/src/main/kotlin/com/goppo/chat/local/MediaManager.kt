package com.goppo.chat.local

import android.content.Context
import android.os.StatFs
import com.goppo.chat.net.ApiClient
import java.io.File
import java.util.concurrent.ConcurrentHashMap

/** The receiver's media choices. Stored in private SharedPreferences; survive restarts; never sent to the server or the sender. */
object MediaPrefs {
    private const val P = "goppo_media_prefs"
    fun get(context: Context): MediaSettings {
        val p = context.applicationContext.getSharedPreferences(P, Context.MODE_PRIVATE)
        return MediaSettings(p.getBoolean("auto", true), p.getBoolean("photos", true), p.getBoolean("videos", true))
    }
    fun set(context: Context, s: MediaSettings) {
        context.applicationContext.getSharedPreferences(P, Context.MODE_PRIVATE).edit()
            .putBoolean("auto", s.autoDownload).putBoolean("photos", s.photos).putBoolean("videos", s.videos).apply()
    }
}

/**
 * Downloads a photo / video from the server's media URL into app-private storage - for NEW media (automatic, decided by
 * [AutoDownloadPolicy]) and for OLD media (only when the person asks). One download per media id at a time; a valid local
 * copy is never fetched again; a file only becomes "done" after size + content checks; leftovers are deleted.
 */
object MediaManager {
    private const val MAX_BYTES = 120L * 1024 * 1024
    private val running = ConcurrentHashMap<Long, Boolean>()

    interface Listener { fun changed(mediaId: Long, uuid: String, state: String) }
    @Volatile var listener: Listener? = null

    /** @return the final state: "done", "failed", "downloading" (already running) or "none" (nothing to do). */
    fun download(context: Context, store: LocalStore, mediaId: Long): String {
        val row = store.attachmentRow(mediaId) ?: return "none"
        val local = row.localPath?.let { store.insideMedia(File(it)) }
        if (row.state == "done" && local != null && local.isFile) return "done"          // valid copy: never download twice
        val url = row.url
        if (url.isNullOrEmpty() || row.type !in listOf("image", "video")) return "none"
        if (running.putIfAbsent(mediaId, true) != null) return "downloading"
        try {
            val dir = store.mediaDir
            val free = StatFs(dir.path).availableBytes
            if (free < (row.size ?: 0L) + 20L * 1024 * 1024) { fail(store, row, "storage"); return "failed" }   // storage exhaustion
            store.setMediaState(mediaId, "downloading", null, null); listener?.changed(mediaId, row.uuid, "downloading")
            val part = File(dir, "m$mediaId.part"); val dest = File(dir, "m$mediaId" + extFor(url))
            part.delete()
            val r = ApiClient.download(url, part, MAX_BYTES) { false }
            if (r.error != null) { part.delete(); fail(store, row, r.error); return "failed" }          // interrupted / refused: nothing marked done
            val head = ByteArray(16).also { b -> part.inputStream().use { it.read(b) } }
            if (!DownloadCheck.valid(row.type, row.size, part.length(), head)) { part.delete(); fail(store, row, "corrupt"); return "failed" }
            if (dest.exists()) dest.delete()
            if (!part.renameTo(dest)) { part.delete(); fail(store, row, "rename"); return "failed" }
            store.setMediaState(mediaId, "done", dest.absolutePath, System.currentTimeMillis())
            listener?.changed(mediaId, row.uuid, "done")
            return "done"
        } catch (e: Exception) {
            File(store.mediaDir, "m$mediaId.part").delete(); fail(store, row, e.javaClass.simpleName); return "failed"
        } finally { running.remove(mediaId) }
    }

    private fun fail(store: LocalStore, row: AttachmentRow, why: String) {
        store.setMediaState(row.mediaId, "failed", null, null); listener?.changed(row.mediaId, row.uuid, "failed")
    }

    private fun extFor(url: String): String {
        val e = url.substringBefore('?').substringAfterLast('.', "").lowercase()
        return if (e.length in 2..4 && e.all { it.isLetterOrDigit() }) ".$e" else ".bin"
    }
}
