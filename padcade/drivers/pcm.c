/* Scale one interleaved stereo block. A released key follows the same cubic envelope as the WAV export. */
#include <stdint.h>
#include <math.h>
#include <stddef.h>

/* Each entry bounds the rest of the clip starting at that stereo sample bucket. */
void remaining_peaks(const int16_t *samples, size_t count, unsigned stride, uint16_t *peaks)
{
    unsigned peak = 0;
    while (count) {
        size_t bucket = (count - 1) / stride;
        size_t start = bucket * stride;
        while (count > start) {
            int sample = samples[--count];
            unsigned value = sample < 0 ? -sample : sample;
            if (value > peak)
                peak = value;
        }
        peaks[bucket] = peak;
    }
}

void add_voice(int64_t *sums, const int16_t *samples, unsigned frames, double gain,
               unsigned remaining, unsigned release)
{
    if (!release && gain == 1.) {
        for (unsigned i = 0; i < 2 * frames; i++)
            sums[i] += samples[i];
        return;
    }
    for (unsigned i = 0; i < frames; i++) {
        double level = gain;
        if (release) {
            double t = (double)(remaining - i) / release;
            level *= t * t * t;
        }
        int left = samples[2*i], right = samples[2*i + 1];
        long scaled = lrint(left * level);
        sums[2*i] += scaled;
        sums[2*i + 1] += right == left ? scaled : lrint(right * level);
    }
}

/* The whole period is already mixed. Start reducing gain before a crest reaches
   full scale, so a rising bass wave stays curved without buffering another period. */
void limit_pcm(int64_t *sums, int16_t *out, unsigned count, double *gain, unsigned *hold,
               unsigned hold_frames, double recovery, double target)
{
    double start = *gain, ceiling = target;
    double slope = fmin(0., (target - start) / (count / 2));
    for (unsigned i = 0; i < count; i += 2) {
        int64_t a = sums[i], b = sums[i + 1];
        int64_t peak = a < 0 ? -a : a, other = b < 0 ? -b : b;
        if (other > peak)
            peak = other;
        if (peak > 32767) {
            double safe = 32767. / peak;
            unsigned frame = i / 2 + 1;
            if (safe < ceiling)
                ceiling = safe;
            if (start + frame * slope > safe)
                slope = (safe - start) / frame;
        }
    }
    if (start == 1. && ceiling == 1. && !*hold) {
        /* Samples already fit. Preserve their exact values without scaling. */
        for (unsigned i = 0; i < count; i++) {
            out[i] = (int16_t)sums[i];
            sums[i] = 0;
        }
        return;
    }
    unsigned left = *hold;
    double level = start;
    for (unsigned i = 0; i < count; i += 2) {
        if (slope < 0.)
            level = fmax(ceiling, start + (i / 2 + 1) * slope);
        else if (left)
            --left;
        else if (level < ceiling)
            level = fmin(ceiling, level + recovery);
        out[i] = (int16_t)(sums[i] * level);
        out[i + 1] = (int16_t)(sums[i + 1] * level);
        sums[i] = sums[i + 1] = 0;
    }
    *gain = level;
    *hold = ceiling < target ? hold_frames : left;
}
