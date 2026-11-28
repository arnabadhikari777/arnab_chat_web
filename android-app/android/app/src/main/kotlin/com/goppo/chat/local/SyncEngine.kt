package com.goppo.chat.local

import android.content.Context
import com.goppo.chat.net.ApiClient
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.locks.ReentrantLock

/**
 * The synchronisation engine. One pass = (1) send what is waiting in the outbox, (2) tell the server what was read,
 * (3) pull every change after the stored cursor (first run: the whole history, page by page, resumable).
 *
 * Safe to call from anywhere, any number of times: a single lock allows one pass at a time; a call that arrives meanwhile sets
 * a flag and the running pass goes round once more, so nothing queued meanwhile is missed and nothing runs twice in parallel.
 * Sending is idempotent end to end: every message has a client uuid and the server answers a repeat with the same message.
 */
object SyncEngine {
    enum class Outcome { DONE, RETRY, SIGNED_OUT }

    private val lock = ReentrantLock()
    private val again = AtomicBoolean(false)
    @Volatile var onChanged: ((Set<Long>) -> Unit)? = null
    @Volatile var onSignedOut: (() -> Unit)? = null

    fun run(context: Context): Outcome {
        if (!lock.tryLock()) { again.set(true); return Outcome.DONE }
        try {
            var result = Outcome.DONE
            var rounds = 0
            do {
                again.set(false)
                val r = pass(context.applicationContext)
                if (r == Outcome.SIGNED_OUT) { onSignedOut?.invoke(); return r }
                if (r == Outcome.RETRY) result = Outcome.RETRY
            } while (again.get() && ++rounds < 4)
            return result
        } finally { lock.unlock() }
    }

    private fun parse(o: JSONObject): ServerMessage {
        val a = o.optJSONObject("att")
        return ServerMessage(
            id = o.getLong("id"), uuid = o.optString("uuid", ""), conversationId = o.getLong("conv"), senderId = o.optLong("sender"),
            type = o.optString("type", "text"), body = o.optString("body", ""), status = o.optString("status", "sent"),
            createdAt = o.optString("at", ""), deliveredAt = o.optString("delivered_at").ifEmpty { null }, seenAt = o.optString("seen_at").ifEmpty { null },
            deleted = o.optBoolean("deleted"), deletedAt = o.optString("deleted_at").ifEmpty { null }, hidden = o.optBoolean("hidden"),
            rev = o.optLong("rev"), mine = o.optBoolean("mine"),
            att = a?.let {
                ServerAttachment(it.getLong("id"), it.optString("url"), it.optString("name").ifEmpty { null }, if (it.isNull("size")) null else it.optLong("size"),
                    it.optString("mime").ifEmpty { null }, if (it.isNull("dur")) null else it.optDouble("dur"), if (it.isNull("w")) null else it.optInt("w"),
                    if (it.isNull("h")) null else it.optInt("h"), it.optString("thumb").ifEmpty { null })
            },
        )
    }

    private fun pass(context: Context): Outcome {
        val store = Session.store(context) ?: return Outcome.DONE
        store.resetStaleDownloadsOnce()
        var outcome = Outcome.DONE
        val changed = HashSet<Long>()

        // 0. who is in my chat list (names, avatars) - also tells us at once if the session expired
        val chats = ApiClient.json(context, "GET", "/api/chats?limit=500", null)
        if (chats.signedOut || chats.code == 403) return Outcome.SIGNED_OUT
        if (chats.ok) runCatching {
            val items = JSONObject(chats.body).optJSONArray("chats") ?: JSONObject(chats.body).optJSONArray("items") ?: JSONArray()
            for (i in 0 until items.length()) {
                val c = items.getJSONObject(i); val u = c.optJSONObject("user")
                store.upsertConversation(c.getLong("id"), c.optLong("peer_id"), u?.optString("name") ?: "", u?.optString("username") ?: "",
                    u?.optString("avatar")?.ifEmpty { null }, c.optInt("unread"))
            }
        } else outcome = Outcome.RETRY

        // 1. outbox: my waiting messages and deletions
        val f = flushOutbox(context, store, changed)
        if (f == Outcome.SIGNED_OUT) return f
        if (f == Outcome.RETRY) outcome = Outcome.RETRY

        // 2. read receipts I still owe the server
        for ((conv, upto) in store.seenQueue()) {
            val r = ApiClient.json(context, "POST", "/api/conversations/$conv/seen", JSONObject().put("upto", upto).toString())
            if (r.ok) store.clearSeen(conv, upto) else if (r.signedOut) return Outcome.SIGNED_OUT else outcome = Outcome.RETRY
        }

        // 3. pull changes (history restore on the first run, "only what changed" afterwards)
        val p = pull(context, store, changed)
        if (p == Outcome.SIGNED_OUT) return p
        if (p == Outcome.RETRY) outcome = Outcome.RETRY
        if (changed.isNotEmpty()) onChanged?.invoke(changed)
        return outcome
    }

    private fun pull(context: Context, store: LocalStore, changed: MutableSet<Long>): Outcome {
        if (store.phase() == Phase.RESTORING && store.state("restore_head") == null) {
            val h = ApiClient.json(context, "GET", "/api/sync/head", null)
            if (h.signedOut || h.code == 403) return Outcome.SIGNED_OUT
            if (!h.ok) return Outcome.RETRY
            store.setState("restore_head", JSONObject(h.body).optLong("rev").toString())
        }
        val head = store.state("restore_head")?.toLongOrNull() ?: 0L
        var cur = store.cursor()
        var guard = 0
        while (guard++ < 10_000) {
            val r = ApiClient.json(context, "GET", "/api/sync/changes?rev=${cur.rev}&id=${cur.id}&limit=200", null)
            if (r.signedOut || r.code == 403) return Outcome.SIGNED_OUT
            if (!r.ok) return Outcome.RETRY
            val j = runCatching { JSONObject(r.body) }.getOrNull() ?: return Outcome.RETRY
            val msgs = j.optJSONArray("messages") ?: JSONArray()
            val restoring = store.phase() == Phase.RESTORING
            for (i in 0 until msgs.length()) {
                val m = parse(msgs.getJSONObject(i))
                // History = what existed when the restore began. Anything newer is a NEW message, even if it arrives in a restore page.
                val phase = if (restoring && m.rev <= head) Phase.RESTORING else Phase.LIVE
                val res = store.applyServerMessage(m, System.currentTimeMillis())
                changed.add(m.conversationId)
                val att = res.row.let { store.attachmentByUuid(it.uuid) }
                if (att != null && AutoDownloadPolicy.shouldDownload(MediaPrefs.get(context), res.row.type, res.row.mine, phase, res.isNewToThisPhone,
                        res.row.deleted || res.row.hidden, !att.url.isNullOrEmpty(), att.state == "done")) SyncScheduler.enqueueDownload(context, att.mediaId)
            }
            val nx = j.optJSONObject("next")
            if (nx != null) { cur = cur.advancedTo(SyncCursor(nx.optLong("rev"), nx.optLong("id"))); store.setState("cursor_rev", cur.rev.toString()); store.setState("cursor_id", cur.id.toString()) }
            if (!j.optBoolean("has_more")) break
        }
        if (store.phase() == Phase.RESTORING) store.setState("phase", "live")          // restore finished: from now on messages are "new"
        return Outcome.DONE
    }

    private fun flushOutbox(context: Context, store: LocalStore, changed: MutableSet<Long>): Outcome {
        var outcome = Outcome.DONE
        for (item in store.outboxDue(System.currentTimeMillis())) {
            val m = store.message(item.uuid)
            if (m == null) { store.outboxDone(item.id); continue }
            val r: ApiClient.Result = when (item.kind) {
                "send_text" -> { if (m.serverId != null) { store.outboxDone(item.id); continue }
                    store.setPendingStatus(m.uuid, Status.SENDING); changed.add(m.conversationId)
                    ApiClient.json(context, "POST", "/api/conversations/${m.conversationId}/messages", JSONObject().put("uuid", m.uuid).put("body", m.body).toString()) }
                "send_media" -> { if (m.serverId != null) { store.outboxDone(item.id); continue }
                    val file = store.pendingFile(m.uuid)
                    if (file == null || !file.isFile) { store.markFailed(m.uuid, "file missing"); changed.add(m.conversationId); continue }
                    store.setPendingStatus(m.uuid, Status.SENDING); changed.add(m.conversationId)
                    val meta = store.attachment(m.uuid)
                    val fields = HashMap<String, String>().apply {
                        put("uuid", m.uuid); put("caption", m.body)
                        if (m.type == "voice") put("voice", "1")
                        meta?.optDouble("dur")?.takeIf { !it.isNaN() && it > 0 }?.let { put("duration", it.toString()) }
                    }
                    ApiClient.upload(context, "/api/conversations/${m.conversationId}/messages", fields, file, meta?.optString("name").takeUnless { it.isNullOrEmpty() || it == "null" } ?: file.name,
                        meta?.optString("mime").takeUnless { it.isNullOrEmpty() || it == "null" } ?: "application/octet-stream") }
                "delete_everyone" -> ApiClient.json(context, "POST", "/api/messages/${item.serverId}/delete", JSONObject().put("scope", "everyone").toString())
                else -> { store.outboxDone(item.id); continue }
            }
            changed.add(m.conversationId)
            when {
                r.ok -> {
                    val applied = if (item.kind == "delete_everyone") { store.applyLocalTombstone(m.uuid); true }
                        else runCatching { store.applyServerMessage(parse(JSONObject(r.body).getJSONObject("message")), System.currentTimeMillis()) }.isSuccess
                    if (applied) store.outboxDone(item.id) else { store.setPendingStatus(m.uuid, Status.WAITING); outcome = Outcome.RETRY }
                }
                r.code == 401 || (r.code == 403 && item.kind != "delete_everyone") -> { store.setPendingStatus(m.uuid, Status.WAITING); return Outcome.SIGNED_OUT }
                item.kind == "delete_everyone" && (r.code == 404 || r.code == 403) -> { if (r.code == 404) store.applyLocalTombstone(m.uuid) else { store.clearDeletePending(m.uuid); store.outboxDone(item.id) } }
                Backoff.isPermanent(r.code) -> {
                    if (item.kind == "delete_everyone") { store.clearDeletePending(m.uuid); store.outboxDone(item.id) } else store.markFailed(m.uuid, "http ${r.code}")
                }
                else -> {                                  // offline / timeout / server error: stays Waiting, retried with back-off
                    store.setPendingStatus(m.uuid, Status.WAITING)
                    store.outboxRetryLater(item.id, System.currentTimeMillis(), item.attempts + 1, "http ${r.code}")
                    outcome = Outcome.RETRY
                }
            }
        }
        return outcome
    }
}
