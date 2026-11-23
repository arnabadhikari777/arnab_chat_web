//! Goppo secure-media core (Rust).
//!
//! Live responsibilities (called by the Go signaling server through a C ABI, on every message):
//!   * call-session registry + signaling state machine (who may send what, in which order)
//!   * strict validation of SDP and ICE-candidate payloads (size, syntax, DTLS fingerprint required)
//!   * per-user token-bucket rate limiting
//!   * network-quality scoring (EWMA of RTT / jitter / loss -> 0..100 score, E-model style)
//!
//! Also provided and unit-tested, but NOT on the live P2P path (media never reaches the server):
//!   * `inspect_rtp` – RTP header metadata inspection (useful if a TURN/SFU inspection layer is added)
//!
//! Panics are disabled (`panic = "abort"`) and every FFI entry point validates its pointers.

use std::collections::HashMap;
use std::os::raw::c_char;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Mutex, OnceLock};
use std::time::Instant;

pub const MAX_PAYLOAD: usize = 16 * 1024;
pub const MAX_SDP_LINE: usize = 1024;
pub const MAX_CANDIDATE: usize = 512;

// ---- result codes (negative = rejected) -------------------------------------------------------
pub const OK: i32 = 0;
pub const E_NO_SESSION: i32 = -1;
pub const E_NOT_PARTICIPANT: i32 = -2;
pub const E_TYPE_FORBIDDEN: i32 = -3;
pub const E_TOO_LARGE: i32 = -4;
pub const E_BAD_SDP: i32 = -5;
pub const E_BAD_CANDIDATE: i32 = -6;
pub const E_STATE: i32 = -7;
pub const E_RATE: i32 = -8;
pub const E_ARGS: i32 = -9;
pub const E_DUPLICATE: i32 = -10;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Phase {
    New,
    Offered,
    Answered,
    Ended,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum MsgType {
    Offer,
    Answer,
    IceCandidate,
    Hangup,
    Reject,
    Busy,
    Ready,
    Quality,
}

impl MsgType {
    pub fn parse(s: &str) -> Option<MsgType> {
        Some(match s {
            "offer" => MsgType::Offer,
            "answer" => MsgType::Answer,
            "ice-candidate" => MsgType::IceCandidate,
            "hangup" => MsgType::Hangup,
            "reject" => MsgType::Reject,
            "busy" => MsgType::Busy,
            "ready" => MsgType::Ready,
            "quality" => MsgType::Quality,
            _ => return None,
        })
    }
}

// ---- SDP / ICE validation ---------------------------------------------------------------------
pub fn validate_sdp(sdp: &str) -> Result<(), i32> {
    if sdp.len() > MAX_PAYLOAD || sdp.len() < 20 {
        return Err(E_BAD_SDP);
    }
    if !sdp.starts_with("v=0") {
        return Err(E_BAD_SDP);
    }
    let mut media = 0usize;
    let mut fingerprint = false;
    for line in sdp.split("\r\n").flat_map(|l| l.split('\n')) {
        if line.is_empty() {
            continue;
        }
        if line.len() > MAX_SDP_LINE || line.bytes().any(|b| b == 0 || (b < 0x20 && b != b'\t')) {
            return Err(E_BAD_SDP);
        }
        let b = line.as_bytes();
        if b.len() < 2 || b[1] != b'=' || !b[0].is_ascii_lowercase() {
            return Err(E_BAD_SDP);
        }
        if line.starts_with("m=") {
            media += 1;
            // Only RTP over DTLS (SRTP) or SCTP over DTLS; refuse plain RTP/AVP.
            if !(line.contains("UDP/TLS/RTP/SAVPF") || line.contains("UDP/DTLS/SCTP") || line.contains("TCP/DTLS/SCTP")) {
                return Err(E_BAD_SDP);
            }
        }
        if line.starts_with("a=fingerprint:") {
            fingerprint = true;
        }
    }
    if media == 0 || media > 4 || !fingerprint {
        return Err(E_BAD_SDP);
    }
    Ok(())
}

/// `candidate:<foundation> <component> <udp|tcp> <priority> <addr> <port> typ <host|srflx|prflx|relay> ...`
/// An empty string is the end-of-candidates marker and is accepted.
pub fn validate_candidate(c: &str) -> Result<(), i32> {
    if c.is_empty() {
        return Ok(());
    }
    if c.len() > MAX_CANDIDATE || !c.is_ascii() || c.bytes().any(|b| b < 0x20) {
        return Err(E_BAD_CANDIDATE);
    }
    let c = c.strip_prefix("a=").unwrap_or(c);
    let rest = c.strip_prefix("candidate:").ok_or(E_BAD_CANDIDATE)?;
    let f: Vec<&str> = rest.split_whitespace().collect();
    if f.len() < 8 {
        return Err(E_BAD_CANDIDATE);
    }
    if f[0].is_empty() || f[0].len() > 32 || !f[0].bytes().all(|b| b.is_ascii_alphanumeric() || b == b'+' || b == b'/') {
        return Err(E_BAD_CANDIDATE);
    }
    if !matches!(f[1].parse::<u32>(), Ok(1..=2)) {
        return Err(E_BAD_CANDIDATE);
    }
    if !(f[2].eq_ignore_ascii_case("udp") || f[2].eq_ignore_ascii_case("tcp")) {
        return Err(E_BAD_CANDIDATE);
    }
    if f[3].parse::<u32>().is_err() {
        return Err(E_BAD_CANDIDATE);
    }
    let addr = f[4];
    if addr.is_empty() || addr.len() > 255 || !addr.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'.' || b == b':' || b == b'-') {
        return Err(E_BAD_CANDIDATE);
    }
    if f[5].parse::<u16>().is_err() {
        return Err(E_BAD_CANDIDATE);
    }
    if f[6] != "typ" || !matches!(f[7], "host" | "srflx" | "prflx" | "relay") {
        return Err(E_BAD_CANDIDATE);
    }
    Ok(())
}

// ---- network quality (E-model flavoured) -------------------------------------------------------
#[derive(Clone, Copy, Default, Debug)]
pub struct Quality {
    pub rtt_ms: f64,
    pub jitter_ms: f64,
    pub loss_pct: f64,
    pub samples: u32,
}

impl Quality {
    pub fn update(&mut self, rtt: f64, jitter: f64, loss_pct: f64) {
        let a = if self.samples == 0 { 1.0 } else { 0.3 };
        self.rtt_ms += a * (rtt - self.rtt_ms);
        self.jitter_ms += a * (jitter - self.jitter_ms);
        self.loss_pct += a * (loss_pct - self.loss_pct);
        self.samples = self.samples.saturating_add(1);
    }
    /// 0..=100. Simplified ITU-T G.107 R-factor: delay and loss impairments subtracted from 93.2.
    pub fn score(&self) -> i32 {
        let d = self.rtt_ms / 2.0 + self.jitter_ms * 2.0 + 10.0;
        let id = if d > 177.3 { 0.024 * d + 0.11 * (d - 177.3) } else { 0.024 * d };
        let ie = 30.0 * (1.0 + 15.0 * self.loss_pct / 100.0).ln();
        (93.2 - id - ie).clamp(0.0, 100.0).round() as i32
    }
}

// ---- RTP inspection -----------------------------------------------------------------------------
#[repr(C)]
#[derive(Clone, Copy, Default, Debug, PartialEq, Eq)]
pub struct RtpInfo {
    pub version: u8,
    pub marker: u8,
    pub payload_type: u8,
    pub csrc_count: u8,
    pub has_extension: u8,
    pub sequence: u16,
    pub timestamp: u32,
    pub ssrc: u32,
    pub header_len: u16,
    pub payload_len: u16,
}

pub fn inspect_rtp(p: &[u8]) -> Result<RtpInfo, i32> {
    if p.len() < 12 || p.len() > 1500 {
        return Err(E_ARGS);
    }
    let version = p[0] >> 6;
    if version != 2 {
        return Err(E_ARGS);
    }
    let padding = p[0] & 0x20 != 0;
    let ext = p[0] & 0x10 != 0;
    let cc = (p[0] & 0x0f) as usize;
    let mut hl = 12 + cc * 4;
    if p.len() < hl {
        return Err(E_ARGS);
    }
    if ext {
        if p.len() < hl + 4 {
            return Err(E_ARGS);
        }
        let words = u16::from_be_bytes([p[hl + 2], p[hl + 3]]) as usize;
        hl += 4 + words * 4;
        if p.len() < hl {
            return Err(E_ARGS);
        }
    }
    let mut end = p.len();
    if padding {
        let pad = p[p.len() - 1] as usize;
        if pad == 0 || hl + pad > p.len() {
            return Err(E_ARGS);
        }
        end -= pad;
    }
    Ok(RtpInfo {
        version,
        marker: (p[1] >> 7) & 1,
        payload_type: p[1] & 0x7f,
        csrc_count: cc as u8,
        has_extension: ext as u8,
        sequence: u16::from_be_bytes([p[2], p[3]]),
        timestamp: u32::from_be_bytes([p[4], p[5], p[6], p[7]]),
        ssrc: u32::from_be_bytes([p[8], p[9], p[10], p[11]]),
        header_len: hl as u16,
        payload_len: (end - hl) as u16,
    })
}

// ---- session registry ---------------------------------------------------------------------------
struct Bucket {
    tokens: f64,
    last: Instant,
}

struct Session {
    caller: i64,
    callee: i64,
    video: bool,
    phase: Phase,
    quality: HashMap<i64, Quality>,
}

struct Engine {
    sessions: HashMap<String, Session>,
    buckets: HashMap<i64, Bucket>,
}

static ENGINE: OnceLock<Mutex<Engine>> = OnceLock::new();
static C_CHECKED: AtomicU64 = AtomicU64::new(0);
static C_REJECTED: AtomicU64 = AtomicU64::new(0);
static C_QUALITY: AtomicU64 = AtomicU64::new(0);

fn engine() -> &'static Mutex<Engine> {
    ENGINE.get_or_init(|| Mutex::new(Engine { sessions: HashMap::new(), buckets: HashMap::new() }))
}

const RATE_BURST: f64 = 120.0;
const RATE_PER_SEC: f64 = 60.0;

fn take_token(e: &mut Engine, uid: i64) -> bool {
    let now = Instant::now();
    let b = e.buckets.entry(uid).or_insert(Bucket { tokens: RATE_BURST, last: now });
    let dt = now.duration_since(b.last).as_secs_f64();
    b.tokens = (b.tokens + dt * RATE_PER_SEC).min(RATE_BURST);
    b.last = now;
    if b.tokens >= 1.0 {
        b.tokens -= 1.0;
        true
    } else {
        false
    }
}

pub fn session_open(call_id: &str, caller: i64, callee: i64, video: bool) -> i32 {
    if call_id.is_empty() || call_id.len() > 64 || caller <= 0 || callee <= 0 || caller == callee {
        return E_ARGS;
    }
    let mut e = engine().lock().unwrap_or_else(|p| p.into_inner());
    if let Some(s) = e.sessions.get(call_id) {
        return if s.caller == caller && s.callee == callee { OK } else { E_DUPLICATE };
    }
    e.sessions.insert(call_id.to_string(), Session { caller, callee, video, phase: Phase::New, quality: HashMap::new() });
    OK
}

pub fn session_close(call_id: &str) -> i32 {
    let mut e = engine().lock().unwrap_or_else(|p| p.into_inner());
    match e.sessions.remove(call_id) {
        Some(_) => OK,
        None => E_NO_SESSION,
    }
}

pub fn session_count() -> i32 {
    engine().lock().unwrap_or_else(|p| p.into_inner()).sessions.len() as i32
}

/// Validate one signaling message. `payload` is the SDP text (offer/answer) or the candidate line.
pub fn check_signal(call_id: &str, from: i64, mtype: &str, payload: &str) -> i32 {
    C_CHECKED.fetch_add(1, Ordering::Relaxed);
    let r = check_signal_inner(call_id, from, mtype, payload);
    if r != OK {
        C_REJECTED.fetch_add(1, Ordering::Relaxed);
    }
    r
}

fn check_signal_inner(call_id: &str, from: i64, mtype: &str, payload: &str) -> i32 {
    let mt = match MsgType::parse(mtype) {
        Some(m) => m,
        None => return E_TYPE_FORBIDDEN,
    };
    if payload.len() > MAX_PAYLOAD {
        return E_TOO_LARGE;
    }
    let mut e = engine().lock().unwrap_or_else(|p| p.into_inner());
    if !take_token(&mut e, from) {
        return E_RATE;
    }
    let s = match e.sessions.get_mut(call_id) {
        Some(s) => s,
        None => return E_NO_SESSION,
    };
    let is_caller = from == s.caller;
    if !is_caller && from != s.callee {
        return E_NOT_PARTICIPANT;
    }
    if s.phase == Phase::Ended && mt != MsgType::Hangup {
        return E_STATE;
    }
    match mt {
        MsgType::Offer => {
            if !is_caller {
                return E_TYPE_FORBIDDEN;
            }
            if let Err(c) = validate_sdp(payload) {
                return c;
            }
            if !s.video && payload.contains("m=video") {
                return E_BAD_SDP; // voice call must not negotiate video
            }
            s.phase = Phase::Offered; // a re-offer (ICE restart) is allowed in any live phase
        }
        MsgType::Answer => {
            if is_caller {
                return E_TYPE_FORBIDDEN;
            }
            if s.phase != Phase::Offered {
                return E_STATE;
            }
            if let Err(c) = validate_sdp(payload) {
                return c;
            }
            s.phase = Phase::Answered;
        }
        MsgType::IceCandidate => {
            if let Err(c) = validate_candidate(payload) {
                return c;
            }
        }
        MsgType::Reject | MsgType::Busy => {
            if is_caller {
                return E_TYPE_FORBIDDEN;
            }
            if s.phase == Phase::Answered {
                return E_STATE;
            }
            s.phase = Phase::Ended;
        }
        MsgType::Hangup => {
            s.phase = Phase::Ended;
        }
        MsgType::Ready | MsgType::Quality => {}
    }
    OK
}

pub fn quality_update(call_id: &str, uid: i64, rtt: f64, jitter: f64, loss_permille: f64) -> i32 {
    if !(0.0..=60_000.0).contains(&rtt) || !(0.0..=60_000.0).contains(&jitter) || !(0.0..=1000.0).contains(&loss_permille) {
        return E_ARGS;
    }
    let mut e = engine().lock().unwrap_or_else(|p| p.into_inner());
    let s = match e.sessions.get_mut(call_id) {
        Some(s) => s,
        None => return E_NO_SESSION,
    };
    if uid != s.caller && uid != s.callee {
        return E_NOT_PARTICIPANT;
    }
    let q = s.quality.entry(uid).or_default();
    q.update(rtt, jitter, loss_permille / 10.0);
    C_QUALITY.fetch_add(1, Ordering::Relaxed);
    q.score()
}

// ---- C ABI ------------------------------------------------------------------------------------------
unsafe fn s<'a>(p: *const u8, n: usize) -> Option<&'a str> {
    if p.is_null() && n != 0 {
        return None;
    }
    if n == 0 {
        return Some("");
    }
    std::str::from_utf8(std::slice::from_raw_parts(p, n)).ok()
}

#[no_mangle]
pub extern "C" fn goppo_secure_init() -> i32 {
    let _ = engine();
    OK
}

/// # Safety: pointers must be valid for the given lengths.
#[no_mangle]
pub unsafe extern "C" fn goppo_secure_session_open(id: *const u8, id_len: usize, caller: i64, callee: i64, video: i32) -> i32 {
    match s(id, id_len) {
        Some(i) => session_open(i, caller, callee, video != 0),
        None => E_ARGS,
    }
}

/// # Safety: pointers must be valid for the given lengths.
#[no_mangle]
pub unsafe extern "C" fn goppo_secure_session_close(id: *const u8, id_len: usize) -> i32 {
    match s(id, id_len) {
        Some(i) => session_close(i),
        None => E_ARGS,
    }
}

/// # Safety: pointers must be valid for the given lengths.
#[no_mangle]
pub unsafe extern "C" fn goppo_secure_check_signal(
    id: *const u8, id_len: usize, from: i64, ty: *const u8, ty_len: usize, payload: *const u8, payload_len: usize,
) -> i32 {
    match (s(id, id_len), s(ty, ty_len), s(payload, payload_len)) {
        (Some(i), Some(t), Some(p)) => check_signal(i, from, t, p),
        _ => E_ARGS,
    }
}

/// # Safety: pointers must be valid for the given lengths.
#[no_mangle]
pub unsafe extern "C" fn goppo_secure_quality(id: *const u8, id_len: usize, uid: i64, rtt_ms: f64, jitter_ms: f64, loss_permille: f64) -> i32 {
    match s(id, id_len) {
        Some(i) => quality_update(i, uid, rtt_ms, jitter_ms, loss_permille),
        None => E_ARGS,
    }
}

/// # Safety: `data` must be valid for `len` bytes, `out` must point to a writable RtpInfo.
#[no_mangle]
pub unsafe extern "C" fn goppo_secure_rtp_inspect(data: *const u8, len: usize, out: *mut RtpInfo) -> i32 {
    if data.is_null() || out.is_null() {
        return E_ARGS;
    }
    match inspect_rtp(std::slice::from_raw_parts(data, len)) {
        Ok(i) => {
            *out = i;
            OK
        }
        Err(c) => c,
    }
}

#[repr(C)]
pub struct Counters {
    pub signals_checked: u64,
    pub signals_rejected: u64,
    pub quality_updates: u64,
    pub sessions_open: u64,
}

/// # Safety: `out` must point to a writable Counters.
#[no_mangle]
pub unsafe extern "C" fn goppo_secure_counters(out: *mut Counters) -> i32 {
    if out.is_null() {
        return E_ARGS;
    }
    *out = Counters {
        signals_checked: C_CHECKED.load(Ordering::Relaxed),
        signals_rejected: C_REJECTED.load(Ordering::Relaxed),
        quality_updates: C_QUALITY.load(Ordering::Relaxed),
        sessions_open: session_count() as u64,
    };
    OK
}

#[no_mangle]
pub extern "C" fn goppo_secure_version() -> *const c_char {
    b"goppo-secure-media/2.0.0 (rust)\0".as_ptr() as *const c_char
}

#[cfg(test)]
mod tests {
    use super::*;

    const SDP: &str = "v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\na=fingerprint:sha-256 AA:BB\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=rtpmap:111 opus/48000/2\r\n";
    const SDP_VIDEO: &str = "v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\na=fingerprint:sha-256 AA:BB\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\n";
    const CAND: &str = "candidate:842163049 1 udp 1677729535 203.0.113.4 54321 typ srflx raddr 10.0.0.2 rport 54321";

    #[test]
    fn sdp_rules() {
        assert_eq!(validate_sdp(SDP), Ok(()));
        assert_eq!(validate_sdp("hello world, not sdp at all"), Err(E_BAD_SDP));
        assert_eq!(validate_sdp(&SDP.replace("a=fingerprint:sha-256 AA:BB\r\n", "")), Err(E_BAD_SDP)); // DTLS required
        assert_eq!(validate_sdp(&SDP.replace("UDP/TLS/RTP/SAVPF", "RTP/AVP")), Err(E_BAD_SDP)); // no plain RTP
        assert_eq!(validate_sdp(&format!("{}x\0y\r\n", SDP)), Err(E_BAD_SDP));
        assert_eq!(validate_sdp(&format!("{}a={}\r\n", SDP, "x".repeat(2000))), Err(E_BAD_SDP));
    }

    #[test]
    fn candidate_rules() {
        assert_eq!(validate_candidate(CAND), Ok(()));
        assert_eq!(validate_candidate(""), Ok(()));
        assert_eq!(validate_candidate("candidate:1 1 udp 1 a.local 9 typ host"), Ok(()));
        assert_eq!(validate_candidate("candidate:1 1 sctp 1 1.2.3.4 9 typ host"), Err(E_BAD_CANDIDATE));
        assert_eq!(validate_candidate("candidate:1 1 udp 1 1.2.3.4 70000 typ host"), Err(E_BAD_CANDIDATE));
        assert_eq!(validate_candidate("candidate:1 1 udp 1 1.2.3.4 9 typ evil"), Err(E_BAD_CANDIDATE));
        assert_eq!(validate_candidate("<script>"), Err(E_BAD_CANDIDATE));
    }

    #[test]
    fn state_machine_and_roles() {
        let id = "t-state-1";
        assert_eq!(session_open(id, 1, 2, false), OK);
        assert_eq!(session_open(id, 1, 2, false), OK); // idempotent
        assert_eq!(session_open(id, 1, 3, false), E_DUPLICATE);
        assert_eq!(check_signal(id, 9, "offer", SDP), E_NOT_PARTICIPANT);
        assert_eq!(check_signal(id, 2, "offer", SDP), E_TYPE_FORBIDDEN); // callee cannot offer
        assert_eq!(check_signal(id, 2, "answer", SDP), E_STATE); // answer before offer
        assert_eq!(check_signal(id, 1, "offer", SDP_VIDEO), E_BAD_SDP); // voice call with video m-line
        assert_eq!(check_signal(id, 1, "offer", SDP), OK);
        assert_eq!(check_signal(id, 1, "answer", SDP), E_TYPE_FORBIDDEN);
        assert_eq!(check_signal(id, 2, "answer", SDP), OK);
        assert_eq!(check_signal(id, 2, "reject", ""), E_STATE); // cannot reject an answered call
        assert_eq!(check_signal(id, 1, "ice-candidate", CAND), OK);
        assert_eq!(check_signal(id, 1, "ice-candidate", "junk"), E_BAD_CANDIDATE);
        assert_eq!(check_signal(id, 1, "bogus", ""), E_TYPE_FORBIDDEN);
        assert_eq!(check_signal(id, 1, "hangup", ""), OK);
        assert_eq!(check_signal(id, 1, "offer", SDP), E_STATE); // ended
        assert_eq!(session_close(id), OK);
        assert_eq!(check_signal(id, 1, "ready", ""), E_NO_SESSION);
    }

    #[test]
    fn oversize_and_rate_limit() {
        let id = "t-rate-1";
        assert_eq!(session_open(id, 11, 12, true), OK);
        assert_eq!(check_signal(id, 11, "ice-candidate", &"a".repeat(MAX_PAYLOAD + 1)), E_TOO_LARGE);
        let mut limited = false;
        for _ in 0..500 {
            if check_signal(id, 12, "ready", "") == E_RATE {
                limited = true;
                break;
            }
        }
        assert!(limited, "token bucket must eventually refuse a flood");
        session_close(id);
    }

    #[test]
    fn quality_score_orders_networks() {
        let id = "t-q-1";
        assert_eq!(session_open(id, 21, 22, true), OK);
        let good = quality_update(id, 21, 30.0, 5.0, 0.0);
        let bad = quality_update(id, 22, 600.0, 80.0, 150.0);
        assert!(good > 80, "good={good}");
        assert!(bad < 40, "bad={bad}");
        assert_eq!(quality_update(id, 99, 1.0, 1.0, 1.0), E_NOT_PARTICIPANT);
        assert_eq!(quality_update(id, 21, -1.0, 1.0, 1.0), E_ARGS);
        session_close(id);
    }

    #[test]
    fn rtp_header() {
        let mut p = vec![0x80, 0xE0 | 0x01, 0x12, 0x34, 0, 0, 0, 100, 0xDE, 0xAD, 0xBE, 0xEF];
        p.extend_from_slice(&[1, 2, 3, 4, 5]);
        let i = inspect_rtp(&p).unwrap();
        assert_eq!((i.version, i.marker, i.payload_type, i.sequence, i.timestamp, i.ssrc), (2, 1, 0x61, 0x1234, 100, 0xDEADBEEF));
        assert_eq!((i.header_len, i.payload_len), (12, 5));
        assert!(inspect_rtp(&p[..8]).is_err());
        let mut v1 = p.clone();
        v1[0] = 0x40;
        assert!(inspect_rtp(&v1).is_err());
        let mut cc = p.clone();
        cc[0] = 0x8F; // claims 15 CSRCs
        assert!(inspect_rtp(&cc).is_err());
    }

    #[test]
    fn ffi_rejects_null_and_bad_utf8() {
        unsafe {
            assert_eq!(goppo_secure_session_open(std::ptr::null(), 5, 1, 2, 0), E_ARGS);
            let bad = [0xffu8, 0xfe];
            assert_eq!(goppo_secure_session_close(bad.as_ptr(), 2), E_ARGS);
            let mut c = Counters { signals_checked: 0, signals_rejected: 0, quality_updates: 0, sessions_open: 0 };
            assert_eq!(goppo_secure_counters(&mut c), OK);
            assert_eq!(goppo_secure_rtp_inspect(std::ptr::null(), 0, std::ptr::null_mut()), E_ARGS);
        }
    }
}
