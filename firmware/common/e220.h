#pragma once

#include <stddef.h>
#include <stdint.h>

int eg_e220_init(void);
int eg_e220_send(const uint8_t *data, size_t length, uint32_t timeout_ms);
/* 1 frame received, 0 timeout, negative on UART/frame transport failure. */
int eg_e220_receive(uint8_t *frame, size_t capacity, uint32_t timeout_ms);
