# Known limitations and remaining manual checks

1. **No server-side media processing.** Calls are P2P and end-to-end encrypted; C/C++/Rust work on signaling and telemetry (`ARCHITECTURE.md`). An SFU would be needed for anything else and does not fit Render.
2. **TURN is required for hard networks and cannot run on Render.** Without it, some mobile-carrier and symmetric-NAT pairs will not connect. Not tested here; test on your users' real networks.
3. **Render Free sleeps** (≈30–60 s cold start; the UI handles it with a bounded wait). A deploy/restart during a call drops that call (clients retry for ~20 s).
4. **Incoming calls are web-notification based**, not a native phone ring: on Android Chrome the push shows a persistent notification; if the browser is fully killed by the OS or the user disabled notifications, the call may only appear when the site is opened (it then shows immediately, up to 60 s after the call started). iOS needs the site added to the Home Screen (Web Push). A closed-tab ring-tone is not possible on the web.
5. **In-page incoming calls use polling** (3 s visible / 10 s hidden tabs) because PythonAnywhere has no WebSockets. Worst-case ring delay ≈ 3 s in a visible tab. Browsers may throttle background tabs further.
6. **One server instance.** Call sessions live in memory; if you scale Render to several instances, both parties of a call may land on different instances. Keep one instance (the default).
7. **Tokens need roughly synchronised clocks** (30 s tolerance on `iat`) between PythonAnywhere and Render.
8. **Call history UI**: records, API (`/api/calling/history`) and missed-call notices exist; there is no dedicated "calls" page in the existing UI beyond the notice box and admin statistics.
9. **Pre-existing failing tests** from the supplied ZIP (49) remain; see `TESTING_REPORT.md`.
10. `inspect_rtp` (Rust) is tested but not on the live path (P2P).
11. Camera flip uses `facingMode: exact`; devices with one camera show "no second camera".
12. Quality advice pauses outgoing **video** on a bad network (to keep audio); it does not renegotiate bitrate/codecs.

## Remaining manual tests (do these before announcing)
See `DEPLOYMENT_CHECKLIST.md` → "Manual acceptance tests".
