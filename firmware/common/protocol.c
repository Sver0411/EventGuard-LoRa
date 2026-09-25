#include "protocol.h"

static void put16(uint8_t *p, uint16_t value) { p[0] = (uint8_t)value; p[1] = (uint8_t)(value >> 8); }
static void put32(uint8_t *p, uint32_t value) { put16(p, (uint16_t)value); put16(p + 2, (uint16_t)(value >> 16)); }
static uint16_t get16(const uint8_t *p) { return (uint16_t)p[0] | ((uint16_t)p[1] << 8); }
static uint32_t get32(const uint8_t *p) { return (uint32_t)get16(p) | ((uint32_t)get16(p + 2) << 16); }

uint16_t eg_crc16(const uint8_t *data, size_t length) {
    uint16_t crc = 0xFFFF;
    for (size_t i = 0; i < length; ++i) {
        crc ^= (uint16_t)data[i] << 8;
        for (int bit = 0; bit < 8; ++bit) crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
    }
    return crc;
}

static void begin_frame(uint8_t *out, uint8_t type, uint8_t node) {
    out[0] = 'E'; out[1] = 'G'; out[2] = EG_PROTOCOL_VERSION; out[3] = type; out[4] = node;
}

size_t eg_encode_data(const eg_data_packet_t *p, uint8_t *out, size_t capacity) {
    if (!p || !out || capacity < EG_DATA_FRAME_SIZE) return 0;
    begin_frame(out, EG_FRAME_DATA, p->node_id);
    put16(out + 5, p->sequence); put16(out + 7, p->sample_id);
    out[9] = p->importance; out[10] = p->copy_index; out[11] = p->copy_count;
    put32(out + 12, p->uptime_ms); put16(out + 16, (uint16_t)p->temperature_centi);
    put16(out + 18, p->humidity_centi); put16(out + 20, p->light_lux); put16(out + 22, p->soil_percent_tenths);
    put16(out + 24, eg_crc16(out, 24));
    return EG_DATA_FRAME_SIZE;
}

bool eg_decode_data(const uint8_t *f, size_t length, eg_data_packet_t *p) {
    if (!f || !p || length != EG_DATA_FRAME_SIZE || f[0] != 'E' || f[1] != 'G' || f[2] != EG_PROTOCOL_VERSION || f[3] != EG_FRAME_DATA || get16(f + 24) != eg_crc16(f, 24)) return false;
    p->node_id = f[4]; p->sequence = get16(f + 5); p->sample_id = get16(f + 7);
    p->importance = f[9]; p->copy_index = f[10]; p->copy_count = f[11]; p->uptime_ms = get32(f + 12);
    p->temperature_centi = (int16_t)get16(f + 16); p->humidity_centi = get16(f + 18);
    p->light_lux = get16(f + 20); p->soil_percent_tenths = get16(f + 22);
    return true;
}

size_t eg_encode_ack(const eg_ack_packet_t *p, uint8_t *out, size_t capacity) {
    if (!p || !out || capacity < EG_ACK_FRAME_SIZE) return 0;
    begin_frame(out, EG_FRAME_ACK, p->node_id);
    put16(out + 5, p->sequence); put16(out + 7, p->sample_id);
    out[9] = p->status; out[10] = p->copy_index;
    put16(out + 11, eg_crc16(out, 11));
    return EG_ACK_FRAME_SIZE;
}

bool eg_decode_ack(const uint8_t *f, size_t length, eg_ack_packet_t *p) {
    if (!f || !p || length != EG_ACK_FRAME_SIZE || f[0] != 'E' || f[1] != 'G' || f[2] != EG_PROTOCOL_VERSION || f[3] != EG_FRAME_ACK || get16(f + 11) != eg_crc16(f, 11)) return false;
    p->node_id = f[4]; p->sequence = get16(f + 5); p->sample_id = get16(f + 7); p->status = f[9]; p->copy_index = f[10];
    return true;
}
