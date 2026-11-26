package com.goppo.chat.calls

enum class CallPhase { RINGING, ACCEPTING, ACTIVE, ENDED }
enum class CallDirection { INCOMING, OUTGOING }

data class CallInfo(
    val callId: String, val video: Boolean, val peerName: String, val peerId: String?, val avatarUrl: String?,
    val conversationId: String?, val path: String?, val direction: CallDirection,
)

data class CallSession(
    val info: CallInfo, val phase: CallPhase, val expiresAtMs: Long = 0L, val connectedAtMs: Long = 0L, val muted: Boolean = false,
)

/** What to do with a call announcement. */
enum class IncomingDecision {
    /** Ring. */
    NEW,

    /** The same call arrived again (push + page poll): ignore. */
    DUPLICATE,

    /** Another call is ringing/active: do not interrupt it (the website tells the caller "busy"). */
    BUSY,

    /** Its ring time is over: never show it. */
    EXPIRED,

    /** This call already ended on this phone: a late or replayed notification must never ring again. */
    STALE,
}

/**
 * The phone's single source of truth for "which call is this app handling". Pure Kotlin: unit-tested on the JVM.
 * One call at a time. The server remains the authority (every Accept is re-validated with it); this class only makes sure
 * the phone never rings twice for one call, never rings for a finished call, and never runs two calls at once.
 */
class CallSessions(private val clock: () -> Long = System::currentTimeMillis) {
    private var current: CallSession? = null
    private val finished = LinkedHashMap<String, Long>()          // callId -> ended at (bounded)

    val session: CallSession? get() = current?.takeIf { it.phase != CallPhase.ENDED }

    private fun remember(id: String) {
        finished[id] = clock()
        while (finished.size > MAX_FINISHED) finished.remove(finished.keys.first())
        val cutoff = clock() - FINISHED_KEEP_MS
        finished.entries.removeAll { it.value < cutoff }
    }

    fun isFinished(callId: String): Boolean = finished.containsKey(callId)

    fun onIncoming(info: CallInfo, expiresAtMs: Long): IncomingDecision {
        if (isFinished(info.callId)) return IncomingDecision.STALE
        val cur = session
        if (cur != null) return if (cur.info.callId == info.callId) IncomingDecision.DUPLICATE else IncomingDecision.BUSY
        if (expiresAtMs <= clock()) {
            remember(info.callId)
            return IncomingDecision.EXPIRED
        }
        current = CallSession(info, CallPhase.RINGING, expiresAtMs)
        return IncomingDecision.NEW
    }

    /** RINGING -> ACCEPTING. False if this call is not the ringing one (double tap, stale action). */
    fun onAccept(callId: String): Boolean {
        val cur = session ?: return false
        if (cur.info.callId != callId || cur.phase != CallPhase.RINGING || cur.expiresAtMs <= clock()) return false
        current = cur.copy(phase = CallPhase.ACCEPTING)
        return true
    }

    /** The website reports the media connection is up. For an outgoing call this is where the phone learns about it. */
    fun onConnected(info: CallInfo): Boolean {
        if (isFinished(info.callId)) return false
        val cur = session
        if (cur != null && cur.info.callId != info.callId) return false
        val base = cur?.info ?: info
        current = CallSession(base, CallPhase.ACTIVE, 0L, clock(), cur?.muted ?: false)
        return true
    }

    fun onMuted(callId: String, muted: Boolean): Boolean {
        val cur = session ?: return false
        if (cur.info.callId != callId) return false
        current = cur.copy(muted = muted)
        return true
    }

    /** The call is over (hang-up, reject, timeout, server says so). True if it was the current call. */
    fun onEnded(callId: String): Boolean {
        val cur = current
        val wasCurrent = cur != null && cur.info.callId == callId && cur.phase != CallPhase.ENDED
        if (wasCurrent) current = cur!!.copy(phase = CallPhase.ENDED)
        remember(callId)
        return wasCurrent
    }

    /**
     * The server says the ringing stopped. Only a call that is still ringing here is dismissed. A phone that is accepting or
     * in the call ignores "answered elsewhere" / "handled elsewhere" (it IS the one that answered).
     */
    fun onCancelled(callId: String, reason: String): Boolean {
        val cur = session
        if (cur == null || cur.info.callId != callId) {
            remember(callId)                                    // a cancel may arrive before the ring: never ring for it afterwards
            return false
        }
        if (cur.phase == CallPhase.RINGING) return onEnded(callId)
        if (reason == "cancelled" || reason == "missed") return onEnded(callId)
        return false
    }

    companion object {
        const val MAX_FINISHED = 32
        const val FINISHED_KEEP_MS = 10 * 60 * 1000L
    }
}
