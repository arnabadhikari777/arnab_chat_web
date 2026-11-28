package com.goppo.chat

import com.goppo.chat.local.*
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

class LocalFirstLogicTest {
    private fun srv(id: Long, status: String = "sent", deleted: Boolean = false, body: String = "hi", mine: Boolean = false, hidden: Boolean = false, rev: Long = id) =
        ServerMessage(id, "uuid-$id", 1, if (mine) 1 else 2, "text", if (deleted) "" else body, status, "2026-11-27T10:00:00Z",
            null, null, deleted, null, hidden, rev, mine, null)

    private fun local(m: ServerMessage) = Merge.apply(null, m, 100L).row

    // ---------------------------------------------------------------- status
    @Test fun statusOnlyMovesForward() {
        assertEquals("seen", Status.forward("seen", "delivered"))
        assertEquals("delivered", Status.forward("sent", "delivered"))
        assertEquals("seen", Status.forward("delivered", "seen"))
        assertEquals("sent", Status.forward("sending", "sent"))
    }

    @Test fun pendingStatesAreNeverShownAsSent() {
        assertTrue(Status.isPending(Status.SENDING)); assertTrue(Status.isPending(Status.WAITING))
        assertFalse(Status.isPending(Status.SENT)); assertFalse(Status.isPending(Status.FAILED))
    }

    @Test fun theLocalRowKeepsItsOwnUuidWhenTheServerRecordCarriesAnother() {
        // LocalStore stores a peer's message that reuses a uuid under a derived uuid; later merges must keep that key.
        val existing = Merge.apply(null, srv(7).copy(uuid = "u~7"), 1L).row
        val again = Merge.apply(existing, srv(7).copy(uuid = "u"), 2L)
        assertEquals("u~7", again.row.uuid)
    }

    // ---------------------------------------------------------------- merge
    @Test fun newServerMessageIsNewToThisPhone() {
        val r = Merge.apply(null, srv(5), 1L)
        assertTrue(r.isNewToThisPhone); assertEquals(5L, r.row.serverId); assertEquals("sent", r.row.status)
    }

    @Test fun mergingTheSameMessageTwiceKeepsOneRowAndIsNotNewTheSecondTime() {
        val first = Merge.apply(null, srv(5), 1L).row.copy(localId = 9)
        val second = Merge.apply(first, srv(5), 2L)
        assertFalse(second.isNewToThisPhone); assertEquals(9L, second.row.localId); assertEquals(1L, second.row.localCreatedAt)
    }

    @Test fun anOutboxMessageBecomesSentOnlyWhenTheServerConfirmsIt() {
        val pending = LocalMessage(3, null, "uuid-5", 1, 1, true, "text", "hello", Status.WAITING, 50, null, null, null, false, false, 0)
        val confirmed = Merge.apply(pending, srv(5, mine = true), 99L)
        assertEquals("sent", confirmed.row.status); assertEquals(5L, confirmed.row.serverId); assertEquals(50L, confirmed.row.localCreatedAt)
    }

    @Test fun aStalePageCannotTurnBlueTicksBackIntoGreyOnes() {
        val seen = local(srv(5, "seen"))
        assertEquals("seen", Merge.apply(seen, srv(5, "sent"), 1L).row.status)
    }

    @Test fun deletedForEveryoneIsStickyAndRemovesTheText() {
        val live = local(srv(5, body = "secret"))
        val gone = Merge.apply(live, srv(5, deleted = true), 1L)
        assertTrue(gone.row.deleted); assertEquals("", gone.row.body); assertTrue(gone.tombstoned)
        val retry = Merge.apply(gone.row, srv(5, body = "secret"), 2L)               // an old retry / stale page
        assertTrue(retry.row.deleted); assertEquals("", retry.row.body); assertFalse(retry.tombstoned)
    }

    @Test fun hiddenForMeKeepsNoText() {
        val r = Merge.apply(null, srv(5, body = "x", hidden = true), 1L)
        assertTrue(r.row.hidden); assertEquals("", r.row.body)
    }

    @Test fun orderingIsServerOrderThenPendingByLocalTime() {
        val a = local(srv(2)); val b = local(srv(1))
        val p1 = a.copy(serverId = null, localCreatedAt = 20, uuid = "p1"); val p2 = a.copy(serverId = null, localCreatedAt = 10, uuid = "p2")
        assertEquals(listOf("uuid-1", "uuid-2", "p2", "p1"), listOf(a, b, p1, p2).sortedWith(Merge.ORDER).map { it.uuid })
    }

    @Test fun cursorOnlyMovesForward() {
        val c = SyncCursor(5, 7)
        assertEquals(SyncCursor(5, 8), c.advancedTo(SyncCursor(5, 8)))
        assertEquals(c, c.advancedTo(SyncCursor(5, 3)))
        assertEquals(c, c.advancedTo(SyncCursor(4, 99)))
        assertEquals(SyncCursor(6, 0), c.advancedTo(SyncCursor(6, 0)))
    }

    // ---------------------------------------------------------------- auto download
    private fun dl(s: MediaSettings = MediaSettings(), type: String = "image", mine: Boolean = false, phase: Phase = Phase.LIVE,
                   isNew: Boolean = true, deleted: Boolean = false, url: Boolean = true, have: Boolean = false) =
        AutoDownloadPolicy.shouldDownload(s, type, mine, phase, isNew, deleted, url, have)

    @Test fun newPhotoAutoDownloadOnAndOff() {
        assertTrue(dl()); assertFalse(dl(MediaSettings(photos = false))); assertFalse(dl(MediaSettings(autoDownload = false)))
    }

    @Test fun newVideoAutoDownloadOnAndOff() {
        assertTrue(dl(type = "video")); assertFalse(dl(MediaSettings(videos = false), type = "video"))
        assertFalse(dl(MediaSettings(autoDownload = false), type = "video"))
        assertTrue("photo switch does not control videos", dl(MediaSettings(photos = false), type = "video"))
    }

    @Test fun historyIsNeverDownloadedAutomatically() {
        assertFalse(dl(phase = Phase.RESTORING)); assertFalse(dl(isNew = false))
    }

    @Test fun neverMyOwnNeverDeletedNeverTwiceNeverWithoutUrl() {
        assertFalse(dl(mine = true)); assertFalse(dl(deleted = true)); assertFalse(dl(have = true)); assertFalse(dl(url = false))
    }

    @Test fun voiceAndDocumentsAreNotAutoDownloaded() { assertFalse(dl(type = "voice")); assertFalse(dl(type = "document")) }

    // ---------------------------------------------------------------- download validation
    private val jpg = byteArrayOf(0xFF.toByte(), 0xD8.toByte(), 0xFF.toByte(), 0xE0.toByte(), 0, 0, 0, 0, 0, 0, 0, 0)
    private val mp4 = byteArrayOf(0, 0, 0, 0x18, 'f'.code.toByte(), 't'.code.toByte(), 'y'.code.toByte(), 'p'.code.toByte(), 0, 0, 0, 0)

    @Test fun validImageAndVideoPass() {
        assertTrue(DownloadCheck.valid("image", 1000, 1000, jpg)); assertTrue(DownloadCheck.valid("video", null, 5000, mp4))
    }

    @Test fun corruptOrIncompleteDownloadsAreRejected() {
        assertFalse(DownloadCheck.valid("image", 1000, 400, jpg))                         // interrupted: size differs
        assertFalse(DownloadCheck.valid("image", 1000, 1000, ByteArray(12)))              // an HTML error page / garbage
        assertFalse(DownloadCheck.valid("video", null, 0, mp4))                           // empty
        assertFalse(DownloadCheck.valid("video", 10, 10, jpg))                            // wrong kind of file
    }

    // ---------------------------------------------------------------- retry
    @Test fun backoffGrowsAndIsCapped() {
        assertEquals(15L, Backoff.seconds(0)); assertEquals(30L, Backoff.seconds(1)); assertEquals(900L, Backoff.seconds(20))
    }

    @Test fun onlyFinalAnswersAreNotRetried() {
        assertTrue(Backoff.isPermanent(400)); assertTrue(Backoff.isPermanent(413))
        assertFalse(Backoff.isPermanent(500)); assertFalse(Backoff.isPermanent(0)); assertFalse(Backoff.isPermanent(429)); assertFalse(Backoff.isPermanent(401))
    }
}
