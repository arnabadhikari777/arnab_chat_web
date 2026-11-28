package com.goppo.chat.push

import android.content.Context
import com.getcapacitor.Logger
import com.google.android.gms.common.ConnectionResult
import com.google.android.gms.common.GoogleApiAvailability
import com.google.firebase.FirebaseApp
import com.google.firebase.messaging.FirebaseMessaging
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import com.goppo.chat.GoppoRuntime
import com.goppo.chat.calls.NativeCallManager
import com.goppo.chat.net.SiteClient
import com.goppo.chat.notify.CallNotifications
import com.goppo.chat.notify.MessageNotifier
import org.json.JSONObject
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/** Is Firebase really usable in this build / on this phone? Drives the `nativePush` capability. */
object FirebaseSupport {
    /** True only with a google-services.json in the build (FirebaseApp exists) AND Google Play services on the phone. */
    fun operational(context: Context): Boolean {
        if (FirebaseApp.getApps(context).isEmpty()) return false
        return GoogleApiAvailability.getInstance().isGooglePlayServicesAvailable(context) == ConnectionResult.SUCCESS
    }

    fun reason(context: Context): String = when {
        FirebaseApp.getApps(context).isEmpty() -> "firebase_not_configured"
        GoogleApiAvailability.getInstance().isGooglePlayServicesAvailable(context) != ConnectionResult.SUCCESS -> "play_services_unavailable"
        else -> "ok"
    }

    /** Asks Firebase for the current registration token. The callback gets null on any failure. */
    fun fetchToken(context: Context, callback: (String?) -> Unit) {
        if (!operational(context)) { callback(null); return }
        try {
            FirebaseMessaging.getInstance().token.addOnCompleteListener { task ->
                callback(if (task.isSuccessful) task.result else null)
            }
        } catch (e: Exception) {
            Logger.warn("Goppo: FCM token request failed (${e.javaClass.simpleName})")
            callback(null)
        }
    }
}

/**
 * Receives FCM data messages, also when the app is in the background or its process was removed (Android starts the
 * process for a high-priority message). The payload is parsed and validated by [PushParser]; what is shown is decided here.
 * FCM tokens are never logged. Token refreshes reach the server through the website (device.js), which has the session + CSRF.
 */
class GoppoFirebaseService : FirebaseMessagingService() {

    override fun onNewToken(token: String) {
        // The website registers it with PATCH /api/devices/<id> when the page asks via getPushToken() or receives push:token.
        GoppoRuntime.emitToWeb("push:token", JSONObject().put("token", token))
    }

    override fun onMessageReceived(message: RemoteMessage) {
        val ctx = applicationContext
        // Nothing in a push payload may ever crash the app: init + parsing are guarded like the handling below.
        val parsed = try {
            NativeCallManager.init(ctx)
            PushParser.parse(message.data, System.currentTimeMillis())
        } catch (e: Exception) {
            Logger.warn("Goppo: push could not be parsed (${e.javaClass.simpleName})")
            return
        }
        // Run on the main thread (notifications, Telecom, windows) but do not return before it is done: the process may be frozen after return.
        val done = CountDownLatch(1)
        android.os.Handler(android.os.Looper.getMainLooper()).post {
            try { handle(ctx, parsed) } catch (e: Exception) { Logger.error("Goppo: push handling failed", e) } finally { done.countDown() }
        }
        done.await(8, TimeUnit.SECONDS)
    }

    private fun handle(ctx: Context, m: PushMessage) {
        when (m) {
            is PushMessage.IncomingCall -> NativeCallManager.onIncomingPush(ctx, m)
            is PushMessage.CallCancelled -> NativeCallManager.onCancelledPush(ctx, m)
            is PushMessage.MissedCall -> NativeCallManager.onMissedPush(ctx, m)
            is PushMessage.NewMessage -> {
                MessageNotifier.show(ctx, m)
                m.ack?.let { ackAsync(ctx, it) }
                runCatching { com.goppo.chat.local.SyncScheduler.enqueueNow(ctx) }      // save the new message (and auto-download new media) in the background
            }
            is PushMessage.SyncHint -> runCatching { com.goppo.chat.local.SyncScheduler.enqueueNow(ctx) }
            is PushMessage.Notice -> MessageNotifier.showNotice(ctx, m)
            is PushMessage.Invalid -> Logger.warn("Goppo: ignored an invalid push (${m.why})")
        }
    }

    private fun ackAsync(ctx: Context, ack: String) {
        io.execute {
            // Delivery receipt (the sender's double tick). No cookie, no CSRF: the signed token authenticates it.
            SiteClient.postAnonymous(ctx, "/api/push/ack", JSONObject().put("token", ack).toString())
        }
    }

    private companion object {
        val io = Executors.newSingleThreadExecutor()
    }
}
