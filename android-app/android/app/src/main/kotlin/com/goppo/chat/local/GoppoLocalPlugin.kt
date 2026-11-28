package com.goppo.chat.local

import android.util.Base64
import com.getcapacitor.JSArray
import com.getcapacitor.JSObject
import com.getcapacitor.Plugin
import com.getcapacitor.PluginCall
import com.getcapacitor.PluginMethod
import com.getcapacitor.annotation.CapacitorPlugin
import com.goppo.chat.net.ApiClient
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream

/**
 * Capacitor plugin "GoppoLocal": what the bundled pages use instead of the network - the local database, the outbox, the
 * media downloads and the settings. Arguments are validated; nothing here returns a path of the phone's file system to the
 * page: media is addressed by id and served by the WebView client from the account's own media folder only.
 * (Plugin methods run on Capacitor's background thread, never the main thread.)
 */
@CapacitorPlugin(name = "GoppoLocal")
class GoppoLocalPlugin : Plugin() {
    private val UUID = Regex("^[A-Za-z0-9_-]{8,64}$")
    private val HANDLE = Regex("^[a-f0-9]{32}$")
    private val TYPES = setOf("image", "video", "voice", "document")
    private val MAX_UPLOAD = 64L * 1024 * 1024

    override fun load() {
        SyncEngine.onChanged = { convs -> notifyListeners("localChanged", JSObject().put("convs", JSArray().also { a -> convs.forEach { a.put(it) } })) }
        SyncEngine.onSignedOut = { notifyListeners("signedOut", JSObject()) }
        MediaManager.listener = object : MediaManager.Listener {
            override fun changed(mediaId: Long, uuid: String, state: String) {
                notifyListeners("mediaChanged", JSObject().put("media_id", mediaId).put("uuid", uuid).put("state", state))
            }
        }
        SyncScheduler.observeNetwork(context); SyncScheduler.schedulePeriodic(context)
    }

    private fun store(call: PluginCall): LocalStore? = Session.store(context) ?: run { call.reject("no_session"); null }
    private fun long(call: PluginCall, k: String): Long? = call.data.optLong(k, Long.MIN_VALUE).takeIf { it != Long.MIN_VALUE }
    private fun nudge() { SyncScheduler.enqueueNow(context); Thread { runCatching { SyncEngine.run(context) } }.start() }   // instant when online; the lock makes double runs harmless

    // ------------------------------------------------------------------ session
    /** After a successful /api/auth/login (or when /api/auth/me says someone is signed in). */
    @PluginMethod
    fun sessionBegin(call: PluginCall) {
        val uid = long(call, "userId")?.takeIf { it > 0 } ?: return call.reject("bad_user")
        val prev = Session.userId(context)
        if (prev != null && prev != uid) LocalDb.wipe(context, prev)          // another account signs in on this phone: the old local copy goes
        Session.begin(context, uid)
        LocalStore.forUser(context, uid)
        nudge()
        call.resolve(JSObject().put("userId", uid))
    }

    @PluginMethod
    fun sessionInfo(call: PluginCall) {
        val uid = Session.userId(context) ?: return call.resolve(JSObject().put("userId", JSONObject.NULL))
        val s = LocalStore.forUser(context, uid)
        call.resolve(JSObject().put("userId", uid).put("phase", s.phase().name.lowercase()).put("counts", s.counts()))
    }

    /** Logout. Unsent messages would be lost with the local copy, so the page must confirm first (force=true). Server history is never touched. */
    @PluginMethod
    fun sessionEnd(call: PluginCall) {
        val uid = Session.userId(context) ?: return call.resolve(JSObject().put("ended", true).put("pending", 0))
        val pending = LocalStore.forUser(context, uid).outboxCount()
        if (pending > 0 && !call.getBoolean("force", false)!!) return call.resolve(JSObject().put("ended", false).put("pending", pending))
        Session.end(context)
        LocalDb.wipe(context, uid)
        ApiClient.forgetCsrf()
        call.resolve(JSObject().put("ended", true).put("pending", pending))
    }

    // ------------------------------------------------------------------ network bridge for the bundled pages
    @PluginMethod
    fun http(call: PluginCall) {
        val method = (call.getString("method") ?: "GET").uppercase()
        val path = call.getString("path") ?: return call.reject("bad_path")
        if (method !in setOf("GET", "POST", "PATCH", "DELETE") || !ApiClient.allowedPath(path)) return call.reject("not_allowed")
        val r = ApiClient.json(context, method, path, call.data.opt("json")?.takeIf { it !== JSONObject.NULL }?.toString())
        call.resolve(JSObject().put("code", r.code).put("body", r.body))
    }

    @PluginMethod
    fun syncNow(call: PluginCall) { nudge(); call.resolve() }

    // ------------------------------------------------------------------ reading
    @PluginMethod
    fun chatList(call: PluginCall) { val s = store(call) ?: return; call.resolve(JSObject().put("chats", JSArray(s.chatList().toString()))) }

    @PluginMethod
    fun messages(call: PluginCall) {
        val s = store(call) ?: return
        val one = call.getString("uuid")                                   // a single message (conv is then ignored)
        if (one != null) { val m = s.message(one); return call.resolve(JSObject().put("messages", JSArray().also { a -> if (m != null) a.put(JSObject(s.toJson(m).toString())) }).put("more", false).put("rev", 0)) }
        val conv = long(call, "conv") ?: return call.reject("bad_conv")
        val (arr, more) = s.page(conv, long(call, "before"), (call.getInt("limit") ?: 30).coerceIn(1, 100))
        call.resolve(JSObject().put("messages", JSArray(arr.toString())).put("more", more).put("rev", s.maxRev(conv)))
    }

    @PluginMethod
    fun changes(call: PluginCall) {
        val s = store(call) ?: return; val conv = long(call, "conv") ?: return call.reject("bad_conv")
        call.resolve(JSObject().put("messages", JSArray(s.changedSince(conv, long(call, "rev") ?: 0L).toString())).put("rev", s.maxRev(conv)))
    }

    // ------------------------------------------------------------------ sending (local first)
    @PluginMethod
    fun sendText(call: PluginCall) {
        val s = store(call) ?: return
        val conv = long(call, "conv") ?: return call.reject("bad_conv")
        val uuid = call.getString("uuid") ?: ""; val body = (call.getString("body") ?: "").trim()
        if (!UUID.matches(uuid) || body.isEmpty() || body.length > 4000) return call.reject("bad_message")
        var m = s.addPendingText(conv, uuid, body, System.currentTimeMillis())       // visible at once, status WAITING until the server accepts
        if (m.status == Status.FAILED && s.retry(uuid, System.currentTimeMillis())) m = s.message(uuid)!!   // tap on a failed message = try again, same uuid
        nudge()
        call.resolve(JSObject().put("message", JSObject(s.toJson(m).toString())))
    }

    @PluginMethod
    fun uploadChunk(call: PluginCall) {
        val s = store(call) ?: return
        val handle = call.getString("handle") ?: ""; if (!HANDLE.matches(handle)) return call.reject("bad_handle")
        val bytes = try { Base64.decode(call.getString("data") ?: "", Base64.DEFAULT) } catch (e: IllegalArgumentException) { return call.reject("bad_data") }
        val f = File(s.outboxDir, "$handle.tmp")
        if (f.length() + bytes.size > MAX_UPLOAD) { f.delete(); return call.reject("too_large") }
        FileOutputStream(f, call.getBoolean("first", false) != true).use { it.write(bytes) }
        call.resolve(JSObject().put("size", f.length()))
    }

    @PluginMethod
    fun sendMedia(call: PluginCall) {
        val s = store(call) ?: return
        val conv = long(call, "conv") ?: return call.reject("bad_conv")
        val uuid = call.getString("uuid") ?: ""; val handle = call.getString("handle") ?: ""
        val type = call.getString("type") ?: ""
        if (!UUID.matches(uuid) || !HANDLE.matches(handle) || type !in TYPES) return call.reject("bad_message")
        val tmp = File(s.outboxDir, "$handle.tmp"); if (!tmp.isFile || tmp.length() == 0L) return call.reject("no_file")
        val dest = File(s.outboxDir, uuid); if (dest.exists()) dest.delete()
        if (!tmp.renameTo(dest)) return call.reject("io")
        val name = (call.getString("name") ?: "file").replace(Regex("[\\r\\n\"/\\\\]"), "_").take(120)
        s.addPendingMedia(conv, uuid, type, (call.getString("caption") ?: "").take(1000), dest, name,
            (call.getString("mime") ?: "application/octet-stream").take(100), call.data.optDouble("duration").takeIf { !it.isNaN() && it > 0 }, System.currentTimeMillis())
        nudge()
        call.resolve(JSObject().put("message", JSObject(s.toJson(s.message(uuid)!!).toString())))
    }

    @PluginMethod
    fun retry(call: PluginCall) { val s = store(call) ?: return; val ok = s.retry(call.getString("uuid") ?: "", System.currentTimeMillis()); if (ok) nudge(); call.resolve(JSObject().put("ok", ok)) }

    @PluginMethod
    fun discard(call: PluginCall) { val s = store(call) ?: return; call.resolve(JSObject().put("ok", s.discardUnsent(call.getString("uuid") ?: ""))) }

    @PluginMethod
    fun markSeen(call: PluginCall) {
        val s = store(call) ?: return; val conv = long(call, "conv") ?: return call.reject("bad_conv")
        s.markSeenLocal(conv, long(call, "upto") ?: 0L); nudge(); call.resolve()
    }

    /** Delete for everyone: queued in the outbox, shown as "deleting" until the SERVER confirms, retried until it does. */
    @PluginMethod
    fun deleteEveryone(call: PluginCall) {
        val s = store(call) ?: return
        val uuid = call.getString("uuid") ?: long(call, "serverId")?.let { s.uuidForServerId(it) } ?: ""
        val ok = s.requestDeleteEveryone(uuid, System.currentTimeMillis()); if (ok) nudge()
        call.resolve(JSObject().put("ok", ok))
    }

    /** Delete for me needs the server too (so a second phone follows): the page calls it through http(); then the local row is hidden. */
    @PluginMethod
    fun hideLocal(call: PluginCall) {
        val s = store(call) ?: return
        s.hideLocally(call.getString("uuid") ?: long(call, "serverId")?.let { s.uuidForServerId(it) } ?: ""); call.resolve()
    }

    // ------------------------------------------------------------------ media
    @PluginMethod
    fun downloadMedia(call: PluginCall) {
        val s = store(call) ?: return; val id = long(call, "mediaId") ?: return call.reject("bad_media")
        call.resolve(JSObject().put("state", MediaManager.download(context, s, id)))            // the page asked: only then is OLD media fetched
    }

    @PluginMethod
    fun getMediaSettings(call: PluginCall) {
        val m = MediaPrefs.get(context); val s = Session.store(context)
        call.resolve(JSObject().put("autoDownload", m.autoDownload).put("photos", m.photos).put("videos", m.videos).put("usageBytes", s?.mediaUsageBytes() ?: 0L))
    }

    @PluginMethod
    fun setMediaSettings(call: PluginCall) {
        val cur = MediaPrefs.get(context)
        MediaPrefs.set(context, MediaSettings(call.getBoolean("autoDownload", cur.autoDownload)!!, call.getBoolean("photos", cur.photos)!!, call.getBoolean("videos", cur.videos)!!))
        getMediaSettings(call)
    }

    /** Deletes the local COPIES only. The server's originals (Cloudinary) and every message stay; the media can be downloaded again. */
    @PluginMethod
    fun clearMedia(call: PluginCall) {
        val s = store(call) ?: return
        call.resolve(JSObject().put("freedBytes", s.clearDownloadedMedia()))
        notifyListeners("localChanged", JSObject())
    }
}
