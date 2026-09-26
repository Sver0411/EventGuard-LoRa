#pragma once

#include <stddef.h>
#include <stdint.h>

#define EG_E220_MAX_FRAME_SIZE 26

typedef struct {
    uint64_t uart_bytes_received;
    uint64_t frames_started;
    uint64_t frames_completed;
    uint64_t partial_header_timeouts;
    uint64_t partial_body_timeouts;
    uint64_t short_reads;
    uint64_t parser_resyncs;
    uint64_t crc_failures;
    uint64_t invalid_type;
    uint64_t invalid_version;
    uint64_t aux_interrupts;
    uint64_t aux_ring_overflow;
    size_t rx_buffered_bytes_peak;
} eg_e220_diagnostics_t;

typedef struct {
    int64_t uart_tx_done_timestamp_us;
    int64_t aux_ready_timestamp_us;
} eg_e220_tx_timing_t;

int eg_e220_init(void);
int eg_e220_send(const uint8_t *data, size_t length, uint32_t timeout_ms);
/* 1 frame received, 0 timeout, negative on UART/frame transport failure. */
int eg_e220_receive(uint8_t *frame, size_t capacity, uint32_t timeout_ms);
void eg_e220_get_diagnostics(eg_e220_diagnostics_t *out);
void eg_e220_reset_diagnostics(void);
eg_e220_tx_timing_t eg_e220_get_last_tx_timing(void);
