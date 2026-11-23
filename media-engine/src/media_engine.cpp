#include "goppo_media.h"

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <memory>
#include <mutex>
#include <vector>

extern "C" {
#include "goppo_audio.h"
}

namespace {

std::atomic<uint64_t> g_samples{0};

struct Ring {
    GoppoRing *r;
    explicit Ring(size_t n) : r(goppo_ring_new(n)) {}
    ~Ring() { goppo_ring_free(r); }
    Ring(const Ring &) = delete;
    Ring &operator=(const Ring &) = delete;
};

constexpr size_t kWindow = 30;  // ~30 telemetry samples (one every 2 s => last minute)

bool finite_in(double v, double lo, double hi) { return std::isfinite(v) && v >= lo && v <= hi; }

}  // namespace

struct GoppoQ {
    std::mutex mu;
    Ring rtt{kWindow}, jitter{kWindow}, loss{kWindow}, bitrate{kWindow}, skew{kWindow}, jb{kWindow}, mic{kWindow};
    int32_t samples = 0;
    bool ok() const { return rtt.r && jitter.r && loss.r && bitrate.r && skew.r && jb.r && mic.r; }
};

extern "C" {

GoppoQ *goppo_q_new(void) {
    auto q = std::make_unique<GoppoQ>();
    return q->ok() ? q.release() : nullptr;
}

void goppo_q_free(GoppoQ *q) { delete q; }

int goppo_q_add(GoppoQ *q, const GoppoQSample *s) {
    if (!q || !s) return -1;
    if (!finite_in(s->rtt_ms, 0, 60000) || !finite_in(s->jitter_ms, 0, 60000) || !finite_in(s->loss_permille, 0, 1000) ||
        !finite_in(s->bitrate_kbps, 0, 100000) || !finite_in(s->jb_delay_ms, 0, 60000) ||
        !finite_in(s->audio_playout_ms, 0, 1e15) || !finite_in(s->video_playout_ms, 0, 1e15) ||
        !(std::isfinite(s->audio_peak) && s->audio_peak <= 1.0))
        return -2;
    std::lock_guard<std::mutex> lock(q->mu);
    goppo_ring_push(q->rtt.r, s->rtt_ms);
    goppo_ring_push(q->jitter.r, s->jitter_ms);
    goppo_ring_push(q->loss.r, s->loss_permille);
    goppo_ring_push(q->bitrate.r, s->bitrate_kbps);
    goppo_ring_push(q->jb.r, s->jb_delay_ms);
    if (s->audio_playout_ms > 0 && s->video_playout_ms > 0) goppo_ring_push(q->skew.r, s->audio_playout_ms - s->video_playout_ms);
    if (s->audio_peak >= 0) goppo_ring_push(q->mic.r, (double)goppo_level_to_dbfs((float)s->audio_peak));
    q->samples++;
    g_samples.fetch_add(1, std::memory_order_relaxed);
    return 0;
}

int goppo_q_report(GoppoQ *q, GoppoQReport *o) {
    if (!q || !o) return -1;
    std::lock_guard<std::mutex> lock(q->mu);
    *o = GoppoQReport{};
    o->samples = q->samples;
    o->rtt_p50_ms = goppo_ring_percentile(q->rtt.r, 0.5);
    o->rtt_p95_ms = goppo_ring_percentile(q->rtt.r, 0.95);
    o->jitter_p95_ms = goppo_ring_percentile(q->jitter.r, 0.95);
    o->loss_mean_permille = goppo_ring_mean(q->loss.r);
    o->bitrate_mean_kbps = goppo_ring_mean(q->bitrate.r);
    o->av_skew_ms = goppo_ring_mean(q->skew.r);
    o->mic_peak_dbfs = goppo_ring_len(q->mic.r) ? goppo_ring_max(q->mic.r) : -120.0;
    o->jb_target_ms = std::clamp(20.0 + 2.0 * o->jitter_p95_ms, 20.0, 400.0);

    if (q->samples >= 4) {  // do not flap on the first noisy samples
        int level = 0;
        if (o->loss_mean_permille >= 15 || o->rtt_p95_ms >= 200 || o->jitter_p95_ms >= 30) level = 1;
        if (o->loss_mean_permille >= 50 || o->rtt_p95_ms >= 400 || o->jitter_p95_ms >= 60) level = 2;
        if (o->loss_mean_permille >= 150 || o->rtt_p95_ms >= 800 || o->jitter_p95_ms >= 150) level = 3;
        o->level = level;
        o->advise_reduce_video = level >= 2;
        o->advise_audio_only = level >= 3;
    }
    // Microphone silent: the last five known peaks are all below -60 dBFS.
    size_t n = goppo_ring_len(q->mic.r);
    if (n >= 5) {
        // goppo_ring_* has no random access; the max over the whole window is a conservative test.
        o->silent_mic = goppo_ring_max(q->mic.r) < -60.0 ? 1 : 0;
    }
    return 0;
}

int goppo_media_selftest(int *frames_out, int *peak_out) {
    constexpr int N = GOPPO_FRAME_SAMPLES_48K;
    GoppoOpusEnc *enc = goppo_opus_enc_new(GOPPO_RATE_48K, 1, 32000, nullptr);
    GoppoOpusDec *dec = goppo_opus_dec_new(GOPPO_RATE_48K, 1, nullptr);
    int rc = -1, frames = 0;
    float peak = 0;
    if (enc && dec) {
        std::vector<int16_t> pcm(N), out(N);
        uint8_t pkt[1275];
        rc = 0;
        for (int f = 0; f < 20 && rc == 0; f++) {
            for (int i = 0; i < N; i++) pcm[i] = (int16_t)(12000.0 * std::sin(2.0 * 3.14159265358979 * 440.0 * (f * N + i) / 48000.0));
            if (goppo_pcm_apply_gain(pcm.data(), N, 6.0f) != 0 || goppo_pcm_soft_limit(pcm.data(), N, 0.9f) != 0) { rc = -2; break; }
            int n = goppo_opus_encode(enc, pcm.data(), N, pkt, sizeof pkt);
            if (n <= 0 || goppo_opus_decode(dec, pkt, n, out.data(), N) != N) { rc = -3; break; }
            peak = std::max(peak, goppo_pcm_peak(out.data(), N));
            frames++;
        }
        if (rc == 0 && !(peak > 0.3f && peak <= 1.0f)) rc = -4;
    }
    goppo_opus_enc_free(enc);
    goppo_opus_dec_free(dec);
    if (frames_out) *frames_out = frames;
    if (peak_out) *peak_out = (int)std::lround(peak * 1000.0f);
    return rc;
}

uint64_t goppo_media_samples_processed(void) { return g_samples.load(std::memory_order_relaxed); }

const char *goppo_media_version(void) {
    static char buf[64];
    std::snprintf(buf, sizeof buf, "goppo-media-engine/2.0.0 (c++%ld)", (long)__cplusplus / 100 % 100);
    return buf;
}

}  // extern "C"
