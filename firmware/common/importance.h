#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    uint32_t timestamp_ms;
    float values[4];
} eg_observation_t;

typedef struct {
    float important_threshold;
    float critical_threshold;
    float scales[4];
    float baseline_alpha;
} eg_importance_config_t;

typedef struct {
    bool initialized;
    eg_observation_t previous;
    float baseline[4];
    uint8_t persistence;
} eg_importance_state_t;

/* 0 NORMAL, 1 IMPORTANT, 2 CRITICAL. */
uint8_t eg_classify(eg_importance_state_t *state, const eg_observation_t *observation,
                    const eg_importance_config_t *config, float *score_out);
