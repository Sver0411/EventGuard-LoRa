#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "faults.h"
#include "importance.h"
#include "strategy.h"

int main(int argc, char **argv) {
    if (argc != 8) return 2;
    uint32_t seed = (uint32_t)strtoul(argv[1], NULL, 10);
    double rate = strtod(argv[2], NULL);
    eg_fault_model_t model = (eg_fault_model_t)atoi(argv[3]);
    eg_strategy_t strategy = (eg_strategy_t)atoi(argv[4]);
    uint32_t budget = (uint32_t)strtoul(argv[5], NULL, 10);
    size_t count = (size_t)strtoul(argv[6], NULL, 10);
    uint8_t burst_length = (uint8_t)atoi(argv[7]);
    eg_fault_table_t *table = calloc(1, sizeof(*table));
    if (!table) return 3;
    eg_fault_build_model(table, seed, rate, model, burst_length, count);
    eg_importance_config_t config = {.important_threshold = .85f, .critical_threshold = 3.0f,
                                     .scales = {.5f, 5.0f, 100.0f, 8.0f}, .baseline_alpha = .06f};
    eg_importance_state_t classifier = {0};
    eg_link_estimator_t link;
    eg_link_init(&link, 12, .80, .50, 3);
    uint8_t allocated[EG_MAX_TRACE] = {0};
    if ((strategy == EG_UNIFORM_BUDGET || strategy == EG_RANDOM_BUDGET) &&
        !eg_budget_allocate(strategy, count, budget, seed, 3, allocated)) return 4;
    for (size_t i = 0; i < count; ++i) {
        unsigned id, timestamp;
        float t, h, l, s;
        if (scanf("%u,%u,%f,%f,%f,%f", &id, &timestamp, &t, &h, &l, &s) != 6) return 5;
        eg_observation_t obs = {.timestamp_ms = timestamp, .values = {t, h, l, s}};
        float score = 0;
        uint8_t importance = eg_classify(&classifier, &obs, &config, &score);
        eg_link_state_t state = eg_link_state(&link);
        uint8_t copies = (strategy == EG_UNIFORM_BUDGET || strategy == EG_RANDOM_BUDGET) ? allocated[i] :
            eg_choose_redundancy(strategy, importance, state, 2, 3);
        printf("%u,%u,%u,%.5f", importance, copies, state, score);
        for (uint8_t copy = 0; copy < 3; ++copy)
            printf(",%u,%u", eg_fault_drop(table, false, (uint16_t)i, copy),
                   eg_fault_drop(table, true, (uint16_t)i, copy));
        putchar('\n');
        for (uint8_t copy = 0; copy < copies; ++copy) {
            bool accepted = !eg_fault_drop(table, false, (uint16_t)i, copy) &&
                            !eg_fault_drop(table, true, (uint16_t)i, copy);
            eg_link_observe_copy(&link, accepted, copy == 0);
        }
    }
    free(table);
    return 0;
}
