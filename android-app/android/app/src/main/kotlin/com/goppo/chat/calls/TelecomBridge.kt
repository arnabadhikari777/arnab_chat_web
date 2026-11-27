package com.goppo.chat.calls

import android.content.ComponentName
import android.content.Context
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.telecom.CallAudioState
import android.telecom.Connection
import android.telecom.ConnectionRequest
import android.telecom.ConnectionService
import android.telecom.DisconnectCause
import android.telecom.PhoneAccount
import android.telecom.PhoneAccountHandle
import android.telecom.TelecomManager
import androidx.core.content.ContextCompat
import com.getcapacitor.Logger
import com.goppo.chat.R
import com.goppo.chat.audio.AudioRouter
import java.util.concurrent.ConcurrentHashMap

/**
 * Android Telecom integration (self-managed ConnectionService, API 26+).
 *
 * Telecom only COORDINATES the call with the rest of the phone: it makes Android know a VoIP call exists (so a regular
 * cellular call cannot silently collide with it), lets the headset button / Bluetooth / car answer or hang up, owns the audio
 * route and focus while the call is registered, and allows the foreground service to start from the background. The media and
 * the signaling remain the website's WebRTC; Telecom never carries a second call protocol.
 *
 * Everything here is best effort: if Telecom refuses (no permission, another call, old Android) the call continues with the
 * notification + foreground service + AudioManager routing.
 *
 * Why not Jetpack Core-Telecom: see docs/PHASE3_REPORT.md (it wraps this same service on Android < 14 and could not be
 * compile-verified in the build environment used for Phase 3).
 */
object TelecomBridge {
    private const val ACCOUNT_ID = "goppo_self_managed"
    const val EXTRA_CALL_ID = "goppo.call_id"
    const val EXTRA_PEER = "goppo.peer"
    private val connections = ConcurrentHashMap<String, GoppoConnection>()
    private var registered = false

    fun supported(context: Context): Boolean =
        Build.VERSION.SDK_INT >= 26 &&
            ContextCompat.checkSelfPermission(context, android.Manifest.permission.MANAGE_OWN_CALLS) == android.content.pm.PackageManager.PERMISSION_GRANTED

    private fun handle(context: Context) = PhoneAccountHandle(ComponentName(context, GoppoConnectionService::class.java), ACCOUNT_ID)

    private fun telecom(context: Context): TelecomManager? = context.getSystemService(TelecomManager::class.java)

    @Synchronized
    private fun register(context: Context): Boolean {
        if (registered) return true
        return try {
            val account = PhoneAccount.builder(handle(context), context.getString(R.string.app_name))
                .setCapabilities(PhoneAccount.CAPABILITY_SELF_MANAGED)
                .addSupportedUriScheme(PhoneAccount.SCHEME_SIP).build()
            telecom(context)?.registerPhoneAccount(account)
            registered = true
            true
        } catch (e: Exception) {
            Logger.warn("Goppo: Telecom account registration failed: ${e.javaClass.simpleName}")
            false
        }
    }

    private fun address(callId: String): Uri = Uri.fromParts(PhoneAccount.SCHEME_SIP, "call-$callId", null)

    /** Tells Telecom a call is ringing. Returns false if Telecom is not available (the caller carries on without it). */
    fun addIncoming(context: Context, info: CallInfo): Boolean {
        if (!supported(context) || !register(context)) return false
        return try {
            val extras = Bundle().apply {
                putParcelable(TelecomManager.EXTRA_INCOMING_CALL_ADDRESS, address(info.callId))
                putParcelable(TelecomManager.EXTRA_PHONE_ACCOUNT_HANDLE, handle(context))
                putBundle(TelecomManager.EXTRA_INCOMING_CALL_EXTRAS, Bundle().apply { putString(EXTRA_CALL_ID, info.callId); putString(EXTRA_PEER, info.peerName) })
            }
            telecom(context)?.addNewIncomingCall(handle(context), extras)
            true
        } catch (e: Exception) {
            Logger.warn("Goppo: Telecom refused the incoming call: ${e.javaClass.simpleName}")
            false
        }
    }

    /** Registers a call this phone started (the website just reported it connected). */
    fun placeOutgoing(context: Context, info: CallInfo): Boolean {
        if (!supported(context) || !register(context)) return false
        return try {
            val extras = Bundle().apply {
                putParcelable(TelecomManager.EXTRA_PHONE_ACCOUNT_HANDLE, handle(context))
                putBundle(TelecomManager.EXTRA_OUTGOING_CALL_EXTRAS, Bundle().apply { putString(EXTRA_CALL_ID, info.callId); putString(EXTRA_PEER, info.peerName) })
            }
            telecom(context)?.placeCall(address(info.callId), extras)
            true
        } catch (e: Exception) {
            Logger.warn("Goppo: Telecom refused the outgoing call: ${e.javaClass.simpleName}")
            false
        }
    }

    fun hasConnection(callId: String) = connections.containsKey(callId)

    fun anyConnection(): Boolean = connections.isNotEmpty()

    internal fun add(callId: String, c: GoppoConnection) { connections[callId] = c }

    /** The user answered (our UI, headset button...). */
    fun markActive(callId: String) { connections[callId]?.setActive() }

    fun finish(callId: String, cause: Int) {
        val c = connections.remove(callId) ?: return
        try {
            c.setDisconnected(DisconnectCause(cause))
            c.destroy()
        } catch (_: Exception) {
        }
    }

    /** CallAudioState.ROUTE_* of the active Telecom call. */
    @Suppress("DEPRECATION")
    fun requestRoute(route: Int): Boolean {
        val c = connections.values.firstOrNull() ?: return false
        return try { c.setAudioRoute(route); true } catch (_: Exception) { false }
    }
}

/** Telecom binds this service to create the call objects. */
class GoppoConnectionService : ConnectionService() {

    private fun callId(request: ConnectionRequest?, key: String): String? =
        (request?.extras?.getBundle(key) ?: request?.extras)?.getString(TelecomBridge.EXTRA_CALL_ID)

    override fun onCreateIncomingConnection(account: PhoneAccountHandle?, request: ConnectionRequest?): Connection {
        val id = callId(request, TelecomManager.EXTRA_INCOMING_CALL_EXTRAS) ?: return Connection.createFailedConnection(DisconnectCause(DisconnectCause.ERROR))
        // addNewIncomingCall() is asynchronous: if the call was cancelled / rejected / answered elsewhere before Telecom got here,
        // creating the connection now would leave a ringing ghost that nothing ever finishes.
        if (NativeCallManager.session()?.info?.callId != id) return Connection.createFailedConnection(DisconnectCause(DisconnectCause.REMOTE))
        val peer = (request?.extras?.getBundle(TelecomManager.EXTRA_INCOMING_CALL_EXTRAS))?.getString(TelecomBridge.EXTRA_PEER) ?: ""
        return GoppoConnection(id, peer).also { it.setRinging(); TelecomBridge.add(id, it) }
    }

    override fun onCreateOutgoingConnection(account: PhoneAccountHandle?, request: ConnectionRequest?): Connection {
        val id = callId(request, TelecomManager.EXTRA_OUTGOING_CALL_EXTRAS) ?: return Connection.createFailedConnection(DisconnectCause(DisconnectCause.ERROR))
        if (NativeCallManager.session()?.info?.callId != id) return Connection.createFailedConnection(DisconnectCause(DisconnectCause.REMOTE))
        val peer = (request?.extras?.getBundle(TelecomManager.EXTRA_OUTGOING_CALL_EXTRAS))?.getString(TelecomBridge.EXTRA_PEER) ?: ""
        // The website already reports the call as connected when this is created.
        return GoppoConnection(id, peer).also { it.setActive(); TelecomBridge.add(id, it) }
    }

    override fun onCreateIncomingConnectionFailed(account: PhoneAccountHandle?, request: ConnectionRequest?) {
        Logger.warn("Goppo: Telecom could not create the incoming connection; continuing without it")
    }

    override fun onCreateOutgoingConnectionFailed(account: PhoneAccountHandle?, request: ConnectionRequest?) {
        Logger.warn("Goppo: Telecom could not create the outgoing connection; continuing without it")
    }
}

class GoppoConnection(private val callId: String, peerName: String) : Connection() {
    init {
        connectionProperties = PROPERTY_SELF_MANAGED
        connectionCapabilities = CAPABILITY_HOLD or CAPABILITY_SUPPORT_HOLD
        audioModeIsVoip = true
        setAddress(Uri.fromParts(PhoneAccount.SCHEME_SIP, "call-$callId", null), TelecomManager.PRESENTATION_ALLOWED)
        setCallerDisplayName(peerName, TelecomManager.PRESENTATION_ALLOWED)
    }

    override fun onAnswer() {
        setActive()
        NativeCallManager.onTelecomAnswer(callId)
    }

    override fun onAnswer(videoState: Int) = onAnswer()

    override fun onReject() {
        NativeCallManager.onTelecomReject(callId)
        TelecomBridge.finish(callId, DisconnectCause.REJECTED)
    }

    override fun onDisconnect() {
        NativeCallManager.onTelecomDisconnect(callId)
        TelecomBridge.finish(callId, DisconnectCause.LOCAL)
    }

    override fun onAbort() {
        TelecomBridge.finish(callId, DisconnectCause.CANCELED)
    }

    override fun onHold() {
        setOnHold()                                      // e.g. a regular phone call answered: mute our mic, keep the call
        NativeCallManager.onTelecomHold(callId, true)
    }

    override fun onUnhold() {
        setActive()
        NativeCallManager.onTelecomHold(callId, false)
    }

    @Suppress("DEPRECATION", "OVERRIDE_DEPRECATION")
    override fun onCallAudioStateChanged(state: CallAudioState?) {
        if (state != null) AudioRouter.onTelecomAudioState(state.supportedRouteMask, state.route)
    }
}
