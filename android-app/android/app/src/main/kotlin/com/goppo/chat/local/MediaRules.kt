package com.goppo.chat.local

/** The receiver's own choices (SharedPreferences on the phone). The sender's settings never matter. */
data class MediaSettings(val autoDownload: Boolean = true, val photos: Boolean = true, val videos: Boolean = true)

object AutoDownloadPolicy {
    /**
     * Should this message's media be downloaded automatically?
     *  - only media of OTHER people (never my own upload),
     *  - only a NEW message (phase LIVE, first time this phone sees it) - never history brought in by a restore,
     *  - master switch AND the photo / video switch must be on,
     *  - only after the server has the message (it only exists once the upload succeeded) and a valid URL,
     *  - never if a valid local copy exists, never for deleted messages.
     */
    fun shouldDownload(
        s: MediaSettings, type: String, mine: Boolean, phase: Phase, isNewToThisPhone: Boolean,
        deleted: Boolean, hasUrl: Boolean, alreadyDownloaded: Boolean,
    ): Boolean {
        if (mine || deleted || !hasUrl || alreadyDownloaded) return false
        if (phase != Phase.LIVE || !isNewToThisPhone) return false
        if (!s.autoDownload) return false
        return when (type) { "image" -> s.photos; "video" -> s.videos; else -> false }
    }
}

object DownloadCheck {
    private fun startsWith(b: ByteArray, vararg sig: Int) = b.size >= sig.size && sig.indices.all { (b[it].toInt() and 0xFF) == sig[it] }

    /**
     * A finished download counts only if the size matches what the server said (when it said) and the first bytes look
     * like the kind of file we expect. Anything else is deleted and the media stays "not downloaded".
     */
    fun valid(type: String, expectedSize: Long?, actualSize: Long, head: ByteArray): Boolean {
        if (actualSize <= 0) return false
        if (expectedSize != null && expectedSize > 0 && actualSize != expectedSize) return false
        return when (type) {
            "image" -> startsWith(head, 0xFF, 0xD8, 0xFF) || startsWith(head, 0x89, 0x50, 0x4E, 0x47) ||
                startsWith(head, 0x47, 0x49, 0x46, 0x38) || (startsWith(head, 0x52, 0x49, 0x46, 0x46) && head.size >= 12 && String(head, 8, 4, Charsets.ISO_8859_1) == "WEBP")
            "video" -> (head.size >= 12 && String(head, 4, 4, Charsets.ISO_8859_1) == "ftyp") || startsWith(head, 0x1A, 0x45, 0xDF, 0xA3)
            else -> true
        }
    }
}

object Backoff {
    /** Seconds to wait before retry number [attempt] (0 = first retry): 15 s, 30 s, 1 min ... capped at 15 min. */
    fun seconds(attempt: Int): Long = minOf(900L, 15L shl minOf(attempt.coerceAtLeast(0), 6))

    /** The server said "no" for good (bad request, not found, too large, wrong type): retrying cannot help, so the message
     *  becomes FAILED instead of looping. 401/403 (signed out), 408, 429 and every 5xx / network error are retried. */
    fun isPermanent(httpCode: Int) = httpCode == 400 || httpCode == 404 || httpCode == 413 || httpCode == 415 || httpCode == 422
}
