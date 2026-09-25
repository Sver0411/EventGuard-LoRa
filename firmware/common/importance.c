#include "importance.h"

#include <math.h>
#include <string.h>

static const eg_importance_config_t default_config = {
    .important_threshold = 0.85f, .critical_threshold = 2.40f,
    .scales = {0.5f, 5.0f, 100.0f, 8.0f}, .baseline_alpha = 0.06f};

uint8_t eg_classify(eg_importance_state_t *state, const eg_observation_t *obs,
                    const eg_importance_config_t *config, float *score_out) {
    if (!state || !obs) return 0;
    if (!config) config = &default_config;
    if (!state->initialized) {
        memcpy(state->baseline, obs->values, sizeof(state->baseline));
        state->previous = *obs;
        state->initialized = true;
        if (score_out) *score_out = 0.0f;
        return 0;
    }
    float elapsed = (obs->timestamp_ms - state->previous.timestamp_ms) / 1000.0f;
    if (elapsed < 0.001f) elapsed = 0.001f;
    float sum_change = 0.0f, sum_level = 0.0f, sum_rate = 0.0f;
    int simultaneous = 0;
    for (int i = 0; i < 4; ++i) {
        float scale = fmaxf(0.000001f, config->scales[i]);
        float change = fminf(4.0f, fabsf(obs->values[i] - state->previous.values[i]) / scale);
        float level = fminf(4.0f, fabsf(obs->values[i] - state->baseline[i]) / scale);
        float rate = fminf(4.0f, change / fmaxf(0.25f, elapsed / 5.0f));
        sum_change += change; sum_level += level; sum_rate += rate;
        if (change >= 0.25f) simultaneous++;
    }
    float score = 0.45f * sum_change + 0.25f * sum_level + 0.40f * sum_rate
                + (simultaneous >= 2 ? 0.70f : 0.0f) + 0.10f * (state->persistence < 5 ? state->persistence : 5);
    uint8_t result;
    if (score >= config->critical_threshold) { result = 2; if (state->persistence < 255) state->persistence++; }
    else if (score >= config->important_threshold) { result = 1; if (state->persistence < 255) state->persistence++; }
    else {
        result = 0; state->persistence = 0;
        for (int i = 0; i < 4; ++i) state->baseline[i] = (1.0f - config->baseline_alpha) * state->baseline[i] + config->baseline_alpha * obs->values[i];
    }
    state->previous = *obs;
    if (score_out) *score_out = score;
    return result;
}
