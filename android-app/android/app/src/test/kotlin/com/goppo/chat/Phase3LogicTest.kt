package com.goppo.chat

import com.goppo.chat.audio.AudioEndpoint
import com.goppo.chat.audio.AudioModel
import com.goppo.chat.audio.EndpointType
import com.goppo.chat.calls.CallDirection
import com.goppo.chat.calls.CallInfo
import com.goppo.chat.calls.CallPhase
import com.goppo.chat.calls.CallSessions
import com.goppo.chat.calls.IncomingDecision
import com.goppo.chat.push.PushMessage
import com.goppo.chat.push.PushParser
import com.goppo.chat.push.SitePaths
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

private const val CALL = "0f8fad5b-d9cb-469f-a165-70867728950e"
private const val NOW = 1_700_000_000_000L

class PushParserTest {
    private fun call(extra: Map<String, String> = emptyMap()) = mapOf(
        "type" to "incoming_call", "call_id" to CALL, "call_type" to "video", "caller_name" to "Alice  ", "caller_id" to "7",
        "avatar_url" to "https://res.cloudinary.com/x/a.jpg", "conversation_id" to "12", "path" to "/chat/12?incoming=$CALL",
        "expires_at" to (NOW + 55_000).toString(), "sent_at" to NOW.toString(), "notification_id" to "99",
    ) + extra

    @Test fun syncHintIsParsedAndCarriesNoContent() {
        val m = PushParser.parse(mapOf("type" to "sync", "reason" to "deleted!!", "conversation_id" to "5"), NOW) as PushMessage.SyncHint
        assertEquals("deleted", m.reason)
    }

    @Test fun incomingCallIsParsedAndCleaned() {
        val m = PushParser.parse(call(), NOW) as PushMessage.IncomingCall
        assertEquals(CALL, m.callId); assertTrue(m.video); assertEquals("Alice", m.callerName); assertEquals("7", m.callerId)
        assertEquals("/chat/12?incoming=$CALL", m.path); assertEquals(NOW + 55_000, m.expiresAtMs)
        assertEquals("https://res.cloudinary.com/x/a.jpg", m.avatarUrl)
    }

    @Test fun aFarFutureExpiryIsClamped() {
        val m = PushParser.parse(call(mapOf("expires_at" to (NOW + 10 * 60_000).toString())), NOW) as PushMessage.IncomingCall
        assertEquals(NOW + PushParser.MAX_CALL_WINDOW_MS, m.expiresAtMs)
    }

    @Test fun missingExpiryDefaultsToOneMinuteAfterSending() {
        val m = PushParser.parse(call() - "expires_at", NOW) as PushMessage.IncomingCall
        assertEquals(NOW + PushParser.DEFAULT_CALL_WINDOW_MS, m.expiresAtMs)
    }

    @Test fun badIdsAndUnknownTypesAreInvalid() {
        assertTrue(PushParser.parse(call(mapOf("call_id" to "../../x")), NOW) is PushMessage.Invalid)
        assertTrue(PushParser.parse(call(mapOf("call_id" to "")), NOW) is PushMessage.Invalid)
        assertTrue(PushParser.parse(mapOf("type" to "wipe_data"), NOW) is PushMessage.Invalid)
        assertTrue(PushParser.parse(emptyMap(), NOW) is PushMessage.Invalid)
        assertTrue(PushParser.parse(mapOf("type" to "new_message", "conversation_id" to "abc"), NOW) is PushMessage.Invalid)
    }

    @Test fun unsafePathsAndAvatarsAreDropped() {
        val m = PushParser.parse(call(mapOf("path" to "https://evil.example/x", "avatar_url" to "http://x/a.jpg")), NOW) as PushMessage.IncomingCall
        assertNull(m.path); assertNull(m.avatarUrl)
        val n = PushParser.parse(mapOf("type" to "new_message", "conversation_id" to "5", "path" to "//evil.example"), NOW) as PushMessage.NewMessage
        assertEquals("/chat/5", n.path)                                        // falls back to the conversation itself
    }

    @Test fun messageFieldsAreBoundedAndControlCharactersRemoved() {
        val long = "x".repeat(500)
        val n = PushParser.parse(mapOf("type" to "new_message", "conversation_id" to "5", "sender_name" to "Bo\nb", "body" to long, "count" to "100000", "ack" to "tok"), NOW) as PushMessage.NewMessage
        assertEquals("Bob", n.senderName); assertEquals(200, n.body.length); assertEquals(999, n.count); assertNull(n.ack)
    }

    @Test fun otherTypes() {
        assertTrue(PushParser.parse(mapOf("type" to "call_cancelled", "call_id" to CALL, "reason" to "missed"), NOW) is PushMessage.CallCancelled)
        assertTrue(PushParser.parse(mapOf("type" to "missed_call", "call_id" to CALL, "caller_name" to "A", "body" to "Missed"), NOW) is PushMessage.MissedCall)
        assertTrue(PushParser.parse(mapOf("type" to "announcement", "body" to "Hi"), NOW) is PushMessage.Notice)
    }

    @Test fun sitePaths() {
        assertEquals("/chat/5", SitePaths.safe("/chat/5"))
        listOf(null, "", "chat/5", "//x.y/z", "/a\\b", "/a b", "/a\nb", "https://x.y", "javascript:alert(1)", "/" + "a".repeat(400)).forEach { assertNull(it, SitePaths.safe(it)) }
        assertEquals(12L, SitePaths.conversationId("/chat/12")); assertEquals(12L, SitePaths.conversationId("/chat/12?incoming=x#y"))
        assertNull(SitePaths.conversationId("/chat/12abc")); assertNull(SitePaths.conversationId("/settings")); assertNull(SitePaths.conversationId(null))
    }
}

class CallSessionsTest {
    private var now = NOW
    private val s = CallSessions { now }
    private fun info(id: String = CALL, dir: CallDirection = CallDirection.INCOMING) = CallInfo(id, false, "Alice", "7", null, "12", "/chat/12", dir)
    private val other = "11111111-2222-4333-8444-555555555555"

    @Test fun ringOnceThenDuplicateIsIgnored() {
        assertEquals(IncomingDecision.NEW, s.onIncoming(info(), NOW + 60_000))
        assertEquals(IncomingDecision.DUPLICATE, s.onIncoming(info(), NOW + 60_000))
        assertEquals(CallPhase.RINGING, s.session!!.phase)
    }

    @Test fun aSecondCallWhileBusyIsNotShown() {
        s.onIncoming(info(), NOW + 60_000)
        assertEquals(IncomingDecision.BUSY, s.onIncoming(info(other), NOW + 60_000))
        assertEquals(CALL, s.session!!.info.callId)
    }

    @Test fun anExpiredInvitationNeverRingsAndNeverComesBack() {
        assertEquals(IncomingDecision.EXPIRED, s.onIncoming(info(), NOW - 1))
        assertEquals(IncomingDecision.STALE, s.onIncoming(info(), NOW + 60_000))
        assertNull(s.session)
    }

    @Test fun aFinishedCallCannotBeRepliedByAStaleNotification() {
        s.onIncoming(info(), NOW + 60_000); s.onAccept(CALL)
        assertTrue(s.onEnded(CALL))
        assertEquals(IncomingDecision.STALE, s.onIncoming(info(), NOW + 60_000))
        assertFalse(s.onAccept(CALL))
    }

    @Test fun acceptOnlyWorksOnceAndOnlyWhileRinging() {
        s.onIncoming(info(), NOW + 60_000)
        assertFalse(s.onAccept(other))
        assertTrue(s.onAccept(CALL))
        assertFalse(s.onAccept(CALL))                                           // double tap
        assertEquals(CallPhase.ACCEPTING, s.session!!.phase)
    }

    @Test fun cannotAcceptAfterTheRingTimeIsOver() {
        s.onIncoming(info(), NOW + 60_000)
        now = NOW + 60_001
        assertFalse(s.onAccept(CALL))
    }

    @Test fun cancelDismissesARingingCallButNotOneThatWasAnsweredHere() {
        s.onIncoming(info(), NOW + 60_000)
        assertTrue(s.onCancelled(CALL, "answered_elsewhere"))
        assertNull(s.session)
        val s2 = CallSessions { now }
        s2.onIncoming(info(), NOW + 60_000); s2.onAccept(CALL)
        assertFalse(s2.onCancelled(CALL, "answered_elsewhere")); assertFalse(s2.onCancelled(CALL, "handled_elsewhere"))
        assertEquals(CallPhase.ACCEPTING, s2.session!!.phase)
        assertTrue(s2.onCancelled(CALL, "cancelled"))                           // the caller hung up while we were connecting
    }

    @Test fun aCancelThatArrivesBeforeTheRingPreventsTheRing() {
        assertFalse(s.onCancelled(CALL, "cancelled"))
        assertEquals(IncomingDecision.STALE, s.onIncoming(info(), NOW + 60_000))
    }

    @Test fun connectedCallLifecycleAndMute() {
        s.onIncoming(info(), NOW + 60_000); s.onAccept(CALL)
        assertTrue(s.onConnected(info()))
        assertEquals(CallPhase.ACTIVE, s.session!!.phase); assertEquals(NOW, s.session!!.connectedAtMs)
        assertTrue(s.onMuted(CALL, true)); assertTrue(s.session!!.muted)
        assertFalse(s.onMuted(other, false))
        assertTrue(s.onEnded(CALL)); assertNull(s.session)
        assertFalse(s.onEnded(CALL))                                            // repeated end is harmless
    }

    @Test fun anOutgoingCallBecomesActiveWhenTheWebsiteSaysConnected() {
        assertTrue(s.onConnected(info(dir = CallDirection.OUTGOING)))
        assertEquals(CallDirection.OUTGOING, s.session!!.info.direction)
        assertFalse(s.onConnected(info(other, CallDirection.OUTGOING)))         // never two calls at once
    }

    @Test fun finishedCallsAreRememberedBoundedAndForAWhile() {
        for (i in 0 until 40) s.onEnded("id-$i")
        assertFalse(s.isFinished("id-0")); assertTrue(s.isFinished("id-39"))
        now = NOW + CallSessions.FINISHED_KEEP_MS + 1
        s.onEnded("fresh")
        assertFalse(s.isFinished("id-39")); assertTrue(s.isFinished("fresh"))
    }
}

class AudioModelTest {
    private val all = AudioModel.fromRouteMask(15)
    private fun types(l: List<AudioEndpoint>) = l.map { it.type }

    @Test fun routeMaskBecomesEndpoints() {
        assertEquals(listOf(EndpointType.EARPIECE, EndpointType.WIRED_HEADSET, EndpointType.BLUETOOTH, EndpointType.SPEAKER), types(all))
        assertEquals(listOf(EndpointType.EARPIECE, EndpointType.SPEAKER), types(AudioModel.fromRouteMask(9)))
        assertTrue(AudioModel.fromRouteMask(0).isEmpty())
    }

    @Test fun routeAndTypeMapBothWays() {
        EndpointType.values().filter { it != EndpointType.OTHER }.forEach { assertEquals(it, AudioModel.typeForRoute(AudioModel.routeFor(it)!!)) }
        assertNull(AudioModel.routeFor(EndpointType.OTHER)); assertNull(AudioModel.typeForRoute(3))
        assertEquals(EndpointType.SPEAKER, EndpointType.fromWire("speaker")); assertNull(EndpointType.fromWire("x"))
    }

    @Test fun defaultsPreferHeadsetsThenSpeakerForVideoThenEarpiece() {
        assertEquals(EndpointType.BLUETOOTH, AudioModel.defaultEndpoint(all, false)!!.type)
        assertEquals(EndpointType.WIRED_HEADSET, AudioModel.defaultEndpoint(AudioModel.fromRouteMask(13), false)!!.type)
        assertEquals(EndpointType.EARPIECE, AudioModel.defaultEndpoint(AudioModel.fromRouteMask(9), false)!!.type)
        assertEquals(EndpointType.SPEAKER, AudioModel.defaultEndpoint(AudioModel.fromRouteMask(9), true)!!.type)
        assertEquals(EndpointType.SPEAKER, AudioModel.defaultEndpoint(AudioModel.fromRouteMask(8), false)!!.type)
        assertNull(AudioModel.defaultEndpoint(emptyList(), false))
    }

    @Test fun speakerToggle() {
        assertEquals(EndpointType.SPEAKER, AudioModel.speakerToggle(all, true)!!.type)
        assertEquals(EndpointType.BLUETOOTH, AudioModel.speakerToggle(all, false)!!.type)
        assertEquals(EndpointType.EARPIECE, AudioModel.speakerToggle(AudioModel.fromRouteMask(9), false)!!.type)
        assertNull(AudioModel.speakerToggle(AudioModel.fromRouteMask(1), true))
    }

    @Test fun aRemovedDeviceFallsBackAndAKeptOneIsKept() {
        assertEquals("speaker", AudioModel.reconcile("speaker", all, false))
        assertEquals("earpiece", AudioModel.reconcile("bluetooth", AudioModel.fromRouteMask(9), false))
        assertEquals("bluetooth", AudioModel.reconcile(null, all, false))
        assertNotNull(AudioModel.reconcile("x", all, true))
    }
}

/** Regression tests for the Phase 3 bug-fix pass (PHASE3_BUGFIX_REPORT.md). */
class CallSessionsBugfixTest {
    private var now = NOW
    private val s = CallSessions { now }
    private fun info(id: String = CALL, dir: CallDirection = CallDirection.INCOMING) = CallInfo(id, false, "Alice", "7", null, "12", "/chat/12", dir)
    private val other = "11111111-2222-4333-8444-555555555555"

    @Test fun aRepeatedConnectedEventDoesNotRestartTheCall() {            // ICE restart after a network change re-reports "connected"
        s.onIncoming(info(), NOW + 60_000); s.onAccept(CALL)
        assertTrue(s.onConnected(info()))
        val first = s.session!!.connectedAtMs
        now += 30_000
        assertFalse(s.onConnected(info()))                               // false = the native layer must not restart audio / service
        assertEquals(first, s.session!!.connectedAtMs)
        assertEquals(CallPhase.ACTIVE, s.session!!.phase)
    }

    @Test fun onlyTheCurrentCallIsLive() {
        assertFalse(s.isLive(CALL))
        s.onIncoming(info(), NOW + 60_000)
        assertTrue(s.isLive(CALL)); assertFalse(s.isLive(other))
        s.onEnded(CALL)
        assertFalse(s.isLive(CALL))                                      // a Telecom connection created after this must be refused
    }

    @Test fun anUnrelatedCallEndingNeverEndsTheRingingCall() {
        s.onIncoming(info(), NOW + 60_000)
        assertFalse(s.onEnded(other))                                    // NativeCallManager then leaves timers / notification alone
        assertEquals(CallPhase.RINGING, s.session!!.phase)
        assertEquals(CALL, s.session!!.info.callId)
    }

    @Test fun rejectingAfterAcceptanceIsRefusedAndKeepsOneSession() {
        s.onIncoming(info(), NOW + 60_000)
        assertTrue(s.onAccept(CALL)); assertFalse(s.onAccept(CALL))      // second tap: no second session
        assertEquals(CallPhase.ACCEPTING, s.session!!.phase)
        assertEquals(IncomingDecision.BUSY, s.onIncoming(info(other), NOW + 60_000))
    }

    @Test fun aCancelledCallCannotBeAcceptedAndANewOneCanRing() {
        s.onIncoming(info(), NOW + 60_000)
        s.onCancelled(CALL, "cancelled")
        assertFalse(s.onAccept(CALL))
        assertEquals(IncomingDecision.NEW, s.onIncoming(info(other), NOW + 60_000))
    }
}
