#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "e220_stream_parser.h"
#include "protocol.h"

static size_t make_frame(uint16_t sequence, uint8_t *out) {
    eg_data_packet_t packet = {.node_id = 1, .sequence = sequence, .sample_id = sequence,
        .importance = 1, .copy_index = 0, .copy_count = 1, .uptime_ms = sequence * 1000u,
        .temperature_centi = 2200, .humidity_centi = 5000, .light_lux = 120,
        .soil_percent_tenths = 450};
    return eg_encode_data(&packet, out, EG_DATA_FRAME_SIZE);
}

static int feed_bytes(eg_e220_stream_parser_t *parser, const uint8_t *input, size_t length,
                      uint8_t *output, size_t *output_length) {
    int frames = 0;
    for (size_t i = 0; i < length; ++i) {
        uint8_t parsed[EG_E220_MAX_FRAME_SIZE];
        if (eg_e220_stream_parser_feed(parser, input[i], (int64_t)i + 1,
                                      parsed, sizeof(parsed))) {
            memcpy(output, parsed, EG_DATA_FRAME_SIZE);
            *output_length = EG_DATA_FRAME_SIZE;
            frames++;
        }
    }
    return frames;
}

static void test_stream_parser_header_split_across_calls(void) {
    eg_e220_stream_parser_t parser;
    uint8_t frame[EG_DATA_FRAME_SIZE], output[EG_E220_MAX_FRAME_SIZE];
    size_t length = 0;
    make_frame(3, frame);
    eg_e220_stream_parser_reset(&parser);
    assert(eg_e220_stream_parser_feed(&parser, frame[0], 1, output, sizeof(output)) == 0);
    assert(parser.state == RX_WAIT_G && parser.frame_offset == 1);
    assert(eg_e220_stream_parser_feed(&parser, frame[1], 2, output, sizeof(output)) == 0);
    assert(eg_e220_stream_parser_feed(&parser, frame[2], 3, output, sizeof(output)) == 0);
    assert(eg_e220_stream_parser_feed(&parser, frame[3], 4, output, sizeof(output)) == 0);
    assert(eg_e220_stream_parser_feed(&parser, frame[4], 5, output, sizeof(output)) == 0);
    for (size_t i = 5; i < sizeof(frame); ++i)
        if (eg_e220_stream_parser_feed(&parser, frame[i], (int64_t)i + 1, output, sizeof(output))) length++;
    assert(length == 1);
    assert(memcmp(frame, output, sizeof(frame)) == 0);
}

static void test_stream_parser_body_split_across_calls(void) {
    eg_e220_stream_parser_t parser;
    uint8_t frame[EG_DATA_FRAME_SIZE], output[EG_E220_MAX_FRAME_SIZE];
    size_t output_length = 0;
    make_frame(4, frame);
    eg_e220_stream_parser_reset(&parser);
    assert(feed_bytes(&parser, frame, 9, output, &output_length) == 0);
    assert(eg_e220_stream_parser_is_reading_body(&parser));
    assert(parser.frame_offset == 9);
    assert(feed_bytes(&parser, frame + 9, 5, output, &output_length) == 0);
    assert(parser.frame_offset == 14);
    assert(feed_bytes(&parser, frame + 14, sizeof(frame) - 14, output, &output_length) == 1);
    assert(output_length == sizeof(frame) && memcmp(frame, output, sizeof(frame)) == 0);
}

static void test_stream_parser_timeout_preserves_state(void) {
    eg_e220_stream_parser_t parser;
    uint8_t frame[EG_DATA_FRAME_SIZE], output[EG_E220_MAX_FRAME_SIZE];
    size_t output_length = 0;
    make_frame(5, frame);
    eg_e220_stream_parser_reset(&parser);
    assert(feed_bytes(&parser, frame, 12, output, &output_length) == 0);
    size_t offset_before_timeout = parser.frame_offset;
    eg_rx_state_t state_before_timeout = parser.state;
    /* A receive timeout adds no byte and must not reset the persistent parser. */
    assert(parser.frame_offset == offset_before_timeout && parser.state == state_before_timeout);
    assert(feed_bytes(&parser, frame + 12, sizeof(frame) - 12, output, &output_length) == 1);
    assert(memcmp(frame, output, sizeof(frame)) == 0);
}

static void test_stream_parser_back_to_back_frames(void) {
    eg_e220_stream_parser_t parser;
    uint8_t stream[2 * EG_DATA_FRAME_SIZE], output[EG_E220_MAX_FRAME_SIZE];
    size_t output_length = 0;
    make_frame(6, stream);
    make_frame(7, stream + EG_DATA_FRAME_SIZE);
    eg_e220_stream_parser_reset(&parser);
    assert(feed_bytes(&parser, stream, sizeof(stream), output, &output_length) == 2);
    assert(parser.diagnostics.frames_completed == 2);
}

static void test_stream_parser_noise_resynchronization(void) {
    eg_e220_stream_parser_t parser;
    uint8_t frame[EG_DATA_FRAME_SIZE], stream[EG_DATA_FRAME_SIZE + 8], output[EG_E220_MAX_FRAME_SIZE];
    size_t output_length = 0;
    const uint8_t noise[] = {0x00, 'X', 'E', 'x', 0xFF, 'G', 'x', 'E'};
    make_frame(8, frame);
    memcpy(stream, noise, sizeof(noise));
    memcpy(stream + sizeof(noise), frame, sizeof(frame));
    eg_e220_stream_parser_reset(&parser);
    assert(feed_bytes(&parser, stream, sizeof(stream), output, &output_length) == 1);
    assert(memcmp(frame, output, sizeof(frame)) == 0);
}

static void test_stream_parser_crc_failure_resynchronization(void) {
    eg_e220_stream_parser_t parser;
    uint8_t bad[EG_DATA_FRAME_SIZE], good[EG_DATA_FRAME_SIZE];
    uint8_t stream[2 * EG_DATA_FRAME_SIZE], output[EG_E220_MAX_FRAME_SIZE];
    size_t output_length = 0;
    make_frame(9, bad); make_frame(10, good);
    bad[EG_DATA_FRAME_SIZE - 1] ^= 0x80;
    memcpy(stream, bad, sizeof(bad)); memcpy(stream + sizeof(bad), good, sizeof(good));
    eg_e220_stream_parser_reset(&parser);
    assert(feed_bytes(&parser, stream, sizeof(stream), output, &output_length) == 1);
    assert(parser.diagnostics.crc_failures == 1);
    assert(memcmp(good, output, sizeof(good)) == 0);
}

static void test_no_ack_does_not_drop_next_frame(void) {
    eg_e220_stream_parser_t parser;
    uint8_t first[EG_DATA_FRAME_SIZE], second[EG_DATA_FRAME_SIZE], output[EG_E220_MAX_FRAME_SIZE];
    size_t output_length = 0;
    make_frame(11, first); make_frame(12, second);
    eg_e220_stream_parser_reset(&parser);
    assert(feed_bytes(&parser, first, sizeof(first), output, &output_length) == 1);
    /* Simulate the application sending no ACK; the next E220 DATA stream is still parsed. */
    assert(feed_bytes(&parser, second, sizeof(second), output, &output_length) == 1);
    assert(memcmp(second, output, sizeof(second)) == 0);
}

int main(void) {
    test_stream_parser_header_split_across_calls();
    test_stream_parser_body_split_across_calls();
    test_stream_parser_timeout_preserves_state();
    test_stream_parser_back_to_back_frames();
    test_stream_parser_noise_resynchronization();
    test_stream_parser_crc_failure_resynchronization();
    test_no_ack_does_not_drop_next_frame();
    puts("e220_stream_parser: all tests passed");
    return 0;
}
