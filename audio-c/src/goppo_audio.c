#include "goppo_audio.h"
#include <math.h>
#include <opus/opus.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int16_t clip16(float v) {
    if (v > 32767.0f) return 32767;
    if (v < -32768.0f) return -32768;
    return (int16_t)lrintf(v);
}

int goppo_pcm_apply_gain(int16_t *s, size_t n, float gain_db) {
    if (!s || !isfinite(gain_db) || gain_db < -60.0f || gain_db > 30.0f) return -1;
    const float g = powf(10.0f, gain_db / 20.0f);
    for (size_t i = 0; i < n; i++) s[i] = clip16((float)s[i] * g);
    return 0;
}

int goppo_pcm_soft_limit(int16_t *s, size_t n, float threshold) {
    if (!s || !(threshold > 0.05f && threshold < 1.0f)) return -1;
    const float t = threshold * 32768.0f, head = 32767.0f - t;
    for (size_t i = 0; i < n; i++) {
        float a = fabsf((float)s[i]);
        if (a <= t) continue;
        float over = (a - t) / head;                    /* 0..inf */
        float shaped = t + head * tanhf(over);          /* smooth knee, never exceeds full scale */
        s[i] = clip16(s[i] < 0 ? -shaped : shaped);
    }
    return 0;
}

int goppo_pcm_mono_to_stereo(const int16_t *mono, int16_t *stereo, size_t frames) {
    if (!mono || !stereo) return -1;
    for (size_t i = frames; i-- > 0;) { stereo[2 * i] = mono[i]; stereo[2 * i + 1] = mono[i]; }
    return 0;
}

int goppo_pcm_stereo_to_mono(const int16_t *stereo, int16_t *mono, size_t frames) {
    if (!stereo || !mono) return -1;
    for (size_t i = 0; i < frames; i++) mono[i] = (int16_t)(((int)stereo[2 * i] + (int)stereo[2 * i + 1]) / 2);
    return 0;
}

int goppo_pcm_f32_to_s16(const float *in, int16_t *out, size_t n) {
    if (!in || !out) return -1;
    for (size_t i = 0; i < n; i++) out[i] = clip16(isfinite(in[i]) ? in[i] * 32768.0f : 0.0f);
    return 0;
}

int goppo_pcm_s16_to_f32(const int16_t *in, float *out, size_t n) {
    if (!in || !out) return -1;
    for (size_t i = 0; i < n; i++) out[i] = (float)in[i] / 32768.0f;
    return 0;
}

float goppo_pcm_peak(const int16_t *s, size_t n) {
    if (!s) return 0.0f;
    int m = 0;
    for (size_t i = 0; i < n; i++) { int a = s[i] < 0 ? -(int)s[i] : s[i]; if (a > m) m = a; }
    return (float)m / 32768.0f;
}

float goppo_level_to_dbfs(float lin) {
    if (!isfinite(lin) || lin <= 1e-6f) return -120.0f;
    if (lin > 1.0f) lin = 1.0f;
    return 20.0f * log10f(lin);
}

float goppo_pcm_peak_dbfs(const int16_t *s, size_t n) { return goppo_level_to_dbfs(goppo_pcm_peak(s, n)); }

/* ---- Opus ---- */
struct GoppoOpusEnc { OpusEncoder *e; int channels; };
struct GoppoOpusDec { OpusDecoder *d; int channels; };

GoppoOpusEnc *goppo_opus_enc_new(int rate, int channels, int bitrate, int *err) {
    int e = 0;
    GoppoOpusEnc *h = calloc(1, sizeof *h);
    if (!h) { if (err) *err = OPUS_ALLOC_FAIL; return NULL; }
    h->e = opus_encoder_create(rate, channels, OPUS_APPLICATION_VOIP, &e);
    if (e != OPUS_OK || !h->e) { free(h); if (err) *err = e; return NULL; }
    h->channels = channels;
    if (bitrate > 0) opus_encoder_ctl(h->e, OPUS_SET_BITRATE(bitrate));
    opus_encoder_ctl(h->e, OPUS_SET_INBAND_FEC(1));
    if (err) *err = OPUS_OK;
    return h;
}
void goppo_opus_enc_free(GoppoOpusEnc *e) { if (e) { if (e->e) opus_encoder_destroy(e->e); free(e); } }
int goppo_opus_encode(GoppoOpusEnc *e, const int16_t *pcm, int fs, uint8_t *out, int max) {
    if (!e || !pcm || !out || fs <= 0 || max <= 0) return OPUS_BAD_ARG;
    return opus_encode(e->e, pcm, fs, out, max);
}
GoppoOpusDec *goppo_opus_dec_new(int rate, int channels, int *err) {
    int e = 0;
    GoppoOpusDec *h = calloc(1, sizeof *h);
    if (!h) { if (err) *err = OPUS_ALLOC_FAIL; return NULL; }
    h->d = opus_decoder_create(rate, channels, &e);
    if (e != OPUS_OK || !h->d) { free(h); if (err) *err = e; return NULL; }
    h->channels = channels;
    if (err) *err = OPUS_OK;
    return h;
}
void goppo_opus_dec_free(GoppoOpusDec *d) { if (d) { if (d->d) opus_decoder_destroy(d->d); free(d); } }
int goppo_opus_decode(GoppoOpusDec *d, const uint8_t *in, int len, int16_t *pcm, int fs) {
    if (!d || !pcm || fs <= 0) return OPUS_BAD_ARG;
    return opus_decode(d->d, in, len, pcm, fs, 0);
}

/* ---- ring ---- */
struct GoppoRing { double *v; size_t cap, head, len; };

GoppoRing *goppo_ring_new(size_t cap) {
    if (cap == 0 || cap > (1u << 20)) return NULL;
    GoppoRing *r = calloc(1, sizeof *r);
    if (!r) return NULL;
    r->v = calloc(cap, sizeof(double));
    if (!r->v) { free(r); return NULL; }
    r->cap = cap;
    return r;
}
void goppo_ring_free(GoppoRing *r) { if (r) { free(r->v); free(r); } }
void goppo_ring_push(GoppoRing *r, double x) {
    if (!r) return;
    r->v[r->head] = x;
    r->head = (r->head + 1) % r->cap;
    if (r->len < r->cap) r->len++;
}
size_t goppo_ring_len(const GoppoRing *r) { return r ? r->len : 0; }
static double at(const GoppoRing *r, size_t i) { /* i = 0 is the oldest */
    size_t start = (r->head + r->cap - r->len) % r->cap;
    return r->v[(start + i) % r->cap];
}
double goppo_ring_last(const GoppoRing *r) { return (r && r->len) ? at(r, r->len - 1) : 0.0; }
double goppo_ring_mean(const GoppoRing *r) {
    if (!r || !r->len) return 0.0;
    double s = 0; for (size_t i = 0; i < r->len; i++) s += at(r, i);
    return s / (double)r->len;
}
double goppo_ring_max(const GoppoRing *r) {
    if (!r || !r->len) return 0.0;
    double m = at(r, 0); for (size_t i = 1; i < r->len; i++) if (at(r, i) > m) m = at(r, i);
    return m;
}
double goppo_ring_min(const GoppoRing *r) {
    if (!r || !r->len) return 0.0;
    double m = at(r, 0); for (size_t i = 1; i < r->len; i++) if (at(r, i) < m) m = at(r, i);
    return m;
}
static int cmpd(const void *a, const void *b) { double x = *(const double *)a, y = *(const double *)b; return (x > y) - (x < y); }
double goppo_ring_percentile(const GoppoRing *r, double q) {
    if (!r || !r->len) return 0.0;
    if (q < 0) q = 0;
    if (q > 1) q = 1;
    double *tmp = malloc(r->len * sizeof(double));
    if (!tmp) return 0.0;
    for (size_t i = 0; i < r->len; i++) tmp[i] = at(r, i);
    qsort(tmp, r->len, sizeof(double), cmpd);
    size_t k = (size_t)ceil(q * (double)r->len); if (k == 0) k = 1;
    double out = tmp[k - 1];
    free(tmp);
    return out;
}

/* ---- self test ---- */
int goppo_audio_selftest(int *bytes_per_frame, int *rms_ratio_x1000) {
    enum { FRAMES = 50, N = GOPPO_FRAME_SAMPLES_48K };
    int err = 0, rc = -1;
    GoppoOpusEnc *enc = goppo_opus_enc_new(GOPPO_RATE_48K, 1, 32000, &err);
    GoppoOpusDec *dec = goppo_opus_dec_new(GOPPO_RATE_48K, 1, &err);
    if (!enc || !dec) goto done;
    int16_t in[N], out[N]; uint8_t pkt[1275];
    double rin = 0, rout = 0; long total_bytes = 0; size_t phase = 0;
    for (int f = 0; f < FRAMES; f++) {
        for (int i = 0; i < N; i++, phase++) in[i] = (int16_t)(8000.0 * sin(2.0 * 3.14159265358979323846 * 440.0 * (double)phase / 48000.0));
        int n = goppo_opus_encode(enc, in, N, pkt, sizeof pkt);
        if (n <= 0) goto done;
        int m = goppo_opus_decode(dec, pkt, n, out, N);
        if (m != N) goto done;
        total_bytes += n;
        if (f >= FRAMES / 2) for (int i = 0; i < N; i++) { rin += (double)in[i] * in[i]; rout += (double)out[i] * out[i]; }
    }
    double ratio = sqrt(rout / rin);
    if (bytes_per_frame) *bytes_per_frame = (int)(total_bytes / FRAMES);
    if (rms_ratio_x1000) *rms_ratio_x1000 = (int)lrint(ratio * 1000.0);
    rc = (ratio > 0.7 && ratio < 1.3 && total_bytes / FRAMES > 5) ? 0 : -2;
done:
    goppo_opus_enc_free(enc); goppo_opus_dec_free(dec);
    return rc;
}

const char *goppo_audio_version(void) {
    static char buf[96];
    snprintf(buf, sizeof buf, "goppo-audio/2.0.0 (c) %s", opus_get_version_string());
    return buf;
}
