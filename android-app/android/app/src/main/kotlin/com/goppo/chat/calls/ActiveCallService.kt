package com.goppo.chat.calls

import android.app.Notification
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import com.getcapacitor.Logger
import com.goppo.chat.notify.CallNotifications

/**
 * Foreground service for a CONNECTED call. It exists so Android keeps the process (and the WebView's WebRTC microphone/camera)
 * alive while the app is in the background, and so the person sees the ongoing-call notification with the caller, the running
 * duration, Mute and End.
 *
 * Types (Android 14 requires a declared type + matching permission): phoneCall (self-managed call, MANAGE_OWN_CALLS),
 * microphone (RECORD_AUDIO granted) and, for video, camera (CAMERA granted). It is started only after the call is connected
 * (the app is then in the foreground or just left it), never for a ringing call. It never restarts itself after being killed.
 */
class ActiveCallService : Service() {

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val session = NativeCallManager.session()
        if (intent?.action == ACTION_STOP || session == null || session.phase != CallPhase.ACTIVE) {
            ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
            stopSelf()
            return START_NOT_STICKY
        }
        val notification = CallNotifications.buildOngoing(this, session.info, session.connectedAtMs, session.muted, NativeCallManager.avatar(session.info.callId))
        startInForeground(notification, session.info.video)
        return START_NOT_STICKY
    }

    private fun granted(permission: String) = ContextCompat.checkSelfPermission(this, permission) == PackageManager.PERMISSION_GRANTED

    private fun startInForeground(notification: Notification, video: Boolean) {
        var types = 0
        if (Build.VERSION.SDK_INT >= 29) types = ServiceInfo.FOREGROUND_SERVICE_TYPE_PHONE_CALL
        if (Build.VERSION.SDK_INT >= 30) {
            if (granted(android.Manifest.permission.RECORD_AUDIO)) types = types or ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
            if (video && granted(android.Manifest.permission.CAMERA)) types = types or ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA
        }
        try {
            ServiceCompat.startForeground(this, CallNotifications.ID_ACTIVE, notification, types)
        } catch (e: Exception) {
            Logger.warn("Goppo: foreground service refused types=$types (${e.javaClass.simpleName}); retrying with the call type only")
            try {
                ServiceCompat.startForeground(this, CallNotifications.ID_ACTIVE, notification,
                    if (Build.VERSION.SDK_INT >= 29) ServiceInfo.FOREGROUND_SERVICE_TYPE_PHONE_CALL else 0)
            } catch (e2: Exception) {
                Logger.error("Goppo: foreground service could not start", e2)
                CallNotifications.updateOngoing(this, notification)        // at least show the ongoing notification
                stopSelf()
            }
        }
    }

    companion object {
        const val ACTION_STOP = "com.goppo.chat.action.STOP_CALL_SERVICE"

        /** Starts or refreshes the service (a mute change rebuilds the notification). Safe to call repeatedly. */
        fun start(context: Context) {
            try {
                ContextCompat.startForegroundService(context, Intent(context, ActiveCallService::class.java))
            } catch (e: Exception) {
                Logger.warn("Goppo: foreground service could not be started now (${e.javaClass.simpleName})")
            }
        }

        fun stop(context: Context) {
            try {
                context.startService(Intent(context, ActiveCallService::class.java).setAction(ACTION_STOP))
            } catch (_: Exception) {
                context.stopService(Intent(context, ActiveCallService::class.java))
            }
            CallNotifications.cancelOngoing(context)
        }
    }
}
