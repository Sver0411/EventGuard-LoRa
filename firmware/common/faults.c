#include "faults.h"

#include <math.h>
#include <string.h>

uint32_t eg_hash32(uint32_t seed, bool ack, uint32_t slot) {
    uint32_t salt = ack ? 0x0041434BU : 0x44415441U;
    uint32_t value = seed ^ salt ^ (slot * 0x9E3779B9U);
    value += 0x9E3779B9U;
    value = (value ^ (value >> 16)) * 0x85EBCA6BU;
    value = (value ^ (value >> 13)) * 0xC2B2AE35U;
    return value ^ (value >> 16);
}

static void build_one(bool *mask, size_t count, uint32_t seed, bool ack, double rate, eg_fault_model_t model, uint8_t burst_length) {
    memset(mask, 0, count * sizeof(bool));
    if (rate <= 0.0f || count == 0) return;
    if (model == EG_BURST_SAMPLE) {
        size_t samples = count / EG_MAX_COPIES;
        bool sample_mask[EG_MAX_TRACE] = {0};
        build_one(sample_mask, samples, seed, ack, rate, EG_BURST_COPY, burst_length);
        for (size_t i = 0; i < count; ++i) mask[i] = sample_mask[i / EG_MAX_COPIES];
        return;
    }
    if (model == EG_RANDOM_COPY) {
        uint64_t threshold = (uint64_t)(rate * 4294967296.0);
        for (size_t i = 0; i < count; ++i) mask[i] = eg_hash32(seed, ack, (uint32_t)i) < threshold;
        return;
    }
    size_t remaining = (size_t)floor((double)count * rate + 0.5);
    size_t attempts = 0;
    if (burst_length < 2) burst_length = 2;
    while (remaining && attempts < count * 20) {
        size_t length = burst_length < remaining ? burst_length : remaining;
        size_t choices = count - length + 1;
        size_t start = eg_hash32(seed ^ 0x00B17B17U, ack, (uint32_t)attempts) % choices;
        bool clear = true;
        for (size_t i = start; i < start + length; ++i) if (mask[i]) { clear = false; break; }
        if (clear) {
            for (size_t i = start; i < start + length; ++i) mask[i] = true;
            remaining -= length;
        }
        attempts++;
    }
    for (size_t i = 0; i < count && remaining; ++i) if (!mask[i]) { mask[i] = true; remaining--; }
}

void eg_fault_build(eg_fault_table_t *table, uint32_t seed, double rate, bool burst, uint8_t burst_length, size_t trace_length) {
    eg_fault_build_model(table, seed, rate, burst ? EG_BURST_COPY : EG_RANDOM_COPY, burst_length, trace_length);
}

void eg_fault_build_model(eg_fault_table_t *table, uint32_t seed, double rate, eg_fault_model_t model, uint8_t burst_length, size_t trace_length) {
    if (!table) return;
    table->slots = trace_length > EG_MAX_TRACE ? EG_MAX_TRACE * EG_MAX_COPIES : trace_length * EG_MAX_COPIES;
    build_one(table->data, table->slots, seed, false, rate, model, burst_length);
    build_one(table->ack, table->slots, seed, true, rate, model, burst_length);
}

bool eg_fault_drop(const eg_fault_table_t *table, bool ack, uint16_t sample_id, uint8_t copy_index) {
    if (!table || copy_index >= EG_MAX_COPIES) return false;
    size_t slot = (size_t)sample_id * EG_MAX_COPIES + copy_index;
    if (slot >= table->slots) return false;
    return ack ? table->ack[slot] : table->data[slot];
}
