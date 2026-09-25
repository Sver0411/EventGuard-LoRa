#include "strategy.h"
#include "faults.h"
#include <string.h>

void eg_link_init(eg_link_estimator_t *state, uint8_t window, double degraded, double bad, uint8_t streak) {
    memset(state, 0, sizeof(*state));
    state->window = window > 24 ? 24 : (window ? window : 1);
    state->degraded_threshold = degraded;
    state->bad_threshold = bad;
    state->bad_fail_streak = streak;
}
void eg_link_observe_copy(eg_link_estimator_t *state, bool ack_accepted, bool first_copy) {
    state->copy_attempts++;
    state->copy_ack_success += ack_accepted;
    state->copy_failures += !ack_accepted;
    state->consecutive_copy_failures = ack_accepted ? 0 : state->consecutive_copy_failures + 1;
    if (!first_copy) return;
    state->history[state->next] = ack_accepted;
    state->next = (state->next + 1) % state->window;
    if (state->count < state->window) state->count++;
    state->fail_streak = ack_accepted ? 0 : (state->fail_streak < 255 ? state->fail_streak + 1 : 255);
}
eg_link_state_t eg_link_state(const eg_link_estimator_t *state) {
    if (state->fail_streak >= state->bad_fail_streak) return EG_LINK_BAD;
    if (!state->count) return EG_LINK_GOOD;
    uint8_t successes = 0;
    for (uint8_t i = 0; i < state->count; ++i) successes += state->history[i];
    double ratio = (double)successes / state->count;
    if (ratio < state->bad_threshold) return EG_LINK_BAD;
    if (ratio < state->degraded_threshold) return EG_LINK_DEGRADED;
    return EG_LINK_GOOD;
}
uint8_t eg_choose_redundancy(eg_strategy_t strategy, uint8_t importance, eg_link_state_t link,
                             uint8_t fixed, uint8_t maximum) {
    uint8_t count = 1;
    if (strategy == EG_FIXED_REDUNDANCY) count = fixed;
    else if (strategy >= EG_FIXED_1 && strategy <= EG_FIXED_3) count = (uint8_t)(strategy - EG_FIXED_1 + 1);
    else if (strategy == EG_IMPORTANCE_ONLY) count = importance + 1;
    else if (strategy == EG_LINK_ONLY) count = link + 1;
    else if (strategy == EG_EVENTGUARD) {
        count = importance + 1;
        if (link == EG_LINK_DEGRADED) count++;
        else if (link == EG_LINK_BAD && importance) count += 2;
    }
    if (count > maximum) count = maximum;
    return count ? count : 1;
}
bool eg_budget_allocate(eg_strategy_t strategy, size_t count, uint32_t budget, uint32_t seed,
                        uint8_t maximum, uint8_t *output) {
    if (!output || count > EG_MAX_TRACE || !count || budget < count || budget > count * maximum ||
        (strategy != EG_UNIFORM_BUDGET && strategy != EG_RANDOM_BUDGET)) return false;
    memset(output, 1, count);
    uint32_t remaining = budget - count;
    for (uint8_t layer = 1; layer < maximum && remaining; ++layer) {
        uint16_t order[EG_MAX_TRACE];
        for (size_t i = 0; i < count; ++i) order[i] = (uint16_t)i;
        if (strategy == EG_RANDOM_BUDGET) {
            for (size_t i = 1; i < count; ++i) {
                uint16_t value = order[i];
                uint32_t key = eg_hash32(seed ^ 0xB0D6E7U, false, (uint32_t)(layer * count + value));
                size_t j = i;
                while (j) {
                    uint32_t prior = eg_hash32(seed ^ 0xB0D6E7U, false, (uint32_t)(layer * count + order[j - 1]));
                    if (prior < key || (prior == key && order[j - 1] < value)) break;
                    order[j] = order[j - 1]; --j;
                }
                order[j] = value;
            }
        }
        for (size_t i = 0; i < count && remaining; ++i, --remaining) output[order[i]]++;
    }
    return remaining == 0;
}
