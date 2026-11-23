/* Goppo C++ media/quality engine, exposed through a plain C ABI (callable from Go via cgo).
 *
 * Architecture note: media is peer-to-peer (DTLS-SRTP), so this engine never sees media packets.
 * It processes the *telemetry* each browser reports about its own received media (RTCStats), plus
 * synthetic PCM frames for its self-test, and produces quality advice for the clients. */
#ifndef GOPPO_MEDIA_H
#define GOPPO_MEDIA_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

typedef struct GoppoQSample {
    double rtt_ms;
    double jitter_ms;
    double loss_permille;     /* 0..1000 */
    double bitrate_kbps;
    double audio_playout_ms;  /* estimatedPlayoutTimestamp of the audio track (0 = unknown) */
    double video_playout_ms;  /* same for video (0 = unknown / audio-only) */
    double jb_delay_ms;       /* average jitter-buffer delay */
    double audio_peak;        /* 0..1 peak of the locally captured microphone signal, <0 = unknown */
} GoppoQSample;

typedef struct GoppoQReport {
    int32_t samples;
    int32_t level;            /* 0 good, 1 fair, 2 poor, 3 bad */
    int32_t advise_reduce_video;
    int32_t advise_audio_only;
    int32_t silent_mic;
    double  rtt_p50_ms, rtt_p95_ms, jitter_p95_ms, loss_mean_permille, bitrate_mean_kbps;
    double  av_skew_ms;       /* mean(audio - video playout), 0 when not applicable */
    double  jb_target_ms;     /* recommended playout/jitter-buffer target */
    double  mic_peak_dbfs;
} GoppoQReport;

typedef struct GoppoQ GoppoQ;
GoppoQ *goppo_q_new(void);
void    goppo_q_free(GoppoQ *q);
int     goppo_q_add(GoppoQ *q, const GoppoQSample *s);        /* 0 ok, <0 invalid sample */
int     goppo_q_report(GoppoQ *q, GoppoQReport *out);          /* 0 ok */

/* Frame engine self-test: tone -> gain+limiter (C) -> Opus encode/decode (C) -> checks. 0 = ok. */
int         goppo_media_selftest(int *frames_processed, int *peak_x1000);
uint64_t    goppo_media_samples_processed(void);               /* total telemetry samples handled */
const char *goppo_media_version(void);

#ifdef __cplusplus
}
#endif
#endif
