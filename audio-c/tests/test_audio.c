#include "goppo_audio.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

static void test_pcm(void) {
    int16_t s[4] = {1000, -1000, 20000, -20000};
    assert(goppo_pcm_apply_gain(s, 4, 6.0206f) == 0);
    assert(s[0] > 1990 && s[0] < 2010);            /* +6 dB doubles */
    assert(s[2] == 32767 && s[3] == -32768);       /* clipped, not wrapped */
    assert(goppo_pcm_apply_gain(s, 4, 99.0f) < 0); /* absurd gain refused */
    assert(goppo_pcm_apply_gain(NULL, 4, 0.0f) < 0);
    int16_t l[3] = {30000, -32768, 100};
    assert(goppo_pcm_soft_limit(l, 3, 0.5f) == 0);
    assert(l[0] < 32767 && l[0] > 16384 && l[1] > -32768 && l[2] == 100);
    int16_t mono[3] = {1, 2, 3}, st[6], back[3];
    assert(goppo_pcm_mono_to_stereo(mono, st, 3) == 0 && st[4] == 3 && st[5] == 3 && st[0] == 1);
    assert(goppo_pcm_stereo_to_mono(st, back, 3) == 0 && memcmp(mono, back, sizeof mono) == 0);
    float f[2] = {0.5f, -2.0f}, rt[2]; int16_t q[2];
    assert(goppo_pcm_f32_to_s16(f, q, 2) == 0 && q[0] == 16384 && q[1] == -32768);
    assert(goppo_pcm_s16_to_f32(q, rt, 2) == 0 && fabsf(rt[0] - 0.5f) < 1e-4f);
    int16_t pk[3] = {0, -16384, 100};
    assert(fabsf(goppo_pcm_peak(pk, 3) - 0.5f) < 1e-4f);
    assert(fabsf(goppo_pcm_peak_dbfs(pk, 3) + 6.02f) < 0.05f);
    assert(goppo_level_to_dbfs(0.0f) == -120.0f);
}

static void test_ring(void) {
    GoppoRing *r = goppo_ring_new(5);
    assert(r && goppo_ring_len(r) == 0 && goppo_ring_mean(r) == 0.0);
    for (int i = 1; i <= 7; i++) goppo_ring_push(r, (double)i);   /* keeps 3..7 */
    assert(goppo_ring_len(r) == 5);
    assert(goppo_ring_min(r) == 3 && goppo_ring_max(r) == 7 && goppo_ring_last(r) == 7);
    assert(fabs(goppo_ring_mean(r) - 5.0) < 1e-9);
    assert(goppo_ring_percentile(r, 0.5) == 5 && goppo_ring_percentile(r, 1.0) == 7 && goppo_ring_percentile(r, 0.0) == 3);
    goppo_ring_free(r);
    assert(goppo_ring_new(0) == NULL);
}

static void test_opus(void) {
    int err;
    assert(goppo_opus_enc_new(12345, 1, 32000, &err) == NULL && err != 0);   /* invalid rate refused */
    int bytes = 0, ratio = 0;
    assert(goppo_audio_selftest(&bytes, &ratio) == 0);
    assert(bytes > 5 && bytes < 200);
    assert(ratio > 700 && ratio < 1300);
    printf("  opus round trip: %d bytes/frame, rms ratio %.3f\n", bytes, ratio / 1000.0);
}

int main(void) {
    test_pcm(); test_ring(); test_opus();
    printf("audio-c tests passed: %s\n", goppo_audio_version());
    return 0;
}
