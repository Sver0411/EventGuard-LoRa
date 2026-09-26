#include "e220.h"

#include <string.h>
#include <stdio.h>
#include "driver/gpio.h"
#include "driver/uart.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "e220_stream_parser.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "protocol.h"
#include "sdkconfig.h"

#define TAG "e220"
#define RX_RING_SIZE 2048

static const uart_port_t s_uart = (uart_port_t)CONFIG_EG_E220_UART_NUM;
static eg_e220_stream_parser_t s_parser;
static eg_e220_diagnostics_t s_diag;
static eg_e220_tx_timing_t s_last_tx_timing;
static SemaphoreHandle_t s_receive_mutex;
static portMUX_TYPE s_diag_mux = portMUX_INITIALIZER_UNLOCKED;

#if CONFIG_EG_DIAGNOSTIC_MODE && CONFIG_EG_E220_AUX_GPIO >= 0
#define AUX_EDGE_CAPACITY 256
typedef struct { int64_t timestamp_us; uint8_t level; } aux_edge_t;
static aux_edge_t s_aux_edges[AUX_EDGE_CAPACITY];
static volatile uint16_t s_aux_head, s_aux_tail;
static volatile uint64_t s_aux_overflow;
static TaskHandle_t s_aux_log_task_handle;
static portMUX_TYPE s_aux_mux = portMUX_INITIALIZER_UNLOCKED;

static void aux_edge_isr(void *unused) {
    (void)unused;
    BaseType_t task_woken = pdFALSE;
    aux_edge_t edge = {.timestamp_us = esp_timer_get_time(),
                       .level = (uint8_t)gpio_get_level((gpio_num_t)CONFIG_EG_E220_AUX_GPIO)};
    portENTER_CRITICAL_ISR(&s_diag_mux);
    s_diag.aux_interrupts++;
    portEXIT_CRITICAL_ISR(&s_diag_mux);
    portENTER_CRITICAL_ISR(&s_aux_mux);
    uint16_t next = (uint16_t)((s_aux_head + 1) % AUX_EDGE_CAPACITY);
    if (next == s_aux_tail) s_aux_overflow++;
    else { s_aux_edges[s_aux_head] = edge; s_aux_head = next; }
    portEXIT_CRITICAL_ISR(&s_aux_mux);
    if (s_aux_log_task_handle) vTaskNotifyGiveFromISR(s_aux_log_task_handle, &task_woken);
    if (task_woken) portYIELD_FROM_ISR();
}

static void aux_log_task(void *unused) {
    (void)unused;
    for (;;) {
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(500));
        for (;;) {
            aux_edge_t edge;
            portENTER_CRITICAL(&s_aux_mux);
            bool available = s_aux_tail != s_aux_head;
            if (available) { edge = s_aux_edges[s_aux_tail]; s_aux_tail = (uint16_t)((s_aux_tail + 1) % AUX_EDGE_CAPACITY); }
            uint64_t overflow = s_aux_overflow;
            s_aux_overflow = 0;
            portEXIT_CRITICAL(&s_aux_mux);
            if (!available) {
                if (overflow) printf("AUX_OVERFLOW,%llu\n", (unsigned long long)overflow);
                break;
            }
            printf("AUX,%lld,%u\n", (long long)edge.timestamp_us, (unsigned)edge.level);
        }
    }
}
#endif

static void update_buffered_peak(void) {
    size_t buffered = 0;
    if (uart_get_buffered_data_len(s_uart, &buffered) == ESP_OK) {
        portENTER_CRITICAL(&s_diag_mux);
        if (buffered > s_diag.rx_buffered_bytes_peak) s_diag.rx_buffered_bytes_peak = buffered;
        portEXIT_CRITICAL(&s_diag_mux);
    }
}

#if CONFIG_EG_DIAGNOSTIC_MODE
static void log_rx_buffer_state(const char *event, size_t buffered) {
    printf("UART_BUFFER,%s,%lld,%u,%u,%u,%u\n", event,
        (long long)esp_timer_get_time(), (unsigned)buffered, (unsigned)s_parser.state,
        (unsigned)s_parser.frame_offset, (unsigned)s_parser.expected_length);
}
#endif

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
    s_receive_mutex = xSemaphoreCreateMutex();
    if (!s_receive_mutex) return ESP_ERR_NO_MEM;
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
                            .pull_down_en = GPIO_PULLDOWN_DISABLE,
#if CONFIG_EG_DIAGNOSTIC_MODE
                            .intr_type = GPIO_INTR_ANYEDGE};
#else
                            .intr_type = GPIO_INTR_DISABLE};
#endif
        if ((err = gpio_config(&io)) != ESP_OK) return err;
#if CONFIG_EG_DIAGNOSTIC_MODE
        err = gpio_install_isr_service(0);
        if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) return err;
        if ((err = gpio_isr_handler_add((gpio_num_t)CONFIG_EG_E220_AUX_GPIO, aux_edge_isr, NULL)) != ESP_OK) return err;
        if (xTaskCreate(aux_log_task, "eg_aux_log", 3072, NULL, 2, &s_aux_log_task_handle) != pdPASS) return ESP_ERR_NO_MEM;
#endif
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
    memset(&s_last_tx_timing, 0, sizeof(s_last_tx_timing));
    int written = uart_write_bytes(s_uart, data, length);
    if (written != (int)length) return ESP_FAIL;
    esp_err_t err = uart_wait_tx_done(s_uart, pdMS_TO_TICKS(timeout_ms));
    if (err != ESP_OK) return err;
    s_last_tx_timing.uart_tx_done_timestamp_us = esp_timer_get_time();
    if (!wait_aux(timeout_ms)) return ESP_ERR_TIMEOUT;
    s_last_tx_timing.aux_ready_timestamp_us = esp_timer_get_time();
    return ESP_OK;
}

int eg_e220_receive(uint8_t *frame, size_t capacity, uint32_t timeout_ms) {
    if (!frame || capacity < EG_ACK_FRAME_SIZE) return -1;
    if (!s_receive_mutex || xSemaphoreTake(s_receive_mutex, portMAX_DELAY) != pdTRUE) return -1;
    int64_t deadline = esp_timer_get_time() + (int64_t)timeout_ms * 1000;
    int result = 0;
    while (esp_timer_get_time() < deadline) {
        size_t target = eg_e220_stream_parser_read_target(&s_parser);
        if (target > EG_E220_MAX_FRAME_SIZE) target = EG_E220_MAX_FRAME_SIZE;
        uint8_t chunk[EG_E220_MAX_FRAME_SIZE];
        update_buffered_peak();
        int64_t remaining_us = deadline - esp_timer_get_time();
        if (remaining_us <= 0) break;
        uint32_t wait_ms = (uint32_t)((remaining_us + 999) / 1000);
        if (wait_ms > 20) wait_ms = 20;
        if (wait_ms == 0) wait_ms = 1;
        int count = uart_read_bytes(s_uart, chunk, target, pdMS_TO_TICKS(wait_ms));
        if (count < 0) { result = -1; break; }
        if (count == 0) continue;
        portENTER_CRITICAL(&s_diag_mux);
        s_diag.uart_bytes_received += (uint64_t)count;
        if ((size_t)count < target) s_diag.short_reads++;
        portEXIT_CRITICAL(&s_diag_mux);
        for (int i = 0; i < count; ++i) {
            int64_t first_byte_us = s_parser.frame_start_timestamp_us;
            int64_t header_complete_us = s_parser.header_complete_timestamp_us;
            int parsed = eg_e220_stream_parser_feed(&s_parser, chunk[i], esp_timer_get_time(), frame, capacity);
            if (parsed == 1) {
                update_buffered_peak();
#if CONFIG_EG_DIAGNOSTIC_MODE && !CONFIG_EG_ROLE_SENSOR
                if (frame[2] == EG_PROTOCOL_VERSION && frame[3] == EG_FRAME_DATA) {
                    uint16_t sequence = (uint16_t)frame[5] | ((uint16_t)frame[6] << 8);
                    uint16_t sample_id = (uint16_t)frame[7] | ((uint16_t)frame[8] << 8);
                    int64_t complete_us = esp_timer_get_time();
                    printf("D_RX_FIRST_BYTE,%u,%u,%u,%lld\n", sample_id, frame[10], sequence, (long long)first_byte_us);
                    printf("D_RX_HEADER_COMPLETE,%u,%u,%u,%lld\n", sample_id, frame[10], sequence, (long long)header_complete_us);
                    printf("D_RX_FRAME_COMPLETE,%u,%u,%u,%lld\n", sample_id, frame[10], sequence, (long long)complete_us);
                    printf("D_RX_CRC_OK,%u,%u,%u,%lld\n", sample_id, frame[10], sequence, (long long)complete_us);
                }
#else
                (void)first_byte_us;
                (void)header_complete_us;
#endif
#if CONFIG_EG_DIAGNOSTIC_MODE
                size_t buffered = 0;
                (void)uart_get_buffered_data_len(s_uart, &buffered);
                log_rx_buffer_state("FRAME_COMPLETE", buffered);
#endif
                result = frame[3] == EG_FRAME_DATA ? EG_DATA_FRAME_SIZE : EG_ACK_FRAME_SIZE;
                goto receive_done;
            }
        }
    }
    update_buffered_peak();
    size_t buffered = 0;
    (void)uart_get_buffered_data_len(s_uart, &buffered);
#if CONFIG_EG_DIAGNOSTIC_MODE
    if (eg_e220_stream_parser_has_partial_frame(&s_parser) || buffered)
        log_rx_buffer_state("TIMEOUT", buffered);
#endif
    if (result == 0 && eg_e220_stream_parser_has_partial_frame(&s_parser)) {
        portENTER_CRITICAL(&s_diag_mux);
        if (eg_e220_stream_parser_is_reading_body(&s_parser)) s_diag.partial_body_timeouts++;
        else s_diag.partial_header_timeouts++;
        portEXIT_CRITICAL(&s_diag_mux);
    }
receive_done:
    xSemaphoreGive(s_receive_mutex);
    return result;
}

void eg_e220_get_diagnostics(eg_e220_diagnostics_t *out) {
    if (!out) return;
    if (s_receive_mutex) xSemaphoreTake(s_receive_mutex, portMAX_DELAY);
    portENTER_CRITICAL(&s_diag_mux);
    *out = s_diag;
    portEXIT_CRITICAL(&s_diag_mux);
    out->frames_started = s_parser.diagnostics.frames_started;
    out->frames_completed = s_parser.diagnostics.frames_completed;
    out->parser_resyncs = s_parser.diagnostics.parser_resyncs;
    out->crc_failures = s_parser.diagnostics.crc_failures;
    out->invalid_type = s_parser.diagnostics.invalid_type;
    out->invalid_version = s_parser.diagnostics.invalid_version;
#if CONFIG_EG_DIAGNOSTIC_MODE && CONFIG_EG_E220_AUX_GPIO >= 0
    portENTER_CRITICAL(&s_aux_mux);
    out->aux_ring_overflow = s_aux_overflow;
    portEXIT_CRITICAL(&s_aux_mux);
#endif
    if (s_receive_mutex) xSemaphoreGive(s_receive_mutex);
}

void eg_e220_reset_diagnostics(void) {
    if (s_receive_mutex) xSemaphoreTake(s_receive_mutex, portMAX_DELAY);
    portENTER_CRITICAL(&s_diag_mux);
    memset(&s_diag, 0, sizeof(s_diag));
    portEXIT_CRITICAL(&s_diag_mux);
    memset(&s_last_tx_timing, 0, sizeof(s_last_tx_timing));
    eg_e220_stream_parser_reset(&s_parser);
#if CONFIG_EG_DIAGNOSTIC_MODE && CONFIG_EG_E220_AUX_GPIO >= 0
    portENTER_CRITICAL(&s_aux_mux);
    s_aux_head = s_aux_tail = 0;
    s_aux_overflow = 0;
    portEXIT_CRITICAL(&s_aux_mux);
#endif
    if (s_receive_mutex) xSemaphoreGive(s_receive_mutex);
}

eg_e220_tx_timing_t eg_e220_get_last_tx_timing(void) { return s_last_tx_timing; }
