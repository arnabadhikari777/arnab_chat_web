/* Goppo C audio library: Opus codec wrapper, PCM utilities, fixed-size ring buffers.
 * Opus (libopus) is MANDATORY: the library does not compile without it. */
#ifndef GOPPO_AUDIO_H
#define GOPPO_AUDIO_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

#define GOPPO_RATE_48K 48000
#define GOPPO_FRAME_MS 20
#define GOPPO_FRAME_SAMPLES_48K 960 /* per channel, 20 ms */

/* ---- PCM utilities (all return 0 on success, <0 on bad arguments) ---- */
int   goppo_pcm_apply_gain(int16_t *s, size_t n, float gain_db);
int   goppo_pcm_soft_limit(int16_t *s, size_t n, float threshold /* 0..1 of full scale */);
int   goppo_pcm_mono_to_stereo(const int16_t *mono, int16_t *stereo, size_t frames);
int   goppo_pcm_stereo_to_mono(const int16_t *stereo, int16_t *mono, size_t frames);
int   goppo_pcm_f32_to_s16(const float *in, int16_t *out, size_t n);
int   goppo_pcm_s16_to_f32(const int16_t *in, float *out, size_t n);
float goppo_pcm_peak(const int16_t *s, size_t n);          /* 0..1 */
float goppo_pcm_peak_dbfs(const int16_t *s, size_t n);     /* -120..0 */
float goppo_level_to_dbfs(float linear_0_1);               /* -120..0 */

/* ---- Opus ---- */
typedef struct GoppoOpusEnc GoppoOpusEnc;
typedef struct GoppoOpusDec GoppoOpusDec;
GoppoOpusEnc *goppo_opus_enc_new(int rate, int channels, int bitrate_bps, int *err);
void          goppo_opus_enc_free(GoppoOpusEnc *e);
int           goppo_opus_encode(GoppoOpusEnc *e, const int16_t *pcm, int frame_samples, uint8_t *out, int max_bytes);
GoppoOpusDec *goppo_opus_dec_new(int rate, int channels, int *err);
void          goppo_opus_dec_free(GoppoOpusDec *d);
int           goppo_opus_decode(GoppoOpusDec *d, const uint8_t *in, int len, int16_t *pcm, int frame_samples);

/* ---- ring buffer of doubles (fixed capacity, overwrites oldest) ---- */
typedef struct GoppoRing GoppoRing;
GoppoRing *goppo_ring_new(size_t capacity);
void       goppo_ring_free(GoppoRing *r);
void       goppo_ring_push(GoppoRing *r, double v);
size_t     goppo_ring_len(const GoppoRing *r);
double     goppo_ring_last(const GoppoRing *r);
double     goppo_ring_mean(const GoppoRing *r);
double     goppo_ring_max(const GoppoRing *r);
double     goppo_ring_min(const GoppoRing *r);
/* q in 0..1, nearest-rank percentile over the stored values; 0 if empty */
double     goppo_ring_percentile(const GoppoRing *r, double q);

/* ---- lifecycle / verification ---- */
/* Runs a real Opus encode->decode round trip of a 440 Hz tone and checks the result.
 * Returns 0 if the codec works; fills bytes_per_frame and the decoded/original RMS ratio (x1000). */
int         goppo_audio_selftest(int *bytes_per_frame, int *rms_ratio_x1000);
const char *goppo_audio_version(void);

#ifdef __cplusplus
}
#endif
#endif
