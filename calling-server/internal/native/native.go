// Package native is the single cgo bridge to the three native components:
//
//	C    libgoppo_audio.a          (Opus codec, PCM utilities, ring buffers)
//	C++  libgoppo_media.a          (telemetry quality engine, frame self-test)
//	Rust libgoppo_secure_media.a   (signaling validation, session state machine, quality score)
//
// There is deliberately NO pure-Go fallback: if the libraries are not linked the program does not build,
// and if they do not pass their self-tests Init returns an error.
package native

/*
#cgo CFLAGS: -I${SRCDIR}/../../../audio-c/include -I${SRCDIR}/../../../media-engine/include
#cgo LDFLAGS: -L${SRCDIR}/../../../native/lib -lgoppo_media -lgoppo_audio -lgoppo_secure_media -lopus -lstdc++ -lm -ldl -lpthread -lgcc_s -lutil -lrt
#include <stdint.h>
#include <stdlib.h>
#include "goppo_audio.h"
#include "goppo_media.h"

typedef struct { uint64_t checked, rejected, quality, sessions; } GoppoSecureCounters;

extern int  goppo_secure_init(void);
extern int  goppo_secure_session_open(const uint8_t*, size_t, int64_t, int64_t, int);
extern int  goppo_secure_session_close(const uint8_t*, size_t);
extern int  goppo_secure_check_signal(const uint8_t*, size_t, int64_t, const uint8_t*, size_t, const uint8_t*, size_t);
extern int  goppo_secure_quality(const uint8_t*, size_t, int64_t, double, double, double);
extern int  goppo_secure_counters(GoppoSecureCounters*);
extern const char* goppo_secure_version(void);
*/
import "C"

import (
	"errors"
	"fmt"
	"sync"
	"unsafe"
)

// Result codes returned by the Rust validator (mirror secure-media-rs/src/lib.rs).
const (
	OK               = 0
	ErrNoSession     = -1
	ErrNotPart       = -2
	ErrTypeForbidden = -3
	ErrTooLarge      = -4
	ErrBadSDP        = -5
	ErrBadCandidate  = -6
	ErrState         = -7
	ErrRate          = -8
	ErrArgs          = -9
	ErrDuplicate     = -10
)

func CodeName(c int) string {
	switch c {
	case OK:
		return "ok"
	case ErrNoSession:
		return "no_session"
	case ErrNotPart:
		return "not_participant"
	case ErrTypeForbidden:
		return "type_forbidden"
	case ErrTooLarge:
		return "too_large"
	case ErrBadSDP:
		return "invalid_sdp"
	case ErrBadCandidate:
		return "invalid_candidate"
	case ErrState:
		return "invalid_state"
	case ErrRate:
		return "rate_limited"
	case ErrDuplicate:
		return "duplicate"
	default:
		return "invalid_request"
	}
}

type Component struct {
	Name     string         `json:"name"`
	Language string         `json:"language"`
	Version  string         `json:"version"`
	Loaded   bool           `json:"loaded"`
	Verified bool           `json:"verified"`
	Role     string         `json:"role"`
	Detail   map[string]any `json:"detail,omitempty"`
}

var (
	mu         sync.RWMutex
	components []Component
	initErr    error
)

func ptr(s string) *C.uint8_t {
	if s == "" {
		return nil
	}
	return (*C.uint8_t)(unsafe.Pointer(unsafe.StringData(s)))
}

// Init links, initialises and self-tests all three components. Any failure is returned.
func Init() error {
	mu.Lock()
	defer mu.Unlock()
	components = nil
	var errs []error

	// Rust: init + a full open/validate/close cycle through the real FFI.
	rust := Component{Name: "secure-media", Language: "rust", Loaded: true,
		Version: C.GoString(C.goppo_secure_version()), Role: "signaling validation, session state machine, quality score"}
	if C.goppo_secure_init() == 0 {
		id := "selftest-rust"
		ok := C.goppo_secure_session_open(ptr(id), C.size_t(len(id)), 1, 2, 0) == 0
		ty := "ready"
		ok = ok && C.goppo_secure_check_signal(ptr(id), C.size_t(len(id)), 1, ptr(ty), C.size_t(len(ty)), nil, 0) == 0
		bad := "offer"
		ok = ok && C.goppo_secure_check_signal(ptr(id), C.size_t(len(id)), 2, ptr(bad), C.size_t(len(bad)), nil, 0) == C.int(ErrTypeForbidden)
		C.goppo_secure_session_close(ptr(id), C.size_t(len(id)))
		rust.Verified = ok
	}
	if !rust.Verified {
		errs = append(errs, errors.New("rust secure-media self-test failed"))
	}
	components = append(components, rust)

	// C: real Opus round trip.
	var bytesPer, ratio C.int
	rc := C.goppo_audio_selftest(&bytesPer, &ratio)
	cc := Component{Name: "audio", Language: "c", Loaded: true, Version: C.GoString(C.goppo_audio_version()),
		Verified: rc == 0, Role: "Opus codec, PCM processing, ring buffers",
		Detail: map[string]any{"opus_bytes_per_20ms_frame": int(bytesPer), "rms_ratio": float64(ratio) / 1000}}
	if !cc.Verified {
		errs = append(errs, fmt.Errorf("c audio self-test failed (%d)", int(rc)))
	}
	components = append(components, cc)

	// C++: frame engine self-test + quality engine.
	var frames, peak C.int
	rc = C.goppo_media_selftest(&frames, &peak)
	cpp := Component{Name: "media-engine", Language: "c++", Loaded: true, Version: C.GoString(C.goppo_media_version()),
		Verified: rc == 0, Role: "telemetry quality engine, A/V sync, jitter-buffer advice",
		Detail: map[string]any{"selftest_frames": int(frames), "selftest_peak": float64(peak) / 1000}}
	if !cpp.Verified {
		errs = append(errs, fmt.Errorf("c++ media self-test failed (%d)", int(rc)))
	}
	components = append(components, cpp)

	initErr = errors.Join(errs...)
	return initErr
}

// Components returns the component list with live counters (so operators can see the code is really executing).
func Components() []Component {
	mu.RLock()
	defer mu.RUnlock()
	out := make([]Component, len(components))
	for i, c := range components {
		d := map[string]any{}
		for k, v := range c.Detail {
			d[k] = v
		}
		switch c.Language {
		case "rust":
			var k C.GoppoSecureCounters
			C.goppo_secure_counters(&k)
			d["signals_checked"], d["signals_rejected"] = uint64(k.checked), uint64(k.rejected)
			d["quality_updates"], d["sessions_open"] = uint64(k.quality), uint64(k.sessions)
		case "c++":
			d["telemetry_samples_processed"] = uint64(C.goppo_media_samples_processed())
		}
		c.Detail = d
		out[i] = c
	}
	return out
}

func AllVerified() bool {
	mu.RLock()
	defer mu.RUnlock()
	if len(components) != 3 || initErr != nil {
		return false
	}
	for _, c := range components {
		if !c.Loaded || !c.Verified {
			return false
		}
	}
	return true
}

// ---- Rust session API ----

func SessionOpen(callID string, caller, callee int64, video bool) int {
	v := C.int(0)
	if video {
		v = 1
	}
	return int(C.goppo_secure_session_open(ptr(callID), C.size_t(len(callID)), C.int64_t(caller), C.int64_t(callee), v))
}

func SessionClose(callID string) { C.goppo_secure_session_close(ptr(callID), C.size_t(len(callID))) }

// CheckSignal validates one signaling message; payload is the SDP text or the candidate line.
func CheckSignal(callID string, from int64, msgType, payload string) int {
	return int(C.goppo_secure_check_signal(ptr(callID), C.size_t(len(callID)), C.int64_t(from),
		ptr(msgType), C.size_t(len(msgType)), ptr(payload), C.size_t(len(payload))))
}

// QualityScore updates the Rust EWMA for this user and returns 0..100 (negative = error code).
func QualityScore(callID string, uid int64, rttMs, jitterMs, lossPermille float64) int {
	return int(C.goppo_secure_quality(ptr(callID), C.size_t(len(callID)), C.int64_t(uid),
		C.double(rttMs), C.double(jitterMs), C.double(lossPermille)))
}

// ---- C++ quality engine ----

type Sample struct {
	RTTMs, JitterMs, LossPermille, BitrateKbps, AudioPlayoutMs, VideoPlayoutMs, JBDelayMs, AudioPeak float64
}

type Report struct {
	Samples           int     `json:"samples"`
	Level             int     `json:"level"`
	AdviseReduceVideo bool    `json:"advise_reduce_video"`
	AdviseAudioOnly   bool    `json:"advise_audio_only"`
	SilentMic         bool    `json:"silent_mic"`
	RTTP50Ms          float64 `json:"rtt_p50_ms"`
	RTTP95Ms          float64 `json:"rtt_p95_ms"`
	JitterP95Ms       float64 `json:"jitter_p95_ms"`
	LossMeanPermille  float64 `json:"loss_mean_permille"`
	BitrateMeanKbps   float64 `json:"bitrate_mean_kbps"`
	AVSkewMs          float64 `json:"av_skew_ms"`
	JBTargetMs        float64 `json:"jb_target_ms"`
	MicPeakDBFS       float64 `json:"mic_peak_dbfs"`
}

type Quality struct{ q *C.GoppoQ }

func NewQuality() (*Quality, error) {
	q := C.goppo_q_new()
	if q == nil {
		return nil, errors.New("goppo_q_new failed")
	}
	return &Quality{q: q}, nil
}

func (q *Quality) Free() {
	if q != nil && q.q != nil {
		C.goppo_q_free(q.q)
		q.q = nil
	}
}

func (q *Quality) Add(s Sample) error {
	if q == nil || q.q == nil {
		return errors.New("closed")
	}
	cs := C.GoppoQSample{rtt_ms: C.double(s.RTTMs), jitter_ms: C.double(s.JitterMs), loss_permille: C.double(s.LossPermille),
		bitrate_kbps: C.double(s.BitrateKbps), audio_playout_ms: C.double(s.AudioPlayoutMs), video_playout_ms: C.double(s.VideoPlayoutMs),
		jb_delay_ms: C.double(s.JBDelayMs), audio_peak: C.double(s.AudioPeak)}
	if rc := C.goppo_q_add(q.q, &cs); rc != 0 {
		return fmt.Errorf("invalid telemetry sample (%d)", int(rc))
	}
	return nil
}

func (q *Quality) Report() (Report, error) {
	var r C.GoppoQReport
	if q == nil || q.q == nil || C.goppo_q_report(q.q, &r) != 0 {
		return Report{}, errors.New("report failed")
	}
	return Report{Samples: int(r.samples), Level: int(r.level), AdviseReduceVideo: r.advise_reduce_video != 0,
		AdviseAudioOnly: r.advise_audio_only != 0, SilentMic: r.silent_mic != 0, RTTP50Ms: float64(r.rtt_p50_ms),
		RTTP95Ms: float64(r.rtt_p95_ms), JitterP95Ms: float64(r.jitter_p95_ms), LossMeanPermille: float64(r.loss_mean_permille),
		BitrateMeanKbps: float64(r.bitrate_mean_kbps), AVSkewMs: float64(r.av_skew_ms), JBTargetMs: float64(r.jb_target_ms),
		MicPeakDBFS: float64(r.mic_peak_dbfs)}, nil
}
