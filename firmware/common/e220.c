#include "e220.h"

#include <string.h>
#include "driver/gpio.h"
#include "driver/uart.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "protocol.h"
#include "sdkconfig.h"

#define TAG "e220"
#define RX_RING_SIZE 2048

static const uart_port_t s_uart = (uart_port_t)CONFIG_EG_E220_UART_NUM;

static bool wait_aux(uint32_t timeout_ms) {
    if (CONFIG_EG_E220_AUX_GPIO < 0) return true;
    int64_t deadline = esp_timer_get_time() + (int64_t)timeout_ms * 1000;
    while (esp_timer_get_time() < deadline) {
        if (gpio_get_level((gpio_num_t)CONFIG_EG_E220_AUX_GPIO) == 1) return true;
        vTaskDelay(pdMS_TO_TICKS(2));
    }
    ESP_LOGW(TAG, "AUX timeout");
    return false;
}

static bool read_registers(uint8_t address, uint8_t count, uint8_t *values) {
    uint8_t request[3] = {0xC1, address, count};
    uint8_t reply[16];
    if (count + 3 > sizeof(reply)) return false;
    uart_flush_input(s_uart);
    if (uart_write_bytes(s_uart, request, sizeof(request)) != sizeof(request) ||
        uart_wait_tx_done(s_uart, pdMS_TO_TICKS(500)) != ESP_OK) return false;
    size_t received = 0;
    int64_t deadline = esp_timer_get_time() + 1200000;
    while (received < count + 3 && esp_timer_get_time() < deadline) {
        int n = uart_read_bytes(s_uart, reply + received, count + 3 - received, pdMS_TO_TICKS(100));
        if (n > 0) received += (size_t)n;
    }
    if (received != count + 3 || reply[0] != 0xC1 || reply[1] != address || reply[2] != count) return false;
    memcpy(values, reply + 3, count);
    return true;
}

static esp_err_t verify_unchanged_radio_profile(void) {
    uint8_t base[3], channel[1];
    gpio_set_level((gpio_num_t)CONFIG_EG_E220_M0_GPIO, 1);
    gpio_set_level((gpio_num_t)CONFIG_EG_E220_M1_GPIO, 1);
    vTaskDelay(pdMS_TO_TICKS(200));
    bool read_ok = wait_aux(1500) && read_registers(0x00, 3, base) && read_registers(0x04, 1, channel);
    gpio_set_level((gpio_num_t)CONFIG_EG_E220_M0_GPIO, 0);
    gpio_set_level((gpio_num_t)CONFIG_EG_E220_M1_GPIO, 0);
    vTaskDelay(pdMS_TO_TICKS(200));
    if (!read_ok || !wait_aux(1500)) {
        ESP_LOGE(TAG, "E220 configuration read failed; radio settings were not changed");
        return ESP_ERR_TIMEOUT;
    }
    uint16_t address = ((uint16_t)base[0] << 8) | base[1];
    if (address != CONFIG_EG_E220_ADDRESS || base[2] != CONFIG_EG_E220_REG0 ||
        channel[0] != CONFIG_EG_E220_CHANNEL) {
        ESP_LOGE(TAG, "E220 profile mismatch address=%04X reg0=%02X channel=%02X expected=%04X/%02X/%02X",
                 address, base[2], channel[0], CONFIG_EG_E220_ADDRESS,
                 CONFIG_EG_E220_REG0, CONFIG_EG_E220_CHANNEL);
        return ESP_ERR_INVALID_STATE;
    }
    ESP_LOGI(TAG, "E220 profile verified address=%04X reg0=%02X channel=%02X",
             address, base[2], channel[0]);
    return ESP_OK;
}

int eg_e220_init(void) {
    uart_config_t config = {.baud_rate = CONFIG_EG_E220_BAUD, .data_bits = UART_DATA_8_BITS,
                           .parity = UART_PARITY_DISABLE, .stop_bits = UART_STOP_BITS_1,
                           .flow_ctrl = UART_HW_FLOWCTRL_DISABLE, .source_clk = UART_SCLK_DEFAULT};
    esp_err_t err = uart_driver_install(s_uart, RX_RING_SIZE, RX_RING_SIZE, 0, NULL, 0);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) return err;
    if ((err = uart_param_config(s_uart, &config)) != ESP_OK) return err;
    if ((err = uart_set_pin(s_uart, CONFIG_EG_E220_TX_GPIO, CONFIG_EG_E220_RX_GPIO, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE)) != ESP_OK) return err;
    gpio_config_t mode = {.pin_bit_mask = (1ULL << CONFIG_EG_E220_M0_GPIO) | (1ULL << CONFIG_EG_E220_M1_GPIO),
                          .mode = GPIO_MODE_OUTPUT, .pull_up_en = GPIO_PULLUP_DISABLE,
                          .pull_down_en = GPIO_PULLDOWN_DISABLE, .intr_type = GPIO_INTR_DISABLE};
    if ((err = gpio_config(&mode)) != ESP_OK) return err;
    if ((err = gpio_set_level((gpio_num_t)CONFIG_EG_E220_M0_GPIO, 0)) != ESP_OK ||
        (err = gpio_set_level((gpio_num_t)CONFIG_EG_E220_M1_GPIO, 0)) != ESP_OK) return err;
    if (CONFIG_EG_E220_AUX_GPIO >= 0) {
#if CONFIG_EG_E220_AUX_GPIO >= 0
        gpio_config_t io = {.pin_bit_mask = 1ULL << CONFIG_EG_E220_AUX_GPIO,
                            .mode = GPIO_MODE_INPUT, .pull_up_en = GPIO_PULLUP_DISABLE,
                            .pull_down_en = GPIO_PULLDOWN_DISABLE, .intr_type = GPIO_INTR_DISABLE};
        if ((err = gpio_config(&io)) != ESP_OK) return err;
#endif
    }
    ESP_LOGI(TAG, "UART%d TX=%d RX=%d AUX=%d baud=%d", CONFIG_EG_E220_UART_NUM,
             CONFIG_EG_E220_TX_GPIO, CONFIG_EG_E220_RX_GPIO, CONFIG_EG_E220_AUX_GPIO, CONFIG_EG_E220_BAUD);
    vTaskDelay(pdMS_TO_TICKS(200));
    return verify_unchanged_radio_profile();
}

int eg_e220_send(const uint8_t *data, size_t length, uint32_t timeout_ms) {
    if (!data || !length) return ESP_ERR_INVALID_ARG;
    if (!wait_aux(timeout_ms)) return ESP_ERR_TIMEOUT;
    int written = uart_write_bytes(s_uart, data, length);
    if (written != (int)length) return ESP_FAIL;
    esp_err_t err = uart_wait_tx_done(s_uart, pdMS_TO_TICKS(timeout_ms));
    if (err != ESP_OK) return err;
    return wait_aux(timeout_ms) ? ESP_OK : ESP_ERR_TIMEOUT;
}

static int read_byte(uint8_t *byte, int64_t deadline_us) {
    while (esp_timer_get_time() < deadline_us) {
        int count = uart_read_bytes(s_uart, byte, 1, pdMS_TO_TICKS(10));
        if (count == 1) return 1;
    }
    return 0;
}

int eg_e220_receive(uint8_t *frame, size_t capacity, uint32_t timeout_ms) {
    if (!frame || capacity < EG_ACK_FRAME_SIZE) return -1;
    int64_t deadline = esp_timer_get_time() + (int64_t)timeout_ms * 1000;
    int state = 0;
    uint8_t byte = 0;
    while (read_byte(&byte, deadline)) {
        if (state == 0) { state = byte == 'E' ? 1 : 0; continue; }
        if (state == 1) {
            if (byte == 'G') { frame[0] = 'E'; frame[1] = 'G'; state = 2; }
            else state = byte == 'E' ? 1 : 0;
            continue;
        }
        frame[2] = byte;
        if (!read_byte(&frame[3], deadline)) return 0;
        size_t length = frame[3] == 1 ? EG_DATA_FRAME_SIZE : (frame[3] == 2 ? EG_ACK_FRAME_SIZE : 0);
        if (!length || capacity < length) { state = 0; continue; }
        frame[4] = 0;
        int got = uart_read_bytes(s_uart, frame + 4, length - 4, pdMS_TO_TICKS(timeout_ms));
        if (got != (int)(length - 4)) return -1;
        return (int)length;
    }
    return 0;
}
