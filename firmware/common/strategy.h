#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef enum {
    EG_NO_PROTECTION, EG_FIXED_REDUNDANCY, EG_EVENTGUARD,
    EG_FIXED_1, EG_FIXED_2, EG_FIXED_3, EG_IMPORTANCE_ONLY,
    EG_LINK_ONLY, EG_UNIFORM_BUDGET, EG_RANDOM_BUDGET
} eg_strategy_t;
typedef enum { EG_LINK_GOOD, EG_LINK_DEGRADED, EG_LINK_BAD } eg_link_state_t;
typedef struct {
    bool history[24];
    uint8_t window, count, next, fail_streak, bad_fail_streak;
    double degraded_threshold, bad_threshold;
    uint32_t copy_attempts, copy_ack_success, copy_failures, consecutive_copy_failures;
} eg_link_estimator_t;
void eg_link_init(eg_link_estimator_t *state, uint8_t window, double degraded, double bad, uint8_t streak);
void eg_link_observe_copy(eg_link_estimator_t *state, bool ack_accepted, bool first_copy);
eg_link_state_t eg_link_state(const eg_link_estimator_t *state);
uint8_t eg_choose_redundancy(eg_strategy_t strategy, uint8_t importance, eg_link_state_t link,
                             uint8_t fixed, uint8_t maximum);
bool eg_budget_allocate(eg_strategy_t strategy, size_t count, uint32_t budget, uint32_t seed,
                        uint8_t maximum, uint8_t *output);
