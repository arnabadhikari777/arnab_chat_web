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

    // ---------------------------------------------------------------- Task 2: bundled UI
    @Test fun localOriginAndBackendOriginAreBothFirstParty() {
        assertEquals(UrlDecision.INTERNAL, d("https://localhost/messages.html"))
        assertTrue(policy.isLocalOrigin("https://localhost/chat.html?c=1")); assertFalse(policy.isBackendOrigin("https://localhost/"))
        assertTrue(policy.isBackendOrigin("https://arnabchatweb.pythonanywhere.com/people")); assertFalse(policy.isLocalOrigin("https://arnabchatweb.pythonanywhere.com/"))
        assertEquals("https://localhost", policy.localOriginString)
    }

    @Test fun lookAlikeLocalOriginsAreNotTrusted() {
        assertEquals(UrlDecision.EXTERNAL_BROWSER, d("https://localhost.evil.com/"))
        assertEquals(UrlDecision.BLOCKED, d("http://localhost/"))                 // cleartext local origin is not the bundled one
        assertFalse(policy.isAppOrigin("https://localhost:8443/"))
        assertFalse(policy.isAppOrigin("https://evil.com/https://localhost/"))
    }

    @Test fun hostedWebsitePathsWithABundledTwinOpenLocally() {
        assertEquals("messages.html", LocalRoutes.toLocal("/messages"))
        assertEquals("messages.html", LocalRoutes.toLocal("/"))
        assertEquals("chat.html?c=12", LocalRoutes.toLocal("/chat/12"))
        assertEquals("chat.html?c=12", LocalRoutes.toLocal("/chat/12/?x=1"))
        assertEquals("settings.html", LocalRoutes.toLocal("/settings/"))
        assertEquals("login.html", LocalRoutes.toLocal("/login"))
        assertNull(LocalRoutes.toLocal("/settings/?hosted=1"))           // the website's own settings page (no redirect loop)
        assertNull(LocalRoutes.toLocal("/people")); assertNull(LocalRoutes.toLocal("/chat/abc")); assertNull(LocalRoutes.toLocal("/admin/users")); assertNull(LocalRoutes.toLocal(null))
    }

    @Test fun bundledPagesMapBackToSitePaths() {
        assertEquals("/chat/12", LocalRoutes.toSite("/chat.html", "c=12")); assertEquals("/messages", LocalRoutes.toSite("/messages.html", null))
        assertNull(LocalRoutes.toSite("/chat.html", "c=x")); assertNull(LocalRoutes.toSite("/other.html", null))
    }

    @Test fun rangeHeaderParsing() {
        assertEquals(0L to 99L, RangeHeader.parse("bytes=0-99", 1000)); assertEquals(500L to 999L, RangeHeader.parse("bytes=500-", 1000))
        assertEquals(900L to 999L, RangeHeader.parse("bytes=-100", 1000)); assertEquals(0L to 999L, RangeHeader.parse("bytes=0-5000", 1000))
        assertNull(RangeHeader.parse(null, 1000)); assertNull(RangeHeader.parse("bytes=2000-3000", 1000)); assertNull(RangeHeader.parse("bytes=5-1", 1000))
        assertNull(RangeHeader.parse("bytes=0-1,5-9", 1000)); assertNull(RangeHeader.parse("items=0-1", 1000))
    }
}
