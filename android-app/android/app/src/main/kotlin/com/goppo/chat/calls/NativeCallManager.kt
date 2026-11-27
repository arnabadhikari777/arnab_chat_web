package com.goppo.chat.calls

import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.os.Handler
import android.os.Looper
import android.telecom.DisconnectCause
import android.widget.Toast
import com.getcapacitor.Logger
import com.goppo.chat.GoppoRuntime
import com.goppo.chat.R
import com.goppo.chat.audio.AudioRouter
import com.goppo.chat.net.SiteClient
import com.goppo.chat.notify.AvatarLoader
import com.goppo.chat.notify.CallNotifications
import com.goppo.chat.notify.Intents
import com.goppo.chat.notify.NotificationChannels
import com.goppo.chat.push.PushMessage
import org.json.JSONObject
import java.util.concurrent.Executors

/**
 * The native call coordinator: Native incoming call -> existing call API -> existing signaling -> existing WebRTC.
 *
 * It owns NO media and NO signaling. Its job:
 *   ring     FCM message or the page's own `call:incoming` event -> Telecom + notification + incoming screen (once per call)
 *   validate every Accept is re-checked with the server (GET /api/calling/state/<id>) before anything is started
 *   hand off Accept/Reject/Hang up/Mute are delivered to the page's calling.js through CommandBus (one WebRTC connection)
 *   keep     foreground service + ongoing notification + Telecom + audio routing for the connected call
 * All state lives in [CallSessions] (pure, unit-tested). Everything below runs on the main thread.
 */
object NativeCallManager {
    private val main = Handler(Looper.getMainLooper())
    private val io = Executors.newSingleThreadExecutor()
    private var sessions = CallSessions()
    private var appContext: Context? = null
    private val avatars = HashMap<String, Bitmap>()
    private var timeout: Runnable? = null
    private var poll: Runnable? = null
    private var mutedBeforeHold = false

    fun init(context: Context) {
        if (appContext == null) appContext = context.applicationContext
        GoppoRuntime.ensureConfigured(context)
        NotificationChannels.ensure(context)
    }

    fun session(): CallSession? = sessions.session

    fun avatar(callId: String): Bitmap? = avatars[callId]

    private fun ctx(): Context? = appContext

    private fun run(block: () -> Unit) {
        if (Looper.myLooper() == Looper.getMainLooper()) block() else main.post(block)
    }

    // ------------------------------------------------------------------ ringing
    fun onIncomingPush(context: Context, msg: PushMessage.IncomingCall) {
        init(context)
        val info = CallInfo(msg.callId, msg.video, msg.callerName, msg.callerId, msg.avatarUrl, msg.conversationId,
            msg.path ?: msg.conversationId?.let { "/chat/$it" }, CallDirection.INCOMING)
        run { ring(info, msg.expiresAtMs) }
    }

    /** The page reported `call:incoming` (it found the call by polling): covers phones with no FCM or a late push. */
    fun onWebIncoming(context: Context, detail: JSONObject) {
        init(context)
        val id = detail.optString("call_id")
        if (id.isEmpty()) return
        val caller = detail.optJSONObject("caller")
        val conv = detail.optString("conversation_id").ifEmpty { null }
        val info = CallInfo(id.lowercase(), detail.optString("call_type") == "video", caller?.optString("full_name").orEmpty().ifEmpty { "Goppo" },
            caller?.optString("id")?.ifEmpty { null }, caller?.optString("avatar_url")?.takeIf { it.startsWith("https://") }, conv,
            conv?.let { "/chat/$it" }, CallDirection.INCOMING)
        val left = detail.optLong("seconds_left", 60L).coerceIn(3L, 120L)
        run { ring(info, System.currentTimeMillis() + left * 1000) }
    }

    private fun ring(info: CallInfo, expiresAtMs: Long) {
        val c = ctx() ?: return
        when (sessions.onIncoming(info, expiresAtMs)) {
            IncomingDecision.NEW -> Unit
            else -> return                                    // duplicate, busy, expired or already finished: never ring
        }
        TelecomBridge.addIncoming(c, info)                    // best effort
        CallNotifications.showIncoming(c, info, expiresAtMs, null)
        if (GoppoRuntime.appVisible) {                        // the app is open: show the call screen straight away
            try {
                c.startActivity(Intent(c, IncomingCallActivity::class.java).putExtra(IncomingCallActivity.EXTRA_CALL_ID, info.callId)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP))
            } catch (_: Exception) {
            }
        }
        AvatarLoader.load(info.avatarUrl, 384) { bmp -> if (bmp != null && sessions.isLive(info.callId)) avatars[info.callId] = bmp }   // never cache for an ended call
        scheduleTimeout(info.callId, expiresAtMs)
        startPolling(info.callId)
        GoppoRuntime.emitToWeb("call:incoming", JSONObject().put("call_id", info.callId).put("call_type", if (info.video) "video" else "voice"))
    }

    private fun scheduleTimeout(callId: String, expiresAtMs: Long) {
        timeout?.let(main::removeCallbacks)
        val r = Runnable {
            val s = sessions.session
            if (s != null && s.info.callId == callId && s.phase == CallPhase.RINGING) {
                sessions.onEnded(callId)
                stopRinging(callId, DisconnectCause.MISSED)
                GoppoRuntime.emitToWeb("call:missed", JSONObject().put("call_id", callId).put("reason", "timeout"))
            }
        }
        timeout = r
        main.postDelayed(r, (expiresAtMs - System.currentTimeMillis()).coerceAtLeast(0))
    }

    /** While ringing, ask the server every few seconds whether the call is still there (a lost "cancelled" push must not leave a ghost ring). */
    private fun startPolling(callId: String) {
        poll?.let(main::removeCallbacks)
        val c = ctx() ?: return
        val r = object : Runnable {
            override fun run() {
                val s = sessions.session
                if (s == null || s.info.callId != callId || s.phase != CallPhase.RINGING) return
                io.execute {
                    val check = SiteClient.checkCall(c, callId)
                    main.post {
                        val cur = sessions.session
                        if (cur != null && cur.info.callId == callId && cur.phase == CallPhase.RINGING) {
                            if (check is SiteClient.CallCheck.Over) onServerSaysOver(callId, check.status)
                            else if (check !is SiteClient.CallCheck.SignedOut) main.postDelayed(this, POLL_MS)
                        }
                    }
                }
            }
        }
        poll = r
        main.postDelayed(r, FIRST_POLL_MS)              // check almost at once: a late push for a call that already ended dies in ~1 s
    }

    private fun onServerSaysOver(callId: String, status: String) {
        if (sessions.onEnded(callId)) {
            stopRinging(callId, DisconnectCause.REMOTE)
            GoppoRuntime.emitToWeb("call:${if (status == "missed") "missed" else "ended"}", JSONObject().put("call_id", callId).put("status", status))
        }
    }

    private fun stopRinging(callId: String, cause: Int) {
        val c = ctx() ?: return
        timeout?.let(main::removeCallbacks); timeout = null
        poll?.let(main::removeCallbacks); poll = null
        CallNotifications.cancelIncoming(c)
        IncomingCallActivity.dismiss()
        TelecomBridge.finish(callId, cause)
        avatars.remove(callId)
    }

    fun onCancelledPush(context: Context, msg: PushMessage.CallCancelled) {
        init(context)
        run {
            if (sessions.onCancelled(msg.callId, msg.reason)) {
                stopRinging(msg.callId, DisconnectCause.REMOTE)
                if (sessions.session == null) cleanupCall(msg.callId, emit = true)
            }
        }
    }

    fun onMissedPush(context: Context, msg: PushMessage.MissedCall) {
        init(context)
        run {
            if (sessions.onCancelled(msg.callId, "missed")) stopRinging(msg.callId, DisconnectCause.MISSED)
            CallNotifications.showMissed(context.applicationContext, msg)
            GoppoRuntime.emitToWeb("call:missed", JSONObject().put("call_id", msg.callId))
        }
    }

    // ------------------------------------------------------------------ accept / reject
    /** Accept pressed (screen, notification button, headset). Validates with the server, then hands over to the website. */
    fun accept(context: Context) {
        init(context)
        run {
            val c = ctx() ?: return@run
            val s = sessions.session ?: return@run
            val id = s.info.callId
            if (!sessions.onAccept(id)) return@run                   // double tap, stale button, or ring time over
            timeout?.let(main::removeCallbacks); timeout = null
            poll?.let(main::removeCallbacks); poll = null
            CallNotifications.cancelIncoming(c)
            TelecomBridge.markActive(id)
            GoppoRuntime.emitToWeb("call:accepted", JSONObject().put("call_id", id))
            try { c.startActivity(Intents.open(c, s.info.path ?: s.info.conversationId?.let { "/chat/$it" }, acceptCallId = id)) } catch (_: Exception) { }
            io.execute {
                val check = SiteClient.checkCall(c, id)
                main.post {
                    when (check) {
                        is SiteClient.CallCheck.Over -> abortAccept(id, c.getString(R.string.call_already_ended))
                        SiteClient.CallCheck.SignedOut -> abortAccept(id, c.getString(R.string.call_signed_out))
                        else -> CommandBus.enqueue("call:accept", JSONObject().put("call_id", id), 25_000L) { ok ->
                            if (!ok) abortAccept(id, null)           // the page refused (not ringing any more, wrong state)
                        }
                    }
                }
            }
        }
    }

    private fun abortAccept(callId: String, message: String?) {
        val c = ctx() ?: return
        if (sessions.session?.info?.callId == callId && sessions.session?.phase == CallPhase.ACCEPTING) {
            sessions.onEnded(callId)
            TelecomBridge.finish(callId, DisconnectCause.REMOTE)
            avatars.remove(callId)
            GoppoRuntime.emitToWeb("call:ended", JSONObject().put("call_id", callId).put("status", "unavailable"))
        }
        if (message != null) Toast.makeText(c, message, Toast.LENGTH_LONG).show()
    }

    fun reject(context: Context) {
        init(context)
        run {
            val c = ctx() ?: return@run
            val s = sessions.session ?: return@run
            if (s.phase != CallPhase.RINGING) return@run
            val id = s.info.callId
            sessions.onEnded(id)
            stopRinging(id, DisconnectCause.REJECTED)
            GoppoRuntime.emitToWeb("call:rejected", JSONObject().put("call_id", id))
            // The server is the authority: decline there even when no page is open. The page (if open) is told too; its own POST is idempotent.
            io.execute { postWithRetry(c, JSONObject().put("call_id", id).put("status", "rejected").toString()) }
            CommandBus.enqueue("call:reject", JSONObject().put("call_id", id), 5_000L)
        }
    }

    // ------------------------------------------------------------------ events from the website (platform.js -> GoppoNative.notify)
    fun onWebEvent(context: Context, event: String, detail: JSONObject) {
        init(context)
        when (event) {
            "call:incoming" -> onWebIncoming(context, detail)
            "call:connected" -> run { onConnected(detail) }
            "call:ended" -> run { onEnded(detail.optString("call_id"), detail.optString("status")) }
            "call:muted-changed" -> run { onMuted(detail.optString("call_id"), detail.optBoolean("muted")) }
        }
    }

    private fun infoFrom(detail: JSONObject): CallInfo? {
        val id = detail.optString("call_id").lowercase().ifEmpty { return null }
        val peer = detail.optJSONObject("peer")
        val conv = detail.optString("conversation_id").ifEmpty { null }
        val existing = sessions.session?.info?.takeIf { it.callId == id }
        return existing ?: CallInfo(id, detail.optString("call_type") == "video", peer?.optString("full_name").orEmpty().ifEmpty { "Goppo" },
            peer?.optString("id")?.ifEmpty { null }, peer?.optString("avatar_url")?.takeIf { it.startsWith("https://") }, conv,
            conv?.let { "/chat/$it" }, if (detail.optString("role") == "caller") CallDirection.OUTGOING else CallDirection.INCOMING)
    }

    private fun onConnected(detail: JSONObject) {
        val c = ctx() ?: return
        val info = infoFrom(detail) ?: return
        if (!sessions.onConnected(info)) return
        timeout?.let(main::removeCallbacks); timeout = null
        poll?.let(main::removeCallbacks); poll = null
        CallNotifications.cancelIncoming(c)
        IncomingCallActivity.dismiss()
        val telecom = when {
            TelecomBridge.hasConnection(info.callId) -> { TelecomBridge.markActive(info.callId); true }
            info.direction == CallDirection.OUTGOING -> TelecomBridge.placeOutgoing(c, info)
            else -> false
        }
        if (avatars[info.callId] == null) AvatarLoader.load(info.avatarUrl, 256) { bmp ->
            if (bmp != null && sessions.session?.info?.callId == info.callId) { avatars[info.callId] = bmp; ActiveCallService.start(c) }
        }
        AudioRouter.start(c, info.video, telecom)
        com.goppo.chat.MainActivity.current?.get()?.requestBluetoothForCall()     // Android 12+: needed to route audio to a headset
        ActiveCallService.start(c)
        GoppoRuntime.emitToWeb("call:connected", JSONObject().put("call_id", info.callId).put("telecom", telecom))
    }

    private fun onMuted(callId: String, muted: Boolean) {
        val c = ctx() ?: return
        if (!sessions.onMuted(callId, muted)) return
        AudioRouter.setMuted(muted)
        ActiveCallService.start(c)                             // refresh the notification button
    }

    private fun onEnded(callId: String, status: String) {
        if (callId.isEmpty()) return
        val wasCurrent = sessions.onEnded(callId)
        if (wasCurrent) {
            stopRinging(callId, DisconnectCause.LOCAL)
            cleanupCall(callId, emit = true, status = status)
        } else {
            // An old / unrelated call ended: it must not touch the timers, notification or screen of the call that is ringing now.
            TelecomBridge.finish(callId, DisconnectCause.LOCAL)
        }
    }

    private fun cleanupCall(callId: String, emit: Boolean, status: String = "ended") {
        val c = ctx() ?: return
        ActiveCallService.stop(c)
        AudioRouter.stop()
        TelecomBridge.finish(callId, DisconnectCause.LOCAL)
        avatars.remove(callId)
        if (emit) GoppoRuntime.emitToWeb("call:ended", JSONObject().put("call_id", callId).put("status", status))
    }

    // ------------------------------------------------------------------ commands (notification buttons, plugin methods, Telecom)
    fun endCall(context: Context) {
        init(context)
        run {
            val c = ctx() ?: return@run
            val s = sessions.session ?: return@run
            val id = s.info.callId
            if (s.phase == CallPhase.RINGING) { reject(context); return@run }
            CommandBus.enqueue("call:hangup", JSONObject().put("call_id", id), 6_000L) { handled ->
                if (!handled && sessions.session?.info?.callId == id) {
                    // The page is gone (process restarted, navigated away): end it on the server ourselves and clean up.
                    io.execute { postWithRetry(c, JSONObject().put("call_id", id).put("status", "completed").toString()) }
                    run { onEnded(id, "completed") }
                }
            }
        }
    }

    /** Status POST that survives a short network drop (offline = code 0, server error = 5xx); other answers are final. Background thread only. */
    private fun postWithRetry(c: Context, json: String) {
        var wait = 1_000L
        repeat(3) {
            val r = try { SiteClient.postJson(c, "/api/calling/status", json) } catch (_: Exception) { null }
            if (r != null && r.code != 0 && r.code !in 500..599) return
            try { Thread.sleep(wait) } catch (_: InterruptedException) { return }
            wait *= 2
        }
    }

    fun setMuted(muted: Boolean) {
        run {
            val s = sessions.session ?: return@run
            CommandBus.enqueue("call:set-muted", JSONObject().put("call_id", s.info.callId).put("muted", muted), 5_000L)
        }
    }

    fun toggleMute() {
        val s = sessions.session ?: return
        setMuted(!s.muted)
    }

    fun onTelecomAnswer(callId: String) { run { val c = ctx() ?: return@run; if (sessions.session?.info?.callId == callId) accept(c) } }
    fun onTelecomReject(callId: String) { run { val c = ctx() ?: return@run; if (sessions.session?.info?.callId == callId) reject(c) } }
    fun onTelecomDisconnect(callId: String) { run { val c = ctx() ?: return@run; if (sessions.session?.info?.callId == callId) endCall(c) } }
    fun onTelecomHold(callId: String, held: Boolean) {
        run {
            val s = sessions.session ?: return@run
            if (s.info.callId != callId) return@run
            Logger.info("Goppo: call on hold=$held")
            if (held) { mutedBeforeHold = s.muted; setMuted(true) }
            else setMuted(mutedBeforeHold)                       // resume with the mute the person had chosen, not "unmuted"
        }
    }

    // ------------------------------------------------------------------ state for the plugin
    fun stateJson(): JSONObject {
        val s = sessions.session ?: return JSONObject().put("active", false)
        return JSONObject()
            .put("active", true).put("phase", s.phase.name.lowercase()).put("call_id", s.info.callId)
            .put("call_type", if (s.info.video) "video" else "voice")
            .put("direction", s.info.direction.name.lowercase()).put("peer_name", s.info.peerName)
            .put("connected_at", if (s.connectedAtMs > 0) s.connectedAtMs else JSONObject.NULL)
            .put("muted", s.muted).put("telecom", TelecomBridge.hasConnection(s.info.callId)).put("audio", AudioRouter.toJson())
    }

    private const val POLL_MS = 4_000L
    private const val FIRST_POLL_MS = 1_000L
}
