package com.goppo.chat.audio

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioDeviceCallback
import android.media.AudioDeviceInfo
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import androidx.core.content.ContextCompat
import com.goppo.chat.GoppoRuntime
import com.goppo.chat.calls.TelecomBridge
import org.json.JSONArray
import org.json.JSONObject

/**
 * Call audio routing: earpiece, speaker, Bluetooth headset, wired headset.
 *
 * Two back ends behind one API:
 *  - Telecom: when the call is registered with Android Telecom, the system owns the route; we read `CallAudioState` and ask
 *    for changes through the Connection (`setAudioRoute`). Telecom also does the audio focus and mode.
 *  - AudioManager: otherwise. Android 12+ uses the communication-device API (getAvailableCommunicationDevices /
 *    setCommunicationDevice, not the deprecated speakerphone/SCO calls); Android 6-11 uses the legacy calls.
 *
 * The media itself stays in the WebView's WebRTC; this only chooses where the sound goes. UNTESTED on real hardware.
 */
object AudioRouter {
    private val main = Handler(Looper.getMainLooper())
    private var context: Context? = null
    private var started = false
    private var video = false
    private var viaTelecom = false
    private var state = AudioState(emptyList(), null)
    private var telecomMask = 0
    private var telecomRoute = 0
    private var focusRequest: AudioFocusRequest? = null
    private var deviceCallback: AudioDeviceCallback? = null
    private var commListener: AudioManager.OnCommunicationDeviceChangedListener? = null

    private fun am(ctx: Context) = ctx.getSystemService(Context.AUDIO_SERVICE) as AudioManager

    fun current(): AudioState = state

    // ------------------------------------------------------------------ lifecycle
    fun start(ctx: Context, isVideo: Boolean, telecom: Boolean) {
        if (started) stop()
        context = ctx.applicationContext
        video = isVideo
        viaTelecom = telecom
        started = true
        if (!telecom) startAudioManagerBackend(ctx.applicationContext)
        refresh(applyDefault = true)
    }

    fun stop() {
        val ctx = context ?: return
        if (!started) return
        started = false
        if (!viaTelecom) stopAudioManagerBackend(ctx)
        state = AudioState(emptyList(), null, state.muted)
        telecomMask = 0
        telecomRoute = 0
    }

    // ------------------------------------------------------------------ API used by the plugin
    fun select(id: String): Boolean {
        val ctx = context ?: return false
        val endpoint = state.endpoints.firstOrNull { it.id == id } ?: return false
        val ok = if (viaTelecom) {
            AudioModel.routeFor(endpoint.type)?.let { TelecomBridge.requestRoute(it) } ?: false
        } else {
            selectViaAudioManager(ctx, endpoint)
        }
        if (ok) main.postDelayed({ refresh(applyDefault = false) }, 150)
        return ok
    }

    fun setSpeaker(enable: Boolean): Boolean {
        val target = AudioModel.speakerToggle(state.endpoints, enable) ?: return false
        return select(target.id)
    }

    fun setMuted(muted: Boolean) {
        if (state.muted == muted) return
        state = state.copy(muted = muted)
        emit("audio:muteChanged", JSONObject().put("muted", muted))
    }

    fun toJson(): JSONObject = JSONObject()
        .put("devices", JSONArray().also { arr -> state.endpoints.forEach { arr.put(endpointJson(it)) } })
        .put("selected", state.selectedId ?: JSONObject.NULL)
        .put("muted", state.muted)
        .put("backend", if (viaTelecom) "telecom" else "audio_manager")

    private fun endpointJson(e: AudioEndpoint) = JSONObject().put("id", e.id).put("type", e.type.wire).put("name", e.name)

    // ------------------------------------------------------------------ Telecom back end
    fun onTelecomAudioState(mask: Int, route: Int) {
        telecomMask = mask
        telecomRoute = route
        if (started && viaTelecom) refresh(applyDefault = false)
    }

    // ------------------------------------------------------------------ AudioManager back end
    private fun startAudioManagerBackend(ctx: Context) {
        val audio = am(ctx)
        requestFocus(audio)
        deviceCallback = object : AudioDeviceCallback() {
            override fun onAudioDevicesAdded(addedDevices: Array<out AudioDeviceInfo>?) = refresh(applyDefault = true)
            override fun onAudioDevicesRemoved(removedDevices: Array<out AudioDeviceInfo>?) = refresh(applyDefault = true)
        }.also { audio.registerAudioDeviceCallback(it, main) }
        if (Build.VERSION.SDK_INT >= 31) {
            commListener = AudioManager.OnCommunicationDeviceChangedListener { refresh(applyDefault = false) }
                .also { audio.addOnCommunicationDeviceChangedListener(ContextCompat.getMainExecutor(ctx), it) }
        }
    }

    private fun stopAudioManagerBackend(ctx: Context) {
        val audio = am(ctx)
        deviceCallback?.let { audio.unregisterAudioDeviceCallback(it) }
        deviceCallback = null
        if (Build.VERSION.SDK_INT >= 31) {
            commListener?.let { audio.removeOnCommunicationDeviceChangedListener(it) }
            commListener = null
            audio.clearCommunicationDevice()                    // back to the system's normal routing
        } else {
            @Suppress("DEPRECATION") run {
                audio.isSpeakerphoneOn = false
                if (audio.isBluetoothScoOn) { audio.stopBluetoothSco(); audio.isBluetoothScoOn = false }
            }
        }
        abandonFocus(audio)
    }

    private fun requestFocus(audio: AudioManager) {
        val attrs = AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build()
        if (Build.VERSION.SDK_INT >= 26) {
            val req = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT).setAudioAttributes(attrs)
                .setOnAudioFocusChangeListener { change -> onFocusChange(change) }.build()
            focusRequest = req
            audio.requestAudioFocus(req)
        } else {
            @Suppress("DEPRECATION")
            audio.requestAudioFocus({ change -> onFocusChange(change) }, AudioManager.STREAM_VOICE_CALL, AudioManager.AUDIOFOCUS_GAIN_TRANSIENT)
        }
    }

    private fun abandonFocus(audio: AudioManager) {
        if (Build.VERSION.SDK_INT >= 26) {
            focusRequest?.let { audio.abandonAudioFocusRequest(it) }
            focusRequest = null
        } else {
            @Suppress("DEPRECATION") audio.abandonAudioFocus(null)
        }
    }

    private fun onFocusChange(change: Int) {
        // A regular phone call (or another app) took audio: tell the website so the person sees it; the call itself stays up.
        if (change == AudioManager.AUDIOFOCUS_LOSS || change == AudioManager.AUDIOFOCUS_LOSS_TRANSIENT) {
            emit("audio:routeChanged", toJson().put("focus", "lost"))
        }
    }

    private fun toEndpoint(d: AudioDeviceInfo): AudioEndpoint? = when (d.type) {
        AudioDeviceInfo.TYPE_BUILTIN_EARPIECE -> AudioEndpoint("earpiece", EndpointType.EARPIECE, "Phone")
        AudioDeviceInfo.TYPE_BUILTIN_SPEAKER -> AudioEndpoint("speaker", EndpointType.SPEAKER, "Speaker")
        AudioDeviceInfo.TYPE_WIRED_HEADSET, AudioDeviceInfo.TYPE_WIRED_HEADPHONES, AudioDeviceInfo.TYPE_USB_HEADSET ->
            AudioEndpoint("wired_headset:${d.id}", EndpointType.WIRED_HEADSET, d.productName?.toString()?.ifBlank { null } ?: "Wired headset")
        AudioDeviceInfo.TYPE_BLUETOOTH_SCO, AudioDeviceInfo.TYPE_HEARING_AID ->
            AudioEndpoint("bluetooth:${d.id}", EndpointType.BLUETOOTH, d.productName?.toString()?.ifBlank { null } ?: "Bluetooth")
        else -> if (Build.VERSION.SDK_INT >= 31 && d.type == AudioDeviceInfo.TYPE_BLE_HEADSET)
            AudioEndpoint("bluetooth:${d.id}", EndpointType.BLUETOOTH, d.productName?.toString()?.ifBlank { null } ?: "Bluetooth") else null
    }

    private fun communicationDevices(audio: AudioManager): List<AudioDeviceInfo> =
        if (Build.VERSION.SDK_INT >= 31) audio.availableCommunicationDevices else audio.getDevices(AudioManager.GET_DEVICES_OUTPUTS).toList()

    private fun selectViaAudioManager(ctx: Context, endpoint: AudioEndpoint): Boolean {
        val audio = am(ctx)
        if (Build.VERSION.SDK_INT >= 31) {
            val device = communicationDevices(audio).firstOrNull { toEndpoint(it)?.id == endpoint.id } ?: return false
            return audio.setCommunicationDevice(device)
        }
        @Suppress("DEPRECATION") run {
            when (endpoint.type) {
                EndpointType.SPEAKER -> { audio.stopBluetoothSco(); audio.isBluetoothScoOn = false; audio.isSpeakerphoneOn = true }
                EndpointType.BLUETOOTH -> { audio.isSpeakerphoneOn = false; audio.startBluetoothSco(); audio.isBluetoothScoOn = true }
                else -> { audio.stopBluetoothSco(); audio.isBluetoothScoOn = false; audio.isSpeakerphoneOn = false }
            }
        }
        return true
    }

    private fun selectedFromAudioManager(ctx: Context, endpoints: List<AudioEndpoint>): String? {
        val audio = am(ctx)
        if (Build.VERSION.SDK_INT >= 31) return audio.communicationDevice?.let { toEndpoint(it)?.id }
        @Suppress("DEPRECATION") run {
            if (audio.isSpeakerphoneOn) return "speaker"
            if (audio.isBluetoothScoOn) return endpoints.firstOrNull { it.type == EndpointType.BLUETOOTH }?.id
        }
        return endpoints.firstOrNull { it.type == EndpointType.WIRED_HEADSET }?.id ?: "earpiece"
    }

    // ------------------------------------------------------------------ state + events
    private fun refresh(applyDefault: Boolean) {
        val ctx = context ?: return
        if (!started) return
        val endpoints: List<AudioEndpoint>
        var selected: String?
        if (viaTelecom) {
            endpoints = AudioModel.fromRouteMask(telecomMask)
            selected = AudioModel.typeForRoute(telecomRoute)?.let { t -> endpoints.firstOrNull { it.type == t }?.id }
        } else {
            val seen = HashSet<String>()
            endpoints = communicationDevices(am(ctx)).mapNotNull { toEndpoint(it) }.filter { seen.add(it.id) }
                .let { list ->                                                  // Android < 12 reports every output: keep one of each kind
                    if (Build.VERSION.SDK_INT >= 31) list else list.distinctBy { it.type }
                }
            selected = selectedFromAudioManager(ctx, endpoints)
        }
        val previous = state
        val devicesChanged = previous.endpoints != endpoints
        if (applyDefault && selected == null) {
            AudioModel.defaultEndpoint(endpoints, video)?.let { select(it.id) }
        }
        if (applyDefault && !viaTelecom && video && selected != null && selected.startsWith("earpiece")) {
            AudioModel.defaultEndpoint(endpoints, true)?.takeIf { it.type == EndpointType.SPEAKER }?.let { select(it.id) }
        }
        selected = AudioModel.reconcile(selected, endpoints, video).takeIf { selected != null } ?: selected
        state = AudioState(endpoints, selected, previous.muted)
        if (devicesChanged) emit("audio:devicesChanged", toJson())
        if (previous.selectedId != selected) emit("audio:routeChanged", toJson())
    }

    private fun emit(command: String, detail: JSONObject) = main.post { GoppoRuntime.emitToWeb(command, detail) }
}
