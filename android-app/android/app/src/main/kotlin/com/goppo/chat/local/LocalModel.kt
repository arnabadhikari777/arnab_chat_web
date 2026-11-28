package com.goppo.chat.local

/**
 * Pure Kotlin rules of the local-first message store (no Android classes, so they are unit-tested on the JVM).
 * The server is the authority for history; these rules only decide how a server record is merged into a local row.
 */
object Status {
    const val SENDING = "sending"     // being sent right now
    const val WAITING = "waiting"     // saved locally, no connection / server not reached yet: stays in the outbox
    const val SENT = "sent"           // the SERVER accepted it (never shown before that)
    const val DELIVERED = "delivered"
    const val SEEN = "seen"
    const val FAILED = "failed"       // the server refused it for good (4xx); the person can retry or delete

    private val ORDER = mapOf(SENT to 1, DELIVERED to 2, SEEN to 3)

    /** Pending = not yet accepted by the server. */
    fun isPending(s: String) = s == SENDING || s == WAITING

    /** A server-confirmed status only ever moves forward; a stale page can never turn a blue tick grey. */
    fun forward(current: String, incoming: String): String {
        val c = ORDER[current] ?: 0
        val i = ORDER[incoming] ?: return if (c > 0) current else incoming
        return if (i >= c) incoming else current
    }
}

/** A message as the server describes it (one element of /api/sync/changes). */
data class ServerMessage(
    val id: Long, val uuid: String, val conversationId: Long, val senderId: Long, val type: String, val body: String,
    val status: String, val createdAt: String, val deliveredAt: String?, val seenAt: String?,
    val deleted: Boolean, val deletedAt: String?, val hidden: Boolean, val rev: Long,
    val mine: Boolean, val att: ServerAttachment?,
)

data class ServerAttachment(
    val id: Long, val url: String, val name: String?, val size: Long?, val mime: String?, val durationSeconds: Double?,
    val width: Int?, val height: Int?, val thumbUrl: String?,
)

/** The row kept in SQLite. */
data class LocalMessage(
    val localId: Long, val serverId: Long?, val uuid: String, val conversationId: Long, val senderId: Long?, val mine: Boolean,
    val type: String, val body: String, val status: String, val localCreatedAt: Long, val serverAt: String?,
    val deliveredAt: String?, val seenAt: String?, val deleted: Boolean, val hidden: Boolean, val rev: Long,
)

/** Which part of the history the app is in: restoring OLD messages, or following NEW ones. */
enum class Phase { RESTORING, LIVE }

data class SyncCursor(val rev: Long, val id: Long) {
    fun advancedTo(other: SyncCursor): SyncCursor =
        if (other.rev > rev || (other.rev == rev && other.id > id)) other else this
}

object Merge {
    /**
     * Result of merging one server message into the local copy (null = no local copy yet).
     * [isNewToThisPhone] is true only when the message did not exist locally before: that is what lets media
     * auto-download tell a NEW incoming photo from an old one that history restore brought in.
     */
    data class Result(val row: LocalMessage, val isNewToThisPhone: Boolean, val tombstoned: Boolean)

    fun apply(existing: LocalMessage?, s: ServerMessage, nowLocal: Long): Result {
        val deleted = s.deleted || (existing?.deleted == true)          // a tombstone is sticky: nothing resurrects text
        val hidden = s.hidden
        val status = when {
            existing == null || Status.isPending(existing.status) || existing.status == Status.FAILED -> s.status
            else -> Status.forward(existing.status, s.status)
        }
        val row = LocalMessage(
            localId = existing?.localId ?: 0L, serverId = s.id, uuid = s.uuid, conversationId = s.conversationId,
            senderId = s.senderId, mine = s.mine, type = s.type,
            body = if (deleted || s.hidden) "" else s.body,
            status = status, localCreatedAt = existing?.localCreatedAt ?: nowLocal, serverAt = s.createdAt,
            deliveredAt = s.deliveredAt ?: existing?.deliveredAt, seenAt = s.seenAt ?: existing?.seenAt,
            deleted = deleted, hidden = hidden, rev = maxOf(s.rev, existing?.rev ?: 0L),
        )
        return Result(row, isNewToThisPhone = existing == null, tombstoned = deleted && existing?.deleted != true)
    }

    /** Local order: server order (id) first, then messages not yet accepted, by when they were written. */
    val ORDER = Comparator<LocalMessage> { a, b ->
        val ka = a.serverId ?: Long.MAX_VALUE
        val kb = b.serverId ?: Long.MAX_VALUE
        if (ka != kb) ka.compareTo(kb) else a.localCreatedAt.compareTo(b.localCreatedAt)
    }
}
