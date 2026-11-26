package com.goppo.chat.notify

import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.media.AudioManager
import android.media.RingtoneManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.app.Person
import androidx.core.graphics.drawable.IconCompat
import com.goppo.chat.GoppoRuntime
import com.goppo.chat.MainActivity
import com.goppo.chat.R
import com.goppo.chat.calls.CallActionReceiver
import com.goppo.chat.calls.CallInfo
import com.goppo.chat.calls.IncomingCallActivity
import com.goppo.chat.push.PushMessage
import com.goppo.chat.push.SitePaths

/** PendingIntents that open the website at a validated path. */
object Intents {
    private const val IMMUTABLE_UPDATE = PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT

    fun open(context: Context, path: String?, acceptCallId: String? = null): Intent =
        Intent(context, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)
            .apply {
                SitePaths.safe(path)?.let { putExtra(MainActivity.EXTRA_OPEN_PATH, it) }
                acceptCallId?.let { putExtra(MainActivity.EXTRA_ACCEPT_CALL, it) }
            }

    fun openPending(context: Context, requestCode: Int, path: String?): PendingIntent =
        PendingIntent.getActivity(context, requestCode, open(context, path), IMMUTABLE_UPDATE)

    fun activity(context: Context, requestCode: Int, intent: Intent): PendingIntent =
        PendingIntent.getActivity(context, requestCode, intent, IMMUTABLE_UPDATE)

    fun broadcast(context: Context, requestCode: Int, intent: Intent): PendingIntent =
        PendingIntent.getBroadcast(context, requestCode, intent, IMMUTABLE_UPDATE)
}

/** Incoming-call, ongoing-call and missed-call notifications. */
object CallNotifications {
    const val ID_INCOMING = 3001
    const val ID_ACTIVE = 3002
    const val ID_MISSED = 3003
    const val TAG_INCOMING = "incoming_call"
    private const val BRAND = 0xFF0B6E78.toInt()

    private fun person(info: CallInfo, avatar: Bitmap?): Person =
        Person.Builder().setName(info.peerName).setImportant(true)
            .apply { if (avatar != null) setIcon(IconCompat.createWithBitmap(avatar)) }.build()

    /** Full-screen intent is only usable if Android allows it for this app (always on < 14; user-revocable on 14+). */
    fun canUseFullScreenIntent(context: Context): Boolean {
        if (Build.VERSION.SDK_INT < 34) return true
        return context.getSystemService(NotificationManager::class.java)?.canUseFullScreenIntent() ?: false
    }

    fun showIncoming(context: Context, info: CallInfo, expiresAtMs: Long, avatar: Bitmap?) {
        NotificationChannels.ensure(context)
        val remaining = (expiresAtMs - System.currentTimeMillis()).coerceAtLeast(1_000L)
        val ui = Intent(context, IncomingCallActivity::class.java)
            .putExtra(IncomingCallActivity.EXTRA_CALL_ID, info.callId)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        val fullScreen = Intents.activity(context, 10, ui)
        // Answer must start an Activity directly: Android 12+ blocks "notification -> receiver -> activity" trampolines.
        val answer = Intents.activity(context, 11, Intent(ui).setAction(IncomingCallActivity.ACTION_ACCEPT).putExtra(IncomingCallActivity.EXTRA_AUTO_ACCEPT, true))
        val decline = Intents.broadcast(context, 12,
            Intent(context, CallActionReceiver::class.java).setAction(CallActionReceiver.ACTION_DECLINE).putExtra(CallActionReceiver.EXTRA_CALL_ID, info.callId))
        val title = context.getString(if (info.video) R.string.call_incoming_video else R.string.call_incoming_voice)
        val b = NotificationCompat.Builder(context, NotificationChannels.CALLS)
            .setSmallIcon(R.drawable.ic_stat_goppo).setColor(BRAND)
            .setContentTitle(info.peerName).setContentText(title)
            .setCategory(NotificationCompat.CATEGORY_CALL).setPriority(NotificationCompat.PRIORITY_MAX)
            .setOngoing(true).setAutoCancel(false).setOnlyAlertOnce(false)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setContentIntent(fullScreen).setTimeoutAfter(remaining)              // Android removes it when the ring time is over
            .setStyle(NotificationCompat.CallStyle.forIncomingCall(person(info, avatar), decline, answer).setIsVideo(info.video))
        if (canUseFullScreenIntent(context)) b.setFullScreenIntent(fullScreen, true)
        if (Build.VERSION.SDK_INT < 26) {                                          // before channels: same system ringtone + vibration
            b.setSound(RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE), AudioManager.STREAM_RING)
            b.setVibrate(longArrayOf(0, 800, 600, 800, 600))
        }
        val n = b.build()
        n.flags = n.flags or Notification.FLAG_INSISTENT                           // keep ringing (channel sound) until answered/cancelled
        post(context, TAG_INCOMING, ID_INCOMING, n)
    }

    fun cancelIncoming(context: Context) = NotificationManagerCompat.from(context).cancel(TAG_INCOMING, ID_INCOMING)

    fun buildOngoing(context: Context, info: CallInfo, connectedAtMs: Long, muted: Boolean, avatar: Bitmap?): Notification {
        NotificationChannels.ensure(context)
        val hangUp = Intents.broadcast(context, 20,
            Intent(context, CallActionReceiver::class.java).setAction(CallActionReceiver.ACTION_END).putExtra(CallActionReceiver.EXTRA_CALL_ID, info.callId))
        val mute = Intents.broadcast(context, 21,
            Intent(context, CallActionReceiver::class.java).setAction(CallActionReceiver.ACTION_TOGGLE_MUTE).putExtra(CallActionReceiver.EXTRA_CALL_ID, info.callId))
        return NotificationCompat.Builder(context, NotificationChannels.ACTIVE_CALL)
            .setSmallIcon(R.drawable.ic_stat_goppo).setColor(BRAND)
            .setContentTitle(info.peerName)
            .setContentText(context.getString(if (info.video) R.string.call_ongoing_video else R.string.call_ongoing_voice))
            .setCategory(NotificationCompat.CATEGORY_CALL).setPriority(NotificationCompat.PRIORITY_LOW)
            .setOngoing(true).setOnlyAlertOnce(true).setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setUsesChronometer(true).setShowWhen(true).setWhen(connectedAtMs)    // shows the call duration
            .setContentIntent(Intents.openPending(context, 22, info.path))
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .addAction(R.drawable.ic_stat_goppo, context.getString(if (muted) R.string.call_unmute else R.string.call_mute), mute)
            .setStyle(NotificationCompat.CallStyle.forOngoingCall(person(info, avatar), hangUp))
            .build()
    }

    fun updateOngoing(context: Context, notification: Notification) = post(context, null, ID_ACTIVE, notification)

    fun cancelOngoing(context: Context) = NotificationManagerCompat.from(context).cancel(ID_ACTIVE)

    fun showMissed(context: Context, msg: PushMessage.MissedCall) {
        NotificationChannels.ensure(context)
        val text = msg.body.ifEmpty { context.getString(if (msg.video) R.string.call_missed_video else R.string.call_missed_voice) }
        val n = NotificationCompat.Builder(context, NotificationChannels.MISSED_CALLS)
            .setSmallIcon(R.drawable.ic_stat_goppo).setColor(BRAND)
            .setContentTitle(msg.callerName).setContentText(text)
            .setCategory(NotificationCompat.CATEGORY_MISSED_CALL).setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .setAutoCancel(true).setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .setPublicVersion(NotificationCompat.Builder(context, NotificationChannels.MISSED_CALLS)
                .setSmallIcon(R.drawable.ic_stat_goppo).setContentTitle(context.getString(R.string.call_missed_public)).build())
            .setContentIntent(Intents.openPending(context, 30 + (msg.callId.hashCode() and 0xFFF), msg.path))
            .build()
        post(context, "missed:${msg.callId}", ID_MISSED, n)
    }

    fun cancelMissedFor(context: Context, callId: String) = NotificationManagerCompat.from(context).cancel("missed:$callId", ID_MISSED)

    fun post(context: Context, tag: String?, id: Int, n: Notification) {
        val nm = NotificationManagerCompat.from(context)
        if (!nm.areNotificationsEnabled()) return
        try {
            nm.notify(tag, id, n)
        } catch (_: SecurityException) {
            // POST_NOTIFICATIONS revoked between the check and the call: nothing to show.
        }
    }
}

/** Message and announcement notifications. */
object MessageNotifier {
    private const val GROUP = "goppo_messages_group"
    private const val SUMMARY_ID = 2000
    private const val MESSAGE_ID = 2001
    private const val NOTICE_ID = 2002
    private const val BRAND = 0xFF0B6E78.toInt()

    /** The person is looking at this very conversation inside the app: no notification needed. */
    fun isViewing(conversationId: String): Boolean =
        GoppoRuntime.appVisible && SitePaths.conversationId(GoppoRuntime.currentPath)?.toString() == conversationId

    fun show(context: Context, msg: PushMessage.NewMessage) {
        NotificationChannels.ensure(context)
        if (isViewing(msg.conversationId)) return
        post(context, msg, null, silent = false)
        AvatarLoader.load(msg.avatarUrl, 192) { bmp -> if (bmp != null) post(context, msg, bmp, silent = true) }
    }

    private fun tag(conversationId: String) = "conv:$conversationId"

    private fun post(context: Context, msg: PushMessage.NewMessage, avatar: Bitmap?, silent: Boolean) {
        val text = msg.body.ifEmpty {
            if (msg.count <= 1) context.getString(R.string.msg_one) else context.getString(R.string.msg_many, msg.count)
        }
        val me = Person.Builder().setName(context.getString(R.string.you)).build()
        val sender = Person.Builder().setName(msg.senderName).setKey("user:${msg.senderId ?: msg.conversationId}")
            .apply { if (avatar != null) setIcon(IconCompat.createWithBitmap(avatar)) }.build()
        val style = NotificationCompat.MessagingStyle(me)
            .addMessage(NotificationCompat.MessagingStyle.Message(text, System.currentTimeMillis(), sender))
        val publicVersion = NotificationCompat.Builder(context, NotificationChannels.MESSAGES)
            .setSmallIcon(R.drawable.ic_stat_goppo).setContentTitle(context.getString(R.string.app_name))
            .setContentText(context.getString(R.string.msg_public)).build()
        val b = NotificationCompat.Builder(context, NotificationChannels.MESSAGES)
            .setSmallIcon(R.drawable.ic_stat_goppo).setColor(BRAND)
            .setContentTitle(msg.senderName).setContentText(text)
            .setStyle(style).setCategory(NotificationCompat.CATEGORY_MESSAGE).setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true).setOnlyAlertOnce(silent).setNumber(msg.count)
            .setVisibility(NotificationCompat.VISIBILITY_PRIVATE).setPublicVersion(publicVersion)
            .setGroup(GROUP).setGroupAlertBehavior(NotificationCompat.GROUP_ALERT_CHILDREN)
            .setContentIntent(Intents.openPending(context, 40 + (msg.conversationId.hashCode() and 0xFFF), msg.path))
        if (avatar != null) b.setLargeIcon(avatar)
        val nm = NotificationManagerCompat.from(context)
        if (!nm.areNotificationsEnabled()) return
        try {
            nm.notify(tag(msg.conversationId), MESSAGE_ID, b.build())            // same tag = the chat's notification is UPDATED, not duplicated
            val summary = NotificationCompat.Builder(context, NotificationChannels.MESSAGES)
                .setSmallIcon(R.drawable.ic_stat_goppo).setColor(BRAND).setContentTitle(context.getString(R.string.app_name))
                .setContentText(context.getString(R.string.msg_public)).setGroup(GROUP).setGroupSummary(true).setAutoCancel(true)
                .setGroupAlertBehavior(NotificationCompat.GROUP_ALERT_CHILDREN).setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
                .setContentIntent(Intents.openPending(context, 39, null)).build()
            nm.notify(SUMMARY_ID, summary)
        } catch (_: SecurityException) {
        }
    }

    fun showNotice(context: Context, msg: PushMessage.Notice) {
        NotificationChannels.ensure(context)
        if (msg.body.isEmpty()) return
        val n = NotificationCompat.Builder(context, NotificationChannels.MESSAGES)
            .setSmallIcon(R.drawable.ic_stat_goppo).setColor(BRAND).setContentTitle(context.getString(R.string.app_name))
            .setContentText(msg.body).setAutoCancel(true).setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .setContentIntent(Intents.openPending(context, 38, msg.path)).build()
        CallNotifications.post(context, "notice:${msg.notificationId ?: "x"}", NOTICE_ID, n)
    }

    /** The person opened the chat: its notification (and the group summary, if it was the last one) goes away. */
    fun cancelConversation(context: Context, conversationId: Long) {
        val nm = NotificationManagerCompat.from(context)
        nm.cancel(tag(conversationId.toString()), MESSAGE_ID)
        val left = try {
            nm.activeNotifications.count { it.id == MESSAGE_ID && it.tag?.startsWith("conv:") == true }
        } catch (_: Exception) { 1 }
        if (left == 0) nm.cancel(SUMMARY_ID)
    }
}
