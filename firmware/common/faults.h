#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define EG_MAX_TRACE 512
#define EG_MAX_COPIES 3

typedef struct {
    bool data[EG_MAX_TRACE * EG_MAX_COPIES];
    bool ack[EG_MAX_TRACE * EG_MAX_COPIES];
    size_t slots;
} eg_fault_table_t;

typedef enum { EG_RANDOM_COPY = 0, EG_BURST_COPY = 1, EG_BURST_SAMPLE = 2 } eg_fault_model_t;
uint32_t eg_hash32(uint32_t seed, bool ack, uint32_t slot);

void eg_fault_build(eg_fault_table_t *table, uint32_t seed, double rate, bool burst, uint8_t burst_length, size_t trace_length);
void eg_fault_build_model(eg_fault_table_t *table, uint32_t seed, double rate, eg_fault_model_t model,
                          uint8_t burst_length, size_t trace_length);
bool eg_fault_drop(const eg_fault_table_t *table, bool ack, uint16_t sample_id, uint8_t copy_index);
