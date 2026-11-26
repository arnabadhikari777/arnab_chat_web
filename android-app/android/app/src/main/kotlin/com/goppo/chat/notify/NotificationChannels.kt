package com.goppo.chat.notify

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.media.AudioAttributes
import android.media.RingtoneManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.core.app.NotificationManagerCompat
import com.goppo.chat.R

/**
 * The four notification channels. Android owns a channel's sound, vibration and importance once it exists, so:
 *  - a channel is only ever CREATED when missing; an existing one is never modified or recreated (the user's choice of
 *    ringtone in Settings > Apps > Goppo Chat > Notifications survives every app update);
 *  - sounds are the SYSTEM defaults (default ringtone / default notification sound); no sound file ships with the app;
 *  - if a future version must change a default, it adds a NEW channel id (v2) instead of touching these.
 */
object NotificationChannels {
    const val CALLS = "goppo_calls"
    const val MESSAGES = "goppo_messages"
    const val MISSED_CALLS = "goppo_missed_calls"
    const val ACTIVE_CALL = "goppo_active_call"
    val ALL = listOf(CALLS, MESSAGES, MISSED_CALLS, ACTIVE_CALL)

    /** Names the website may ask the OS settings screen for. */
    fun idForName(name: String?): String? = when (name) {
        "calls" -> CALLS
        "messages" -> MESSAGES
        "missed_calls" -> MISSED_CALLS
        else -> null
    }

    fun ensure(context: Context) {
        if (Build.VERSION.SDK_INT < 26) return
        val nm = context.getSystemService(NotificationManager::class.java) ?: return
        val ringtone: Uri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE) ?: Settings.System.DEFAULT_RINGTONE_URI
        val ringAttrs = AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_NOTIFICATION_RINGTONE).setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build()
        val noteAttrs = AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_NOTIFICATION).setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build()

        createIfMissing(nm, CALLS) {
            NotificationChannel(CALLS, context.getString(R.string.channel_calls), NotificationManager.IMPORTANCE_HIGH).apply {
                description = context.getString(R.string.channel_calls_desc)
                setSound(ringtone, ringAttrs)                          // the phone's default RINGTONE, user-changeable in Settings
                enableVibration(true)
                vibrationPattern = longArrayOf(0, 800, 600, 800, 600)
                enableLights(true)
                lockscreenVisibility = android.app.Notification.VISIBILITY_PUBLIC
                setShowBadge(false)
            }
        }
        createIfMissing(nm, MESSAGES) {
            NotificationChannel(MESSAGES, context.getString(R.string.channel_messages), NotificationManager.IMPORTANCE_HIGH).apply {
                description = context.getString(R.string.channel_messages_desc)
                setSound(Settings.System.DEFAULT_NOTIFICATION_URI, noteAttrs)     // the default NOTIFICATION sound
                enableVibration(true)
                lockscreenVisibility = android.app.Notification.VISIBILITY_PRIVATE
                setShowBadge(true)
            }
        }
        createIfMissing(nm, MISSED_CALLS) {
            NotificationChannel(MISSED_CALLS, context.getString(R.string.channel_missed), NotificationManager.IMPORTANCE_DEFAULT).apply {
                description = context.getString(R.string.channel_missed_desc)
                setSound(Settings.System.DEFAULT_NOTIFICATION_URI, noteAttrs)
                lockscreenVisibility = android.app.Notification.VISIBILITY_PRIVATE
                setShowBadge(true)
            }
        }
        createIfMissing(nm, ACTIVE_CALL) {
            NotificationChannel(ACTIVE_CALL, context.getString(R.string.channel_active), NotificationManager.IMPORTANCE_LOW).apply {
                description = context.getString(R.string.channel_active_desc)
                setSound(null, null)
                enableVibration(false)
                lockscreenVisibility = android.app.Notification.VISIBILITY_PUBLIC
                setShowBadge(false)
            }
        }
    }

    private fun createIfMissing(nm: NotificationManager, id: String, build: () -> NotificationChannel) {
        if (nm.getNotificationChannel(id) == null) nm.createNotificationChannel(build())
    }

    /** NotificationManager.IMPORTANCE_* of a channel (IMPORTANCE_DEFAULT before Android 8, where channels do not exist). */
    fun importance(context: Context, id: String): Int {
        if (Build.VERSION.SDK_INT < 26) return NotificationManager.IMPORTANCE_DEFAULT
        return context.getSystemService(NotificationManager::class.java)?.getNotificationChannel(id)?.importance
            ?: NotificationManager.IMPORTANCE_NONE
    }

    /** Notifications are on for the app and the channel is not switched off. */
    fun usable(context: Context, id: String): Boolean =
        NotificationManagerCompat.from(context).areNotificationsEnabled() && importance(context, id) != NotificationManager.IMPORTANCE_NONE
}
