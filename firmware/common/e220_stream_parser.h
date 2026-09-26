#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "e220.h"

typedef enum {
    RX_WAIT_E = 0,
    RX_WAIT_G,
    RX_WAIT_VERSION,
    RX_WAIT_TYPE,
    RX_READ_BODY,
} eg_rx_state_t;

typedef struct {
    eg_rx_state_t state;
    uint8_t frame[EG_E220_MAX_FRAME_SIZE];
    size_t frame_offset;
    size_t expected_length;
    int64_t frame_start_timestamp_us;
    int64_t last_byte_timestamp_us;
    int64_t header_complete_timestamp_us;
    eg_e220_diagnostics_t diagnostics;
} eg_e220_stream_parser_t;

void eg_e220_stream_parser_reset(eg_e220_stream_parser_t *parser);
size_t eg_e220_stream_parser_read_target(const eg_e220_stream_parser_t *parser);
bool eg_e220_stream_parser_has_partial_frame(const eg_e220_stream_parser_t *parser);
bool eg_e220_stream_parser_is_reading_body(const eg_e220_stream_parser_t *parser);
/* Returns 1 for a CRC-valid frame, 0 while incomplete or after resynchronizing. */
int eg_e220_stream_parser_feed(eg_e220_stream_parser_t *parser, uint8_t byte,
                               int64_t timestamp_us, uint8_t *frame, size_t capacity);
