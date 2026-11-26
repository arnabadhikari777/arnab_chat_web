package com.goppo.chat.calls

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Notification buttons that need no screen: Decline, End call, Mute. Every action names the call it was made for. */
class CallActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        NativeCallManager.init(context)
        val id = intent.getStringExtra(EXTRA_CALL_ID) ?: return
        // A button of an old notification can never act on a different call.
        if (NativeCallManager.session()?.info?.callId != id) return
        when (intent.action) {
            ACTION_DECLINE -> NativeCallManager.reject(context)
            ACTION_END -> NativeCallManager.endCall(context)
            ACTION_TOGGLE_MUTE -> NativeCallManager.toggleMute()
        }
    }

    companion object {
        const val ACTION_DECLINE = "com.goppo.chat.action.DECLINE_CALL"
        const val ACTION_END = "com.goppo.chat.action.END_CALL"
        const val ACTION_TOGGLE_MUTE = "com.goppo.chat.action.TOGGLE_MUTE"
        const val EXTRA_CALL_ID = "goppo.call_id"
    }
}
