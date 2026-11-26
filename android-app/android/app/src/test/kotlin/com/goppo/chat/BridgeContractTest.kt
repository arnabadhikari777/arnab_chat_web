package com.goppo.chat

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class BridgeContractTest {

    private fun caps(fb: Boolean = false, calls: Boolean = false, active: Boolean = false, msgs: Boolean = false) =
        Capabilities.compute(Capabilities.Inputs(fb, calls, active, msgs))

    @Test fun capabilitiesAreOnlyReportedWhenTheyCanReallyWork() {
        assertEquals(listOf("nativePermissions", "appLifecycle", "openExternal", "audioRouting"), caps())
        assertFalse("nativePush" in caps(calls = true, active = true, msgs = true))                       // no Firebase: no push
        assertTrue("nativePush" in caps(fb = true))
        assertFalse("nativeIncomingCall" in caps(fb = true))                                              // notifications off: the website rings itself
        assertTrue("nativeIncomingCall" in caps(calls = true))                                            // works from the page's own call event too
        assertFalse("nativeMessageNotifications" in caps(msgs = true))                                    // needs push as well
        assertTrue("nativeMessageNotifications" in caps(fb = true, msgs = true))
        assertTrue("nativeCallNotifications" in caps(active = true))
        assertEquals(8, caps(true, true, true, true).size)
    }

    @Test fun notifyAcceptsOnlyKnownEvents() {
        assertEquals("call:connected", WebEvents.validate("call:connected"))
        assertEquals("app:ready", WebEvents.validate("app:ready"))
        assertNull(WebEvents.validate("call:accept"))
        assertNull(WebEvents.validate("app:back"))
        assertNull(WebEvents.validate("<script>"))
        assertNull(WebEvents.validate(""))
        assertNull(WebEvents.validate(null))
        assertNull(WebEvents.validate(42))
    }

    @Test fun booleanArgumentsAreStrict() {
        assertEquals(true, Args.optionalBoolean(true, "audio"))
        assertEquals(false, Args.optionalBoolean(false, "audio"))
        assertNull(Args.optionalBoolean(null, "audio"))
        listOf<Any>("true", 1, 0, "yes", listOf(true)).forEach {
            var failed = false
            try { Args.optionalBoolean(it, "audio") } catch (_: IllegalArgumentException) { failed = true }
            assertTrue("should reject $it", failed)
        }
    }

    @Test fun callStateFollowsEventsAndResetsOnNewPage() {
        val s = NativeState()
        assertFalse(s.callActive); assertFalse(s.webReady)
        assertTrue(s.onWebEvent(WebEvents.READY)); assertTrue(s.webReady)
        assertTrue(s.onWebEvent(WebEvents.CALL_INCOMING)); assertFalse(s.callActive)
        assertTrue(s.onWebEvent(WebEvents.CALL_CONNECTED)); assertTrue(s.callActive)
        assertTrue(s.onWebEvent(WebEvents.CALL_ENDED)); assertFalse(s.callActive)
        s.onWebEvent(WebEvents.CALL_CONNECTED)
        s.onPageStarted()
        assertFalse(s.callActive); assertFalse(s.webReady)
        assertFalse(s.onWebEvent("nonsense"))
    }

    @Test fun lifecycleIsDeliveredOncePerTransitionAndOnlyWhenReady() {
        val g = LifecycleGate()
        assertNull(g.onActivityPaused())               // page not ready yet: nothing delivered
        assertEquals("app:pause", g.onWebReady())      // page loads while app is in the background: told once
        assertNull(g.onWebReady())                     // repeated ready: nothing
        assertNull(g.onActivityPaused())               // duplicate pause: nothing
        assertEquals("app:resume", g.onActivityResumed())
        assertNull(g.onActivityResumed())              // duplicate resume: nothing
        assertEquals("app:pause", g.onActivityPaused())
        assertEquals("app:resume", g.onActivityResumed())
    }

    @Test fun firstResumeAfterLaunchIsNotDelivered() {
        val g = LifecycleGate()
        assertNull(g.onActivityResumed())
        assertNull(g.onWebReady())
    }

    @Test fun pageReloadResetsLifecycleDelivery() {
        val g = LifecycleGate()
        g.onWebReady()
        assertEquals("app:pause", g.onActivityPaused())
        g.onPageStarted()
        assertNull(g.onActivityPaused())               // not ready: wait
        assertEquals("app:pause", g.onWebReady())      // new page learns the state once
    }

    @Test fun backButtonDecisions() {
        assertEquals(BackAction.NONE, BackPolicy.decide(true, false, false))
        assertEquals(BackAction.NONE, BackPolicy.decide(true, true, true))
        assertEquals(BackAction.EXIT, BackPolicy.decide(false, true, false))        // site is at its root: do not walk native history
        assertEquals(BackAction.BACKGROUND, BackPolicy.decide(false, false, true))  // never close the app during a call
        assertEquals(BackAction.WEBVIEW_BACK, BackPolicy.decide(null, true, false))
        assertEquals(BackAction.EXIT, BackPolicy.decide(null, false, false))
        assertEquals(BackAction.BACKGROUND, BackPolicy.decide(null, false, true))
    }

    @Test fun mediaPermissionsAreMappedNarrowly() {
        val audio = MediaPolicy.RES_AUDIO; val video = MediaPolicy.RES_VIDEO
        assertEquals(listOf(MediaPolicy.PERM_AUDIO, MediaPolicy.PERM_VIDEO), MediaPolicy.requiredPermissions(arrayOf(audio, video)))
        assertEquals(listOf(MediaPolicy.PERM_AUDIO), MediaPolicy.requiredPermissions(arrayOf(audio)))
        assertTrue(MediaPolicy.requiredPermissions(arrayOf("android.webkit.resource.MIDI_SYSEX")).isEmpty())
        assertTrue(MediaPolicy.requiredPermissions(arrayOf("android.webkit.resource.PROTECTED_MEDIA_ID")).isEmpty())
        // Only what is granted and supported is handed back; unknown resources are never granted.
        val all = arrayOf(audio, video, "android.webkit.resource.MIDI_SYSEX")
        assertEquals(listOf(audio, video), MediaPolicy.grantableResources(all, setOf(MediaPolicy.PERM_AUDIO, MediaPolicy.PERM_VIDEO)).toList())
        assertEquals(listOf(audio), MediaPolicy.grantableResources(all, setOf(MediaPolicy.PERM_AUDIO)).toList())
        assertTrue(MediaPolicy.grantableResources(all, emptySet()).isEmpty())
        assertEquals(listOf(MediaPolicy.PERM_VIDEO), MediaPolicy.permissionsFor(audio = false, video = true))
        assertTrue(MediaPolicy.permissionsFor(false, false).isEmpty())
    }
}
