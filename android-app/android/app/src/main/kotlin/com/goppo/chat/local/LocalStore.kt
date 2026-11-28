package com.goppo.chat.local

import android.content.ContentValues
import android.content.Context
import android.database.Cursor
import android.database.sqlite.SQLiteDatabase
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

data class OutboxItem(val id: Long, val kind: String, val uuid: String, val serverId: Long?, val attempts: Int)

/** The data-access layer for messages, attachments, conversations, the outbox and the sync cursor of ONE account. */
class LocalStore private constructor(private val context: Context, val userId: Long, private val db: SQLiteDatabase) {

    val mediaDir: File get() = File(context.filesDir, "media/$userId").also { it.mkdirs() }
    val outboxDir: File get() = File(context.filesDir, "outbox/$userId").also { it.mkdirs() }

    // ------------------------------------------------------------------ sync state
    fun state(key: String): String? =
        db.rawQuery("SELECT v FROM sync_state WHERE k = ?", arrayOf(key)).use { if (it.moveToFirst()) it.getString(0) else null }

    fun setState(key: String, value: String) {
        db.execSQL("INSERT OR REPLACE INTO sync_state(k, v) VALUES (?, ?)", arrayOf(key, value))
    }

    fun cursor(): SyncCursor = SyncCursor(state("cursor_rev")?.toLongOrNull() ?: 0L, state("cursor_id")?.toLongOrNull() ?: 0L)

    fun phase(): Phase = if (state("phase") == "live") Phase.LIVE else Phase.RESTORING

    // ------------------------------------------------------------------ messages
    private fun readRow(c: Cursor) = LocalMessage(
        localId = c.getLong(c.getColumnIndexOrThrow("local_id")),
        serverId = c.getColumnIndexOrThrow("server_id").let { if (c.isNull(it)) null else c.getLong(it) },
        uuid = c.getString(c.getColumnIndexOrThrow("uuid")), conversationId = c.getLong(c.getColumnIndexOrThrow("conv_id")),
        senderId = c.getColumnIndexOrThrow("sender_id").let { if (c.isNull(it)) null else c.getLong(it) },
        mine = c.getInt(c.getColumnIndexOrThrow("mine")) == 1, type = c.getString(c.getColumnIndexOrThrow("type")),
        body = c.getString(c.getColumnIndexOrThrow("body")), status = c.getString(c.getColumnIndexOrThrow("status")),
        localCreatedAt = c.getLong(c.getColumnIndexOrThrow("local_created_at")),
        serverAt = c.getString(c.getColumnIndexOrThrow("server_at")), deliveredAt = c.getString(c.getColumnIndexOrThrow("delivered_at")),
        seenAt = c.getString(c.getColumnIndexOrThrow("seen_at")), deleted = c.getInt(c.getColumnIndexOrThrow("deleted")) == 1,
        hidden = c.getInt(c.getColumnIndexOrThrow("hidden")) == 1, rev = c.getLong(c.getColumnIndexOrThrow("rev")),
    )

    private fun byUuid(uuid: String): LocalMessage? =
        db.rawQuery("SELECT * FROM messages WHERE uuid = ?", arrayOf(uuid)).use { if (it.moveToFirst()) readRow(it) else null }

    /**
     * Saves one server message (idempotent: the same message twice = one row). Matches by server id first, then by the
     * client uuid (that is how my own waiting message is confirmed by the server). Everything happens in one transaction.
     */
    fun applyServerMessage(serverMessage: ServerMessage, now: Long): Merge.Result {
        db.beginTransaction()
        try {
            val byServerId = { db.rawQuery("SELECT * FROM messages WHERE server_id = ?", arrayOf(serverMessage.id.toString()))
                .use { if (it.moveToFirst()) readRow(it) else null } }
            // The server only guarantees that a uuid is unique PER SENDER. So the uuid may only identify a message of mine
            // (my own waiting message being confirmed); another person's message is always matched by its server id.
            // Otherwise a peer who reuses my uuid could overwrite my waiting message and its outbox entry.
            val existing = if (serverMessage.mine) (byUuid(serverMessage.uuid)?.takeIf { it.mine } ?: byServerId()) else byServerId()
            val s = if (existing == null && byUuid(serverMessage.uuid) != null) serverMessage.copy(uuid = "${serverMessage.uuid}~${serverMessage.id}") else serverMessage
            val r = Merge.apply(existing, s, now)
            val cv = ContentValues().apply {
                put("server_id", r.row.serverId); put("uuid", r.row.uuid); put("conv_id", r.row.conversationId)
                put("sender_id", r.row.senderId); put("mine", if (r.row.mine) 1 else 0); put("type", r.row.type)
                put("body", r.row.body); put("status", r.row.status); put("local_created_at", r.row.localCreatedAt)
                put("server_at", r.row.serverAt); put("delivered_at", r.row.deliveredAt); put("seen_at", r.row.seenAt)
                put("deleted", if (r.row.deleted) 1 else 0); put("hidden", if (r.row.hidden) 1 else 0); put("rev", r.row.rev)
                if (r.row.deleted) put("delete_pending", 0)
            }
            if (existing == null) db.insertOrThrow("messages", null, cv)
            else db.update("messages", cv, "local_id = ?", arrayOf(existing.localId.toString()))
            if (r.row.deleted || r.row.hidden) removeMediaFor(r.row.uuid)
            else s.att?.let { upsertAttachment(r.row.uuid, it) }
            if (r.row.deleted) db.delete("outbox", "kind = 'delete_everyone' AND message_uuid = ?", arrayOf(r.row.uuid))
            if (r.row.mine && !Status.isPending(r.row.status)) db.delete("outbox", "kind IN ('send_text','send_media') AND message_uuid = ?", arrayOf(r.row.uuid))
            db.setTransactionSuccessful()
            return r
        } finally { db.endTransaction() }
    }

    private fun upsertAttachment(uuid: String, a: ServerAttachment) {
        val cv = ContentValues().apply {
            put("server_att_id", a.id); put("url", a.url); put("thumb_url", a.thumbUrl); put("name", a.name); put("size", a.size)
            put("mime", a.mime); put("duration", a.durationSeconds); put("width", a.width); put("height", a.height)
        }
        val has = db.rawQuery("SELECT 1 FROM attachments WHERE message_uuid = ?", arrayOf(uuid)).use { it.moveToFirst() }
        if (has) db.update("attachments", cv, "message_uuid = ?", arrayOf(uuid))
        else { cv.put("message_uuid", uuid); db.insertOrThrow("attachments", null, cv) }   // state stays 'none': NOT downloaded
    }

    /** A message deleted for everyone: its local media copy goes too (the server original is already gone). */
    private fun removeMediaFor(uuid: String) {
        db.rawQuery("SELECT local_path FROM attachments WHERE message_uuid = ?", arrayOf(uuid)).use {
            if (it.moveToFirst() && !it.isNull(0)) insideMedia(File(it.getString(0)))?.delete()
        }
        db.delete("attachments", "message_uuid = ?", arrayOf(uuid))
    }

    /** Text typed while offline / online: saved at once, shown at once, queued for the server. */
    fun addPendingText(conv: Long, uuid: String, body: String, now: Long): LocalMessage {
        db.beginTransaction()
        try {
            val cv = ContentValues().apply {
                put("uuid", uuid); put("conv_id", conv); put("sender_id", userId); put("mine", 1); put("type", "text")
                put("body", body); put("status", Status.WAITING); put("local_created_at", now)
            }
            if (byUuid(uuid) == null) {
                db.insertOrThrow("messages", null, cv); enqueue("send_text", uuid, null, now)
            }
            db.setTransactionSuccessful()
        } finally { db.endTransaction() }
        return byUuid(uuid)!!
    }

    /** Photo / video / voice / document chosen by me: the file is already copied into [outboxDir]. */
    fun addPendingMedia(conv: Long, uuid: String, type: String, caption: String, file: File, name: String, mime: String, durationSec: Double?, now: Long) {
        db.beginTransaction()
        try {
            if (byUuid(uuid) == null) {
                db.insertOrThrow("messages", null, ContentValues().apply {
                    put("uuid", uuid); put("conv_id", conv); put("sender_id", userId); put("mine", 1); put("type", type)
                    put("body", caption); put("status", Status.WAITING); put("local_created_at", now)
                })
                db.insertOrThrow("attachments", null, ContentValues().apply {
                    put("message_uuid", uuid); put("name", name); put("size", file.length()); put("mime", mime); put("duration", durationSec)
                    put("local_path", file.absolutePath); put("state", "done"); put("downloaded_at", now); put("source", "outbox")
                })
                enqueue("send_media", uuid, null, now)
            }
            db.setTransactionSuccessful()
        } finally { db.endTransaction() }
    }

    fun pendingFile(uuid: String): File? =
        db.rawQuery("SELECT local_path FROM attachments WHERE message_uuid = ? AND source = 'outbox'", arrayOf(uuid))
            .use { if (it.moveToFirst() && !it.isNull(0)) File(it.getString(0)) else null }

    fun message(uuid: String): LocalMessage? = byUuid(uuid)

    fun uuidForServerId(serverId: Long): String? =
        db.rawQuery("SELECT uuid FROM messages WHERE server_id = ?", arrayOf(serverId.toString())).use { if (it.moveToFirst()) it.getString(0) else null }

    /** sending <-> waiting while a send attempt runs; only ever touches a message the server does not have yet. */
    fun setPendingStatus(uuid: String, status: String) {
        db.execSQL("UPDATE messages SET status = ? WHERE uuid = ? AND server_id IS NULL AND status IN ('sending','waiting')", arrayOf(status, uuid))
    }

    /** The server confirmed "delete for everyone" (or the message is already gone there): remove text and media here too. */
    fun applyLocalTombstone(uuid: String) {
        db.beginTransaction()
        try {
            removeMediaFor(uuid)
            db.execSQL("UPDATE messages SET deleted = 1, body = '', delete_pending = 0 WHERE uuid = ?", arrayOf(uuid))
            db.delete("outbox", "message_uuid = ?", arrayOf(uuid))
            db.setTransactionSuccessful()
        } finally { db.endTransaction() }
    }

    fun clearDeletePending(uuid: String) { db.execSQL("UPDATE messages SET delete_pending = 0 WHERE uuid = ?", arrayOf(uuid)) }

    fun body(uuid: String): String? = byUuid(uuid)?.body

    fun markFailed(uuid: String, why: String) {
        db.execSQL("UPDATE messages SET status = ? WHERE uuid = ? AND server_id IS NULL", arrayOf(Status.FAILED, uuid))
        db.execSQL("UPDATE outbox SET last_error = ?, next_attempt_at = 9223372036854775807 WHERE message_uuid = ?", arrayOf(why, uuid))
    }

    /** "Retry" on a failed message: back into the queue. */
    fun retry(uuid: String, now: Long): Boolean {
        val m = byUuid(uuid) ?: return false
        if (m.status != Status.FAILED) return false
        db.execSQL("UPDATE messages SET status = ? WHERE uuid = ?", arrayOf(Status.WAITING, uuid))
        db.execSQL("UPDATE outbox SET attempts = 0, next_attempt_at = 0, last_error = NULL WHERE message_uuid = ?", arrayOf(uuid))
        return true
    }

    /** Discards an UNSENT message (the person chose to). Never touches a message the server has. */
    fun discardUnsent(uuid: String): Boolean {
        val m = byUuid(uuid) ?: return false
        if (m.serverId != null) return false
        removeMediaFor(uuid)
        db.delete("messages", "uuid = ?", arrayOf(uuid)); db.delete("outbox", "message_uuid = ?", arrayOf(uuid))
        return true
    }

    /** Delete for everyone: queued, shown as "deleting" until the server confirms. */
    fun requestDeleteEveryone(uuid: String, now: Long): Boolean {
        val m = byUuid(uuid) ?: return false
        if (!m.mine || m.serverId == null || m.deleted) return false
        db.execSQL("UPDATE messages SET delete_pending = 1 WHERE uuid = ?", arrayOf(uuid))
        enqueue("delete_everyone", uuid, m.serverId, now)
        return true
    }

    /** Delete for me (server first; the row is hidden locally only after the server has it, or at once when offline queue is not needed). */
    fun hideLocally(uuid: String) {
        removeMediaFor(uuid)
        db.execSQL("UPDATE messages SET hidden = 1, body = '' WHERE uuid = ?", arrayOf(uuid))
    }

    fun markSeenLocal(conv: Long, upto: Long) {
        db.execSQL("UPDATE conversations SET unread = 0 WHERE id = ?", arrayOf(conv))
        db.execSQL("INSERT OR REPLACE INTO seen_queue(conv_id, upto) VALUES (?, MAX(?, COALESCE((SELECT upto FROM seen_queue WHERE conv_id = ?), 0)))", arrayOf(conv, upto, conv))
    }

    fun seenQueue(): List<Pair<Long, Long>> =
        db.rawQuery("SELECT conv_id, upto FROM seen_queue", null).use { c -> buildList { while (c.moveToNext()) add(c.getLong(0) to c.getLong(1)) } }

    fun clearSeen(conv: Long, upto: Long) { db.execSQL("DELETE FROM seen_queue WHERE conv_id = ? AND upto <= ?", arrayOf(conv, upto)) }

    // ------------------------------------------------------------------ outbox
    private fun enqueue(kind: String, uuid: String, serverId: Long?, now: Long) {
        db.execSQL("INSERT OR IGNORE INTO outbox(kind, message_uuid, server_id, created_at) VALUES (?, ?, ?, ?)", arrayOf(kind, uuid, serverId, now))
    }

    fun outboxDue(now: Long): List<OutboxItem> =
        db.rawQuery("SELECT id, kind, message_uuid, server_id, attempts FROM outbox WHERE next_attempt_at <= ? ORDER BY id", arrayOf(now.toString())).use { c ->
            buildList { while (c.moveToNext()) add(OutboxItem(c.getLong(0), c.getString(1), c.getString(2), if (c.isNull(3)) null else c.getLong(3), c.getInt(4))) }
        }

    fun outboxCount(): Int = db.rawQuery("SELECT COUNT(*) FROM outbox WHERE next_attempt_at < 9223372036854775807", null).use { it.moveToFirst(); it.getInt(0) }

    fun outboxDone(id: Long) { db.delete("outbox", "id = ?", arrayOf(id.toString())) }

    fun outboxRetryLater(id: Long, now: Long, attempts: Int, error: String) {
        db.execSQL("UPDATE outbox SET attempts = ?, next_attempt_at = ?, last_error = ? WHERE id = ?",
            arrayOf(attempts, now + Backoff.seconds(attempts) * 1000, error.take(120), id))
    }

    /** Wake every waiting item (network is back): the connectivity change is a reason to try now, not after the back-off. */
    fun outboxWakeAll() { db.execSQL("UPDATE outbox SET next_attempt_at = 0 WHERE next_attempt_at < 9223372036854775807") }

    // ------------------------------------------------------------------ conversations
    fun upsertConversation(id: Long, peerId: Long, name: String, username: String, avatar: String?, unread: Int) {
        db.execSQL("INSERT OR IGNORE INTO conversations(id) VALUES (?)", arrayOf(id))
        db.execSQL("UPDATE conversations SET peer_id = ?, peer_name = ?, peer_username = ?, peer_avatar = ?, unread = ? WHERE id = ?",
            arrayOf(peerId, name, username, avatar, unread, id))
    }

    /** Chat list for the inbox, newest first, built from LOCAL data only (works offline). */
    fun chatList(): JSONArray {
        val out = JSONArray()
        db.rawQuery("SELECT id, peer_id, peer_name, peer_username, peer_avatar, unread FROM conversations", null).use { c ->
            while (c.moveToNext()) {
                val id = c.getLong(0)
                val last = lastMessage(id)
                val o = JSONObject().put("id", id).put("peer_id", c.getLong(1)).put("unread", c.getInt(5))
                    .put("user", JSONObject().put("name", c.getString(2) ?: "").put("username", c.getString(3) ?: "")
                        .put("avatar", c.getString(4) ?: JSONObject.NULL).put("online", false).put("seed", (c.getLong(1) % 5).toInt()))
                o.put("last", if (last == null) JSONObject.NULL else previewJson(last))
                o.put("_sort", last?.serverId ?: last?.localCreatedAt ?: 0L)
                out.put(o)
            }
        }
        val list = (0 until out.length()).map { out.getJSONObject(it) }.sortedByDescending { it.optLong("_sort") }
        return JSONArray().also { arr -> list.forEach { it.remove("_sort"); arr.put(it) } }
    }

    private fun lastMessage(conv: Long): LocalMessage? =
        db.rawQuery("SELECT * FROM messages WHERE conv_id = ? AND hidden = 0 ORDER BY COALESCE(server_id, 9223372036854775807) DESC, local_created_at DESC LIMIT 1",
            arrayOf(conv.toString())).use { if (it.moveToFirst()) readRow(it) else null }

    private fun previewJson(m: LocalMessage) = JSONObject().put("type", m.type).put("body", if (m.deleted) "" else m.body.take(100))
        .put("deleted", m.deleted).put("mine", m.mine).put("status", wireStatus(m)).put("at", m.serverAt ?: iso(m.localCreatedAt))

    // ------------------------------------------------------------------ reading for the page
    private fun iso(ms: Long) = java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", java.util.Locale.US)
        .apply { timeZone = java.util.TimeZone.getTimeZone("UTC") }.format(java.util.Date(ms))

    private fun wireStatus(m: LocalMessage) = if (m.status == Status.WAITING && false) Status.SENDING else m.status

    /** Newest [limit] messages (older than [beforeKey] if given), oldest first. The key is the server id (or +inf for unsent). */
    fun page(conv: Long, beforeServerId: Long?, limit: Int): Pair<JSONArray, Boolean> {
        val rows = ArrayList<LocalMessage>()
        val sql = "SELECT * FROM messages WHERE conv_id = ? AND hidden = 0" + (if (beforeServerId != null) " AND server_id < ?" else "") +
            " ORDER BY COALESCE(server_id, 9223372036854775807) DESC, local_created_at DESC LIMIT ?"
        val args = buildList { add(conv.toString()); if (beforeServerId != null) add(beforeServerId.toString()); add((limit + 1).toString()) }
        db.rawQuery(sql, args.toTypedArray()).use { while (it.moveToNext()) rows.add(readRow(it)) }
        val more = rows.size > limit
        val arr = JSONArray()
        rows.take(limit).sortedWith(Merge.ORDER).forEach { arr.put(toJson(it)) }
        return arr to more
    }

    /** Messages that changed or arrived after [afterRev] (what the page polls instead of the network). */
    fun changedSince(conv: Long, afterRev: Long): JSONArray {
        val arr = JSONArray()
        db.rawQuery("SELECT * FROM messages WHERE conv_id = ? AND (rev > ? OR server_id IS NULL OR delete_pending = 1) AND hidden = 0 ORDER BY rev, local_created_at",
            arrayOf(conv.toString(), afterRev.toString())).use { while (it.moveToNext()) arr.put(toJson(readRow(it))) }
        return arr
    }

    fun maxRev(conv: Long): Long = db.rawQuery("SELECT COALESCE(MAX(rev),0) FROM messages WHERE conv_id = ?", arrayOf(conv.toString())).use { it.moveToFirst(); it.getLong(0) }

    fun attachment(uuid: String): JSONObject? =
        db.rawQuery("SELECT media_id, server_att_id, url, thumb_url, name, size, mime, duration, width, height, local_path, state FROM attachments WHERE message_uuid = ?",
            arrayOf(uuid)).use { c ->
            if (!c.moveToFirst()) return null
            val local = !c.isNull(10) && c.getString(11) == "done" && File(c.getString(10)).isFile
            JSONObject().put("media_id", c.getLong(0)).put("id", if (c.isNull(1)) JSONObject.NULL else c.getLong(1))
                .put("url", c.getString(2) ?: "").put("thumb", c.getString(3) ?: JSONObject.NULL).put("name", c.getString(4) ?: JSONObject.NULL)
                .put("size", if (c.isNull(5)) JSONObject.NULL else c.getLong(5)).put("mime", c.getString(6) ?: JSONObject.NULL)
                .put("dur", if (c.isNull(7)) JSONObject.NULL else c.getDouble(7)).put("w", if (c.isNull(8)) JSONObject.NULL else c.getInt(8))
                .put("h", if (c.isNull(9)) JSONObject.NULL else c.getInt(9))
                .put("downloaded", local).put("state", c.getString(11))
                .put("local", if (local) "/__goppo_media__/${c.getLong(0)}" else JSONObject.NULL)
        }

    fun toJson(m: LocalMessage): JSONObject {
        val pendingDelete = db.rawQuery("SELECT delete_pending FROM messages WHERE uuid = ?", arrayOf(m.uuid)).use { it.moveToFirst() && it.getInt(0) == 1 }
        return JSONObject().put("id", m.serverId ?: JSONObject.NULL).put("uuid", m.uuid).put("type", m.type)
            .put("body", if (m.deleted) "" else m.body).put("mine", m.mine).put("status", wireStatus(m))
            .put("at", m.serverAt ?: iso(m.localCreatedAt)).put("local_at", iso(m.localCreatedAt)).put("deleted", m.deleted)
            .put("delete_pending", pendingDelete).put("rev", m.rev).put("pending", Status.isPending(m.status))
            .put("att", if (m.deleted) JSONObject.NULL else (attachment(m.uuid) ?: JSONObject.NULL))
    }

    // ------------------------------------------------------------------ media (see MediaManager for the downloads)
    fun attachmentRow(mediaId: Long): AttachmentRow? =
        db.rawQuery("SELECT media_id, message_uuid, url, size, local_path, state FROM attachments WHERE media_id = ?", arrayOf(mediaId.toString())).use { c ->
            if (!c.moveToFirst()) null else AttachmentRow(c.getLong(0), c.getString(1), c.getString(2), if (c.isNull(3)) null else c.getLong(3),
                if (c.isNull(4)) null else c.getString(4), c.getString(5)).let { r ->
                    val type = db.rawQuery("SELECT type FROM messages WHERE uuid = ?", arrayOf(r.uuid)).use { t -> if (t.moveToFirst()) t.getString(0) else "" }
                    r.copy(type = type)
                }
        }

    fun attachmentByUuid(uuid: String): AttachmentRow? =
        db.rawQuery("SELECT media_id FROM attachments WHERE message_uuid = ?", arrayOf(uuid)).use { if (it.moveToFirst()) attachmentRow(it.getLong(0)) else null }

    fun setMediaState(mediaId: Long, state: String, path: String?, now: Long?) {
        db.execSQL("UPDATE attachments SET state = ?, local_path = ?, downloaded_at = ? WHERE media_id = ?", arrayOf(state, path, now, mediaId))
    }

    /** Local copies only: server originals are never touched. Returns the number of bytes freed. */
    fun clearDownloadedMedia(): Long {
        var freed = 0L
        db.rawQuery("SELECT media_id, local_path FROM attachments WHERE source = 'remote' AND local_path IS NOT NULL", null).use { c ->
            while (c.moveToNext()) {
                val f = insideMedia(File(c.getString(1)))
                if (f != null && f.isFile) { freed += f.length(); f.delete() }
                db.execSQL("UPDATE attachments SET state = 'none', local_path = NULL, downloaded_at = NULL WHERE media_id = ?", arrayOf(c.getLong(0)))
            }
        }
        mediaDir.listFiles()?.filter { it.name.endsWith(".part") }?.forEach { freed += it.length(); it.delete() }   // interrupted downloads
        return freed
    }

    fun mediaUsageBytes(): Long = db.rawQuery("SELECT COALESCE(SUM(size),0) FROM attachments WHERE source = 'remote' AND state = 'done'", null)
        .use { it.moveToFirst(); it.getLong(0) }

    /** A path only counts if it really is inside this account's media folder (no path tricks via the database). */
    fun insideMedia(f: File): File? {
        val base = mediaDir.canonicalFile; val c = runCatching { f.canonicalFile }.getOrNull() ?: return null
        return if (c.path.startsWith(base.path + File.separator)) c else null
    }

    @Volatile private var staleChecked = false
    /** Once per process: a download the previous process was killed in is not "downloading" any more. */
    fun resetStaleDownloadsOnce() { if (!staleChecked) { staleChecked = true; resetStaleDownloads() } }

    /** Files of downloads that were interrupted by a process death: state 'downloading' with no live worker -> back to 'none'. */
    fun resetStaleDownloads() {
        db.execSQL("UPDATE attachments SET state = 'none' WHERE state = 'downloading'")
        mediaDir.listFiles()?.filter { it.name.endsWith(".part") }?.forEach { it.delete() }
    }

    fun counts(): JSONObject = JSONObject().put("messages", db.rawQuery("SELECT COUNT(*) FROM messages", null).use { it.moveToFirst(); it.getInt(0) })
        .put("outbox", outboxCount())

    companion object {
        fun forUser(context: Context, userId: Long): LocalStore = LocalStore(context.applicationContext, userId, LocalDb.forUser(context, userId).writableDatabase)
    }
}

data class AttachmentRow(val mediaId: Long, val uuid: String, val url: String?, val size: Long?, val localPath: String?, val state: String, val type: String = "")
