package com.goppo.chat

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class UrlPolicyTest {
    private val policy = UrlPolicy("https://arnabchatweb.pythonanywhere.com")

    private fun d(url: String?) = policy.classify(url)

    @Test fun sameOriginStaysInApp() {
        assertEquals(UrlDecision.INTERNAL, d("https://arnabchatweb.pythonanywhere.com/"))
        assertEquals(UrlDecision.INTERNAL, d("https://arnabchatweb.pythonanywhere.com/chat/5?x=1#a"))
        assertEquals(UrlDecision.INTERNAL, d("HTTPS://ArnabChatWeb.PythonAnywhere.com/login"))
        assertEquals(UrlDecision.INTERNAL, d("https://arnabchatweb.pythonanywhere.com:443/x"))
    }

    @Test fun otherHttpsSitesOpenInBrowser() {
        assertEquals(UrlDecision.EXTERNAL_BROWSER, d("https://example.com/page"))
        assertEquals(UrlDecision.EXTERNAL_BROWSER, d("http://example.com/page"))
        assertEquals(UrlDecision.EXTERNAL_BROWSER, d("https://other.pythonanywhere.com/"))
    }

    @Test fun lookalikeHostsAreNotTheApp() {
        assertEquals(UrlDecision.EXTERNAL_BROWSER, d("https://arnabchatweb.pythonanywhere.com.evil.example/"))
        assertEquals(UrlDecision.EXTERNAL_BROWSER, d("https://evil-arnabchatweb.pythonanywhere.com/"))
        assertEquals(UrlDecision.EXTERNAL_BROWSER, d("https://sub.arnabchatweb.pythonanywhere.com/"))
        // Same host on another port is neither the app nor "another site": never loaded, never handed out.
        assertEquals(UrlDecision.BLOCKED, d("https://arnabchatweb.pythonanywhere.com:8443/"))
    }

    @Test fun credentialsInUrlAreBlocked() {
        assertEquals(UrlDecision.BLOCKED, d("https://arnabchatweb.pythonanywhere.com@evil.example/"))
        assertEquals(UrlDecision.BLOCKED, d("https://user:pw@example.com/"))
    }

    @Test fun cleartextToTheAppHostIsBlocked() {
        assertEquals(UrlDecision.BLOCKED, d("http://arnabchatweb.pythonanywhere.com/"))
        assertFalse(policy.isAppOrigin("http://arnabchatweb.pythonanywhere.com/"))
    }

    @Test fun unsafeSchemesAreBlocked() {
        listOf(
            "javascript:alert(1)", "JaVaScRiPt:alert(1)", "file:///etc/passwd", "content://x/y", "intent://x#Intent;end",
            "market://details?id=a", "tel:123", "sms:123", "mailto:a@b.c", "ftp://example.com/x", "data:text/html,hi",
            "goppo://open", "chrome://crash", "view-source:https://example.com", "//example.com/x", "",
            "   ", "https://", "https:///x", "http://", "not a url",
        ).forEach { assertEquals("should block: $it", UrlDecision.BLOCKED, d(it)) }
        assertEquals(UrlDecision.BLOCKED, d(null))
    }

    @Test fun controlCharactersAndHugeUrlsAreBlocked() {
        assertEquals(UrlDecision.BLOCKED, d("https://example.com/a\nb"))
        assertEquals(UrlDecision.BLOCKED, d("https://example.com/" + "a".repeat(UrlPolicy.MAX_URL_LENGTH)))
    }

    @Test fun pagesCreatedByTheWebsite() {
        assertEquals(UrlDecision.INTERNAL, d("about:blank"))
        assertEquals(UrlDecision.BLOCKED, d("about:srcdoc"))
        assertEquals(UrlDecision.INTERNAL, d("blob:https://arnabchatweb.pythonanywhere.com/1b2c-3d"))
        assertEquals(UrlDecision.BLOCKED, d("blob:https://evil.example/1b2c-3d"))
    }

    @Test fun externalUrlIsOnlyReturnedForSafeLinks() {
        assertEquals("https://example.com/a", policy.externalUrlOrNull("https://example.com/a"))
        assertNull(policy.externalUrlOrNull("javascript:alert(1)"))
        assertNull(policy.externalUrlOrNull("https://arnabchatweb.pythonanywhere.com/x"))
        assertNull(policy.externalUrlOrNull(null))
    }

    @Test fun appOriginString() {
        assertEquals("https://arnabchatweb.pythonanywhere.com", policy.appOriginString)
        assertTrue(policy.isAppOrigin("https://arnabchatweb.pythonanywhere.com/"))
        assertFalse(policy.isAppOrigin(null))
    }

    @Test fun appUrlMustBeHttps() {
        var failed = false
        try { UrlPolicy("http://arnabchatweb.pythonanywhere.com") } catch (_: IllegalArgumentException) { failed = true }
        assertTrue(failed)
    }
}
