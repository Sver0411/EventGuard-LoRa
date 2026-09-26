#include "e220_stream_parser.h"

#include <string.h>

#include "protocol.h"

static void clear_frame(eg_e220_stream_parser_t *parser) {
    parser->state = RX_WAIT_E;
    parser->frame_offset = 0;
    parser->expected_length = 0;
    parser->frame_start_timestamp_us = 0;
    parser->last_byte_timestamp_us = 0;
    parser->header_complete_timestamp_us = 0;
}

void eg_e220_stream_parser_reset(eg_e220_stream_parser_t *parser) {
    if (!parser) return;
    memset(parser, 0, sizeof(*parser));
    parser->state = RX_WAIT_E;
}

size_t eg_e220_stream_parser_read_target(const eg_e220_stream_parser_t *parser) {
    if (!parser || parser->state == RX_WAIT_E || parser->state == RX_WAIT_G ||
        parser->state == RX_WAIT_VERSION || parser->state == RX_WAIT_TYPE) return 1;
    return parser->expected_length > parser->frame_offset ?
        parser->expected_length - parser->frame_offset : 1;
}

bool eg_e220_stream_parser_has_partial_frame(const eg_e220_stream_parser_t *parser) {
    return parser && parser->frame_offset != 0;
}

bool eg_e220_stream_parser_is_reading_body(const eg_e220_stream_parser_t *parser) {
    return parser && parser->state == RX_READ_BODY;
}

static void start_candidate(eg_e220_stream_parser_t *parser, int64_t timestamp_us) {
    clear_frame(parser);
    parser->frame[0] = 'E';
    parser->frame_offset = 1;
    parser->state = RX_WAIT_G;
    parser->frame_start_timestamp_us = timestamp_us;
    parser->last_byte_timestamp_us = timestamp_us;
    parser->diagnostics.frames_started++;
}

static void resync_on_byte(eg_e220_stream_parser_t *parser, uint8_t byte, int64_t timestamp_us) {
    parser->diagnostics.parser_resyncs++;
    clear_frame(parser);
    if (byte == 'E') start_candidate(parser, timestamp_us);
}

static int resync_from_failed_frame(eg_e220_stream_parser_t *parser, uint8_t *frame,
                                    size_t capacity, int64_t timestamp_us) {
    uint8_t failed[EG_E220_MAX_FRAME_SIZE];
    size_t failed_length = parser->expected_length;
    memcpy(failed, parser->frame, failed_length);
    clear_frame(parser);
    for (size_t i = 1; i < failed_length; ++i) {
        if (failed[i] != 'E') continue;
        if (i + 1 < failed_length && failed[i + 1] != 'G') continue;
        if (i + 2 < failed_length && failed[i + 2] != EG_PROTOCOL_VERSION) continue;
        if (i + 3 < failed_length && failed[i + 3] != EG_FRAME_DATA && failed[i + 3] != EG_FRAME_ACK) continue;
        start_candidate(parser, timestamp_us);
        for (size_t j = i + 1; j < failed_length; ++j) {
            if (eg_e220_stream_parser_feed(parser, failed[j], timestamp_us, frame, capacity)) return 1;
        }
        return 0;
    }
    return 0;
}

int eg_e220_stream_parser_feed(eg_e220_stream_parser_t *parser, uint8_t byte,
                               int64_t timestamp_us, uint8_t *frame, size_t capacity) {
    if (!parser || !frame) return 0;
    parser->last_byte_timestamp_us = timestamp_us;
    switch (parser->state) {
    case RX_WAIT_E:
        if (byte == 'E') start_candidate(parser, timestamp_us);
        return 0;
    case RX_WAIT_G:
        if (byte == 'G') {
            parser->frame[parser->frame_offset++] = byte;
            parser->state = RX_WAIT_VERSION;
        } else if (byte == 'E') {
            start_candidate(parser, timestamp_us);
        } else {
            clear_frame(parser);
        }
        return 0;
    case RX_WAIT_VERSION:
        if (byte != EG_PROTOCOL_VERSION) {
            parser->diagnostics.invalid_version++;
            resync_on_byte(parser, byte, timestamp_us);
            return 0;
        }
        parser->frame[parser->frame_offset++] = byte;
        parser->state = RX_WAIT_TYPE;
        return 0;
    case RX_WAIT_TYPE:
        if (byte != EG_FRAME_DATA && byte != EG_FRAME_ACK) {
            parser->diagnostics.invalid_type++;
            resync_on_byte(parser, byte, timestamp_us);
            return 0;
        }
        parser->frame[parser->frame_offset++] = byte;
        parser->expected_length = byte == EG_FRAME_DATA ? EG_DATA_FRAME_SIZE : EG_ACK_FRAME_SIZE;
        parser->header_complete_timestamp_us = timestamp_us;
        parser->state = RX_READ_BODY;
        return 0;
    case RX_READ_BODY:
        if (parser->frame_offset >= sizeof(parser->frame) ||
            parser->frame_offset >= parser->expected_length) {
            parser->diagnostics.invalid_type++;
            resync_on_byte(parser, byte, timestamp_us);
            return 0;
        }
        parser->frame[parser->frame_offset++] = byte;
        if (parser->frame_offset != parser->expected_length) return 0;
        {
            size_t crc_offset = parser->expected_length - 2;
            uint16_t observed = (uint16_t)parser->frame[crc_offset] |
                ((uint16_t)parser->frame[crc_offset + 1] << 8);
            uint16_t expected = eg_crc16(parser->frame, crc_offset);
            if (observed != expected) {
                parser->diagnostics.crc_failures++;
                parser->diagnostics.parser_resyncs++;
                return resync_from_failed_frame(parser, frame, capacity, timestamp_us);
            }
            if (capacity < parser->expected_length) {
                parser->diagnostics.parser_resyncs++;
                clear_frame(parser);
                return 0;
            }
            memcpy(frame, parser->frame, parser->expected_length);
            parser->diagnostics.frames_completed++;
            clear_frame(parser);
            return 1;
        }
    }
    parser->diagnostics.parser_resyncs++;
    clear_frame(parser);
    return 0;
}
