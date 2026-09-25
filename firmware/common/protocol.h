#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define EG_PROTOCOL_VERSION 1
#define EG_DATA_FRAME_SIZE 26
#define EG_ACK_FRAME_SIZE 13
#define EG_FRAME_DATA 1
#define EG_FRAME_ACK 2

typedef struct {
    uint8_t node_id;
    uint16_t sequence;
    uint16_t sample_id;
    uint8_t importance;
    uint8_t copy_index;
    uint8_t copy_count;
    uint32_t uptime_ms;
    int16_t temperature_centi;
    uint16_t humidity_centi;
    uint16_t light_lux;
    uint16_t soil_percent_tenths;
} eg_data_packet_t;

typedef struct {
    uint8_t node_id;
    uint16_t sequence;
    uint16_t sample_id;
    uint8_t status;
    uint8_t copy_index;
} eg_ack_packet_t;

uint16_t eg_crc16(const uint8_t *data, size_t length);
size_t eg_encode_data(const eg_data_packet_t *packet, uint8_t *out, size_t capacity);
bool eg_decode_data(const uint8_t *frame, size_t length, eg_data_packet_t *packet);
size_t eg_encode_ack(const eg_ack_packet_t *packet, uint8_t *out, size_t capacity);
bool eg_decode_ack(const uint8_t *frame, size_t length, eg_ack_packet_t *packet);
