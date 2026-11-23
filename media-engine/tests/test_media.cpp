#include "goppo_media.h"
#include <cassert>
#include <cstdio>

static GoppoQSample sample(double rtt, double jit, double loss, double peak = 0.3) {
    GoppoQSample s{};
    s.rtt_ms = rtt; s.jitter_ms = jit; s.loss_permille = loss; s.bitrate_kbps = 500;
    s.audio_playout_ms = 1000; s.video_playout_ms = 940; s.jb_delay_ms = 40; s.audio_peak = peak;
    return s;
}

int main() {
    GoppoQ *q = goppo_q_new();
    assert(q);
    GoppoQReport r{};
    for (int i = 0; i < 8; i++) { auto s = sample(40, 5, 2); assert(goppo_q_add(q, &s) == 0); }
    assert(goppo_q_report(q, &r) == 0);
    assert(r.samples == 8 && r.level == 0 && !r.advise_audio_only && !r.silent_mic);
    assert(r.av_skew_ms > 59 && r.av_skew_ms < 61);            // audio ahead of video by 60 ms
    assert(r.jb_target_ms >= 20 && r.jb_target_ms <= 40);
    assert(r.mic_peak_dbfs > -11 && r.mic_peak_dbfs < -9);       // 0.3 -> about -10.5 dBFS
    goppo_q_free(q);

    q = goppo_q_new();
    for (int i = 0; i < 3; i++) { auto s = sample(900, 200, 300); goppo_q_add(q, &s); }
    goppo_q_report(q, &r);
    assert(r.level == 0);                                         // fewer than 4 samples: no verdict yet
    for (int i = 0; i < 6; i++) { auto s = sample(900, 200, 300); goppo_q_add(q, &s); }
    goppo_q_report(q, &r);
    assert(r.level == 3 && r.advise_audio_only && r.advise_reduce_video);
    assert(r.jb_target_ms == 400);                                // clamped
    goppo_q_free(q);

    q = goppo_q_new();
    for (int i = 0; i < 6; i++) { auto s = sample(250, 10, 10, 0.0001); goppo_q_add(q, &s); }
    goppo_q_report(q, &r);
    assert(r.level == 1 && r.silent_mic == 1);                    // RTT 250 -> fair; mic below -60 dBFS
    GoppoQSample bad = sample(-1, 0, 0);
    assert(goppo_q_add(q, &bad) < 0);                             // invalid telemetry refused
    bad = sample(10, 0, 2000);
    assert(goppo_q_add(q, &bad) < 0);
    assert(goppo_q_add(nullptr, &bad) < 0);
    goppo_q_free(q);

    int frames = 0, peak = 0;
    assert(goppo_media_selftest(&frames, &peak) == 0);
    assert(frames == 20 && peak > 300);
    assert(goppo_media_samples_processed() >= 17);
    std::printf("media-engine tests passed: %s (selftest frames=%d peak=%.3f)\n", goppo_media_version(), frames, peak / 1000.0);
    return 0;
}
