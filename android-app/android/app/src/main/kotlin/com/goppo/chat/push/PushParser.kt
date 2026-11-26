package com.goppo.chat.push

/** Site-relative paths received in push payloads. Anything else is dropped. Pure Kotlin: unit-tested on the JVM. */
object SitePaths {
    private val CHAT = Regex("^/chat/(\\d{1,12})(?:[/?#].*)?$")

    /** The path if it is a plain site-relative path ("/chat/5"), else null. Never a scheme, host, "//x" or a control character. */
    fun safe(raw: String?): String? {
        if (raw == null || raw.isEmpty() || raw.length > 300) return null
        if (!raw.startsWith("/") || raw.startsWith("//") || raw.contains('\\')) return null
        if (raw.any { it.isISOControl() || it == ' ' }) return null
        return raw
    }

    /** Conversation id of a chat URL path, or null. */
    fun conversationId(path: String?): Long? = path?.let { CHAT.find(it)?.groupValues?.get(1)?.toLongOrNull() }
}

/** A validated push payload. Nothing here is trusted beyond display; a call is always re-checked with the server before Accept. */
sealed class PushMessage {
    data class IncomingCall(
        val callId: String, val video: Boolean, val callerName: String, val callerId: String?, val avatarUrl: String?,
        val conversationId: String?, val path: String?, val expiresAtMs: Long, val notificationId: String?,
    ) : PushMessage()

    data class CallCancelled(val callId: String, val reason: String) : PushMessage()

    data class NewMessage(
        val conversationId: String, val senderName: String, val senderId: String?, val count: Int, val body: String,
        val avatarUrl: String?, val path: String, val ack: String?, val notificationId: String?,
    ) : PushMessage()

    data class MissedCall(val callId: String, val video: Boolean, val callerName: String, val body: String, val path: String?) : PushMessage()

    data class Notice(val body: String, val path: String?, val notificationId: String?) : PushMessage()

    /** Unknown type or invalid payload: ignored (never shown). */
    data class Invalid(val why: String) : PushMessage()
}

object PushParser {
    private val UUID = Regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
    private val DIGITS = Regex("^\\d{1,12}$")
    const val MAX_CALL_WINDOW_MS = 120_000L
    const val DEFAULT_CALL_WINDOW_MS = 60_000L

    private fun text(v: String?, max: Int): String {
        if (v == null) return ""
        return v.filterNot { it.isISOControl() }.trim().take(max)
    }

    private fun https(v: String?): String? = v?.takeIf { it.startsWith("https://") && it.length <= 500 && it.none { c -> c.isISOControl() || c == ' ' } }

    private fun num(v: String?): String? = v?.takeIf { DIGITS.matches(it) }

    fun parse(data: Map<String, String>, nowMs: Long): PushMessage {
        return when (data["type"]) {
            "incoming_call" -> {
                val id = data["call_id"]?.takeIf { UUID.matches(it) } ?: return PushMessage.Invalid("bad call_id")
                val sent = data["sent_at"]?.toLongOrNull() ?: nowMs
                // Never trust a far-future expiry: a call rings for at most two minutes after it arrives.
                val expires = (data["expires_at"]?.toLongOrNull() ?: (sent + DEFAULT_CALL_WINDOW_MS))
                    .coerceAtMost(nowMs + MAX_CALL_WINDOW_MS)
                PushMessage.IncomingCall(
                    callId = id.lowercase(), video = data["call_type"] == "video",
                    callerName = text(data["caller_name"], 80).ifEmpty { "Goppo" }, callerId = num(data["caller_id"]),
                    avatarUrl = https(data["avatar_url"]), conversationId = num(data["conversation_id"]),
                    path = SitePaths.safe(data["path"]), expiresAtMs = expires, notificationId = num(data["notification_id"]),
                )
            }
            "call_cancelled" -> {
                val id = data["call_id"]?.takeIf { UUID.matches(it) } ?: return PushMessage.Invalid("bad call_id")
                PushMessage.CallCancelled(id.lowercase(), text(data["reason"], 24).ifEmpty { "cancelled" })
            }
            "new_message" -> {
                val conv = num(data["conversation_id"]) ?: return PushMessage.Invalid("bad conversation_id")
                PushMessage.NewMessage(
                    conversationId = conv, senderName = text(data["sender_name"], 80).ifEmpty { "Goppo" }, senderId = num(data["sender_id"]),
                    count = (data["count"]?.toIntOrNull() ?: 1).coerceIn(1, 999), body = text(data["body"], 200),
                    avatarUrl = https(data["avatar_url"]), path = SitePaths.safe(data["path"]) ?: "/chat/$conv",
                    ack = data["ack"]?.takeIf { it.length in 10..1000 && it.none { c -> c.isISOControl() || c == ' ' } },
                    notificationId = num(data["notification_id"]),
                )
            }
            "missed_call" -> {
                val id = data["call_id"]?.takeIf { UUID.matches(it) } ?: return PushMessage.Invalid("bad call_id")
                PushMessage.MissedCall(id.lowercase(), data["call_type"] == "video", text(data["caller_name"], 80).ifEmpty { "Goppo" },
                    text(data["body"], 200), SitePaths.safe(data["path"]))
            }
            "announcement" -> PushMessage.Notice(text(data["body"], 200), SitePaths.safe(data["path"]), num(data["notification_id"]))
            else -> PushMessage.Invalid("unknown type")
        }
    }
}
