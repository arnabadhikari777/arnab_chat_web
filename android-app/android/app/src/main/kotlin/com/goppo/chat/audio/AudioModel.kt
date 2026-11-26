package com.goppo.chat.audio

enum class EndpointType(val wire: String) {
    EARPIECE("earpiece"), SPEAKER("speaker"), BLUETOOTH("bluetooth"), WIRED_HEADSET("wired_headset"), OTHER("other");

    companion object {
        fun fromWire(s: String?): EndpointType? = values().firstOrNull { it.wire == s }
    }
}

/** One place the call audio can go. [id] is stable for the life of the device ("earpiece", "speaker", "bluetooth:<id>"...). */
data class AudioEndpoint(val id: String, val type: EndpointType, val name: String)

data class AudioState(val endpoints: List<AudioEndpoint>, val selectedId: String?, val muted: Boolean = false) {
    val selected: AudioEndpoint? get() = endpoints.firstOrNull { it.id == selectedId }
}

object AudioModel {
    // Values of android.telecom.CallAudioState.ROUTE_* (kept here so the logic is testable without Android).
    const val ROUTE_EARPIECE = 1
    const val ROUTE_BLUETOOTH = 2
    const val ROUTE_WIRED_HEADSET = 4
    const val ROUTE_SPEAKER = 8

    fun fromRouteMask(mask: Int): List<AudioEndpoint> = buildList {
        if (mask and ROUTE_EARPIECE != 0) add(AudioEndpoint("earpiece", EndpointType.EARPIECE, "Phone"))
        if (mask and ROUTE_WIRED_HEADSET != 0) add(AudioEndpoint("wired_headset", EndpointType.WIRED_HEADSET, "Wired headset"))
        if (mask and ROUTE_BLUETOOTH != 0) add(AudioEndpoint("bluetooth", EndpointType.BLUETOOTH, "Bluetooth"))
        if (mask and ROUTE_SPEAKER != 0) add(AudioEndpoint("speaker", EndpointType.SPEAKER, "Speaker"))
    }

    fun routeFor(type: EndpointType): Int? = when (type) {
        EndpointType.EARPIECE -> ROUTE_EARPIECE
        EndpointType.WIRED_HEADSET -> ROUTE_WIRED_HEADSET
        EndpointType.BLUETOOTH -> ROUTE_BLUETOOTH
        EndpointType.SPEAKER -> ROUTE_SPEAKER
        EndpointType.OTHER -> null
    }

    fun typeForRoute(route: Int): EndpointType? = when (route) {
        ROUTE_EARPIECE -> EndpointType.EARPIECE
        ROUTE_BLUETOOTH -> EndpointType.BLUETOOTH
        ROUTE_WIRED_HEADSET -> EndpointType.WIRED_HEADSET
        ROUTE_SPEAKER -> EndpointType.SPEAKER
        else -> null
    }

    /** Where a call should start: a connected headset first, then speaker for video, else the earpiece. */
    fun defaultEndpoint(endpoints: List<AudioEndpoint>, video: Boolean): AudioEndpoint? =
        endpoints.firstOrNull { it.type == EndpointType.BLUETOOTH }
            ?: endpoints.firstOrNull { it.type == EndpointType.WIRED_HEADSET }
            ?: (if (video) endpoints.firstOrNull { it.type == EndpointType.SPEAKER } else null)
            ?: endpoints.firstOrNull { it.type == EndpointType.EARPIECE }
            ?: endpoints.firstOrNull()

    /** The endpoint a "speaker on/off" toggle means: on = speaker; off = the best non-speaker endpoint. */
    fun speakerToggle(endpoints: List<AudioEndpoint>, enable: Boolean): AudioEndpoint? =
        if (enable) endpoints.firstOrNull { it.type == EndpointType.SPEAKER }
        else defaultEndpoint(endpoints.filter { it.type != EndpointType.SPEAKER }, video = false)

    /** Keeps the previous choice if it still exists, else falls back to the default (a headset was unplugged). */
    fun reconcile(previousId: String?, endpoints: List<AudioEndpoint>, video: Boolean): String? =
        endpoints.firstOrNull { it.id == previousId }?.id ?: defaultEndpoint(endpoints, video)?.id
}
