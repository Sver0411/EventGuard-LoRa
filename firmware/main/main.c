#include <math.h>
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>

#include "driver/gpio.h"
#include "e220.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_timer.h"
#include "faults.h"
#include "importance.h"
#include "protocol.h"
#include "sensor_drivers.h"
#include "strategy.h"
#include "sdkconfig.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define TAG "eventguard"
#define CONSOLE_LINE_MAX 256
#define MAX_TRACE EG_MAX_TRACE

typedef enum { TRUTH_NORMAL = 0, TRUTH_IMPORTANT = 1, TRUTH_CRITICAL = 2 } truth_t;

typedef struct {
    uint16_t id;
    uint32_t timestamp_ms;
    float values[4];
    uint8_t truth;
} trace_item_t;

typedef struct {
    eg_strategy_t strategy;
    double loss_rate;
    eg_fault_model_t loss_model;
    uint32_t seed;
    uint8_t burst_length;
    uint8_t fixed_redundancy;
    uint8_t max_redundancy;
    uint8_t link_window;
    double link_degraded_threshold;
    double link_bad_threshold;
    uint8_t link_bad_fail_streak;
    eg_importance_config_t importance;
    uint16_t ack_timeout_ms;
    uint32_t data_copy_budget;
} run_config_t;

static run_config_t s_config = {.strategy = EG_EVENTGUARD, .loss_rate = 0.0f, .loss_model = EG_RANDOM_COPY,
    .seed = 11, .burst_length = 3, .fixed_redundancy = 2, .max_redundancy = 3, .link_window = 12,
    .link_degraded_threshold = 0.80, .link_bad_threshold = 0.50, .link_bad_fail_streak = 3,
    .importance = {.important_threshold = 0.85f, .critical_threshold = 3.0f,
                   .scales = {0.5f, 5.0f, 100.0f, 8.0f}, .baseline_alpha = 0.06f},
    .ack_timeout_ms = CONFIG_EG_ACK_TIMEOUT_MS};
static eg_fault_table_t s_fault_table;
static char s_mode[20] = "TRACE_MODE";
static bool s_e220_ready;
static esp_err_t s_e220_error = ESP_FAIL;

static void print_e220_diagnostics(void) {
    eg_e220_diagnostics_t d;
    eg_e220_get_diagnostics(&d);
    printf("UART_DIAG,uart_bytes_received,%llu,frames_started,%llu,frames_completed,%llu,partial_header_timeouts,%llu,partial_body_timeouts,%llu,short_reads,%llu,parser_resyncs,%llu,crc_failures,%llu,invalid_type,%llu,invalid_version,%llu,rx_buffered_bytes_peak,%u,aux_interrupts,%llu,aux_ring_overflow,%llu\n",
        (unsigned long long)d.uart_bytes_received, (unsigned long long)d.frames_started,
        (unsigned long long)d.frames_completed, (unsigned long long)d.partial_header_timeouts,
        (unsigned long long)d.partial_body_timeouts, (unsigned long long)d.short_reads,
        (unsigned long long)d.parser_resyncs, (unsigned long long)d.crc_failures,
        (unsigned long long)d.invalid_type, (unsigned long long)d.invalid_version,
        (unsigned)d.rx_buffered_bytes_peak, (unsigned long long)d.aux_interrupts,
        (unsigned long long)d.aux_ring_overflow);
}

#if CONFIG_EG_ROLE_SENSOR
static trace_item_t s_trace[MAX_TRACE];
static size_t s_trace_expected;
static size_t s_trace_loaded;
static uint16_t s_next_sequence;
static uint32_t s_sensor_tx_count;
static uint32_t s_sensor_ack_count;
static eg_importance_state_t s_classifier;
static eg_link_estimator_t s_link;
static uint8_t s_budget_allocation[MAX_TRACE];
static volatile bool s_stop_requested;
static volatile bool s_run_active;
#if CONFIG_EG_DIAGNOSTIC_MODE
static volatile bool s_rx_diag_stop_requested;
static char s_rx_diag_test;
#endif

static const char *importance_name(uint8_t importance) {
    return importance == 2 ? "CRITICAL" : (importance == 1 ? "IMPORTANT" : "NORMAL");
}
static const char *truth_name(uint8_t truth) {
    return truth == 2 ? "CRITICAL" : (truth == 1 ? "IMPORTANT" : "NORMAL");
}
static const char *link_name(eg_link_state_t link) {
    return link == EG_LINK_BAD ? "BAD" : (link == EG_LINK_DEGRADED ? "DEGRADED" : "GOOD");
}

static void reset_sensor_run(void) {
    s_next_sequence = 0; s_sensor_tx_count = 0; s_sensor_ack_count = 0;
    memset(&s_classifier, 0, sizeof(s_classifier));
    eg_link_init(&s_link, s_config.link_window, s_config.link_degraded_threshold,
                 s_config.link_bad_threshold, s_config.link_bad_fail_streak);
    eg_e220_reset_diagnostics();
}

static void run_trace(void) {
    reset_sensor_run();
    bool budget_mode = s_config.strategy == EG_UNIFORM_BUDGET || s_config.strategy == EG_RANDOM_BUDGET;
    if (budget_mode && !eg_budget_allocate(s_config.strategy, s_trace_loaded, s_config.data_copy_budget,
                                            s_config.seed, s_config.max_redundancy, s_budget_allocation)) {
        printf("ERR,BUDGET_VALUE\n"); return;
    }
    size_t completed = 0;
    for (size_t index = 0; index < s_trace_loaded; ++index) {
        if (s_stop_requested) break;
        trace_item_t *sample = &s_trace[index];
        eg_observation_t observation = {.timestamp_ms = sample->timestamp_ms};
        memcpy(observation.values, sample->values, sizeof(observation.values));
        float score = 0.0f;
        uint8_t importance = eg_classify(&s_classifier, &observation, &s_config.importance, &score);
        eg_link_state_t current_link = eg_link_state(&s_link);
        uint8_t copies = budget_mode ? s_budget_allocation[index] : eg_choose_redundancy(
            s_config.strategy, importance, current_link, s_config.fixed_redundancy, s_config.max_redundancy);
        uint16_t sequence = s_next_sequence++;
        uint64_t sample_start = esp_timer_get_time();
        uint8_t sent = 0;
        bool ack_success = false;
        printf("EVT,%u,%u,%s,%.4f,%s,%u\n", sample->id, sample->truth, truth_name(sample->truth), score, link_name(current_link), copies);
        for (uint8_t copy = 0; copy < copies; ++copy) {
            if (s_stop_requested) break;
            eg_data_packet_t packet = {.node_id = CONFIG_EG_NODE_ID, .sequence = sequence,
                .sample_id = sample->id, .importance = importance, .copy_index = copy, .copy_count = copies,
                .uptime_ms = (uint32_t)(esp_timer_get_time() / 1000),
                .temperature_centi = (int16_t)lroundf(sample->values[0] * 100.0f),
                .humidity_centi = (uint16_t)lroundf(fmaxf(0, fminf(100, sample->values[1])) * 100.0f),
                .light_lux = (uint16_t)lroundf(fmaxf(0, fminf(65535, sample->values[2]))),
                .soil_percent_tenths = (uint16_t)lroundf(fmaxf(0, fminf(100, sample->values[3])) * 10.0f)};
            uint8_t frame[EG_DATA_FRAME_SIZE];
            size_t frame_len = eg_encode_data(&packet, frame, sizeof(frame));
            printf("TX_BEGIN,%u,%u,%u,%u\n", sample->id, sequence, copy, copies); fflush(stdout);
#if CONFIG_EG_DIAGNOSTIC_MODE
            printf("D_TX_BEGIN,%u,%u,%u,%lld\n", sample->id, copy, sequence,
                   (long long)esp_timer_get_time());
#endif
            int err = eg_e220_send(frame, frame_len, 1000);
            if (err != ESP_OK) { printf("ERR,UART_SEND,%u,%u,%d\n", sequence, copy, err); eg_link_observe_copy(&s_link, false, copy == 0); continue; }
#if CONFIG_EG_DIAGNOSTIC_MODE
            eg_e220_tx_timing_t tx_timing = eg_e220_get_last_tx_timing();
            printf("D_TX_UART_DONE,%u,%u,%u,%lld\n", sample->id, copy, sequence,
                   (long long)tx_timing.uart_tx_done_timestamp_us);
            printf("D_TX_AUX_READY,%u,%u,%u,%lld\n", sample->id, copy, sequence,
                   (long long)tx_timing.aux_ready_timestamp_us);
#endif
            sent++; s_sensor_tx_count++;
            printf("TX,%u,%u,%u,%u,%u\n", sample->id, sequence, copy, copies, (unsigned)frame_len);
            uint8_t ack_frame[EG_DATA_FRAME_SIZE];
#if CONFIG_EG_DIAGNOSTIC_MODE
            printf("D_ACK_WAIT_BEGIN,%u,%u,%u,%lld\n", sample->id, copy, sequence,
                   (long long)esp_timer_get_time());
#endif
            int received = eg_e220_receive(ack_frame, sizeof(ack_frame), s_config.ack_timeout_ms);
#if CONFIG_EG_DIAGNOSTIC_MODE
            if (received == 0)
                printf("D_ACK_TIMEOUT,%u,%u,%u,%lld\n", sample->id, copy, sequence,
                       (long long)esp_timer_get_time());
#endif
            if (received == 0) { printf("TIMEOUT,%u,%u\n", sequence, copy); eg_link_observe_copy(&s_link, false, copy == 0); continue; }
            if (received < 0) { printf("ERR,UART_RX,%u,%u\n", sequence, copy); eg_link_observe_copy(&s_link, false, copy == 0); continue; }
#if CONFIG_EG_DIAGNOSTIC_MODE
            printf("D_ACK_RECEIVED,%u,%u,%u,%lld\n", sample->id, copy, sequence,
                   (long long)esp_timer_get_time());
#endif
            eg_ack_packet_t ack;
            if (!eg_decode_ack(ack_frame, received, &ack)) { printf("ERR,CRC,%u,%u\n", sequence, copy); eg_link_observe_copy(&s_link, false, copy == 0); continue; }
            if (ack.node_id != CONFIG_EG_NODE_ID || ack.sequence != sequence || ack.sample_id != sample->id) {
                printf("ERR,ACK_MISMATCH,%u,%u\n", sequence, copy); eg_link_observe_copy(&s_link, false, copy == 0); continue;
            }
            if (eg_fault_drop(&s_fault_table, true, sample->id, copy)) {
                printf("ACK,%u,%u,%u,DROP\n", sequence, sample->id, copy);
                eg_link_observe_copy(&s_link, false, copy == 0);
                continue;
            }
            printf("ACK,%u,%u,%u,OK\n", sequence, sample->id, copy);
            s_sensor_ack_count++; ack_success = true;
            eg_link_observe_copy(&s_link, true, copy == 0);
        }
        float latency_ms = (float)(esp_timer_get_time() - sample_start) / 1000.0f;
        printf("SAMPLE,%u,%s,%s,%.4f,%s,%u,%u,%u,%.3f\n", sample->id, truth_name(sample->truth), importance_name(importance), score,
               link_name(current_link), copies, sent, ack_success ? 1 : 0, latency_ms);
        completed++;
    }
    printf("COPY_STATS,%lu,%lu,%lu,%lu\n", (unsigned long)s_link.copy_attempts,
           (unsigned long)s_link.copy_ack_success, (unsigned long)s_link.copy_failures,
           (unsigned long)s_link.consecutive_copy_failures);
    printf("END,%u,%u,%u,%u\n", (unsigned)completed, (unsigned)s_sensor_tx_count, (unsigned)s_sensor_ack_count, s_stop_requested ? 1 : 0);
    print_e220_diagnostics();
}

static void run_real_demo(void) {
    esp_err_t err = eg_sensors_init();
    if (err != ESP_OK) { printf("ERR,SENSOR_INIT,%s\n", esp_err_to_name(err)); return; }
    trace_item_t local[30];
    uint64_t begin = esp_timer_get_time();
    for (size_t i = 0; i < 30; ++i) {
        eg_sensor_values_t values;
        err = eg_sensors_read(&values);
        if (err != ESP_OK) { printf("ERR,SENSOR_READ,%s\n", esp_err_to_name(err)); break; }
        local[i] = (trace_item_t){.id = (uint16_t)i, .timestamp_ms = (uint32_t)(esp_timer_get_time() / 1000),
            .values = {values.temperature, values.humidity, values.light, values.soil_moisture}, .truth = TRUTH_NORMAL};
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
    size_t old_count = s_trace_loaded;
    memcpy(s_trace, local, sizeof(local)); s_trace_loaded = 30;
    printf("REAL_SENSOR_DEMO,%u\n", (unsigned)((esp_timer_get_time() - begin) / 1000));
    run_trace();
    s_trace_loaded = old_count;
}

#if CONFIG_EG_DIAGNOSTIC_MODE
static void e220_rx_diagnostic_task(void *parameter) {
    unsigned frames = ((unsigned *)parameter)[0];
    unsigned period_ms = ((unsigned *)parameter)[1];
    unsigned ack_timeout_ms = ((unsigned *)parameter)[2];
    free(parameter);
    uint32_t tx_count = 0, ack_count = 0, timeout_count = 0, tx_errors = 0, rx_errors = 0;
    TickType_t next_wake = xTaskGetTickCount();
    eg_e220_reset_diagnostics();
    for (unsigned seq = 0; seq < frames && !s_rx_diag_stop_requested; ++seq) {
        uint64_t tx_begin = (uint64_t)esp_timer_get_time();
        eg_data_packet_t packet = {.node_id = CONFIG_EG_NODE_ID, .sequence = (uint16_t)seq,
            .sample_id = (uint16_t)seq, .importance = 0, .copy_index = 0, .copy_count = 1,
            .uptime_ms = (uint32_t)(tx_begin / 1000), .temperature_centi = 2200,
            .humidity_centi = 5000, .light_lux = 120, .soil_percent_tenths = 450};
        uint8_t data[EG_DATA_FRAME_SIZE];
        size_t data_len = eg_encode_data(&packet, data, sizeof(data));
        printf("D_TX_BEGIN,%u,0,%u,%llu\n", seq, seq, (unsigned long long)tx_begin);
        int send_result = eg_e220_send(data, data_len, 1000);
        if (send_result != ESP_OK) {
            tx_errors++;
            printf("ERR,DIAG_UART_TX,%u,%d\n", seq, send_result);
        } else {
            eg_e220_tx_timing_t timing = eg_e220_get_last_tx_timing();
            tx_count++;
            printf("D_TX_UART_DONE,%u,0,%u,%lld\n", seq, seq, (long long)timing.uart_tx_done_timestamp_us);
            printf("D_TX_AUX_READY,%u,0,%u,%lld\n", seq, seq, (long long)timing.aux_ready_timestamp_us);
            printf("DIAG_TX_DONE,%u,%lld\n", seq, (long long)esp_timer_get_time());
            printf("D_ACK_WAIT_BEGIN,%u,0,%u,%lld\n", seq, seq, (long long)esp_timer_get_time());
            uint8_t ack_frame[EG_E220_MAX_FRAME_SIZE];
            int received = eg_e220_receive(ack_frame, sizeof(ack_frame), ack_timeout_ms);
            if (received == 0) {
                timeout_count++;
                printf("D_ACK_TIMEOUT,%u,0,%u,%lld\n", seq, seq, (long long)esp_timer_get_time());
            } else if (received < 0) {
                rx_errors++;
                printf("ERR,DIAG_UART_RX,%u\n", seq);
            } else {
                printf("D_ACK_RECEIVED,%u,0,%u,%lld\n", seq, seq, (long long)esp_timer_get_time());
                eg_ack_packet_t ack;
                if (eg_decode_ack(ack_frame, (size_t)received, &ack) && ack.sequence == seq &&
                    ack.sample_id == seq && ack.node_id == CONFIG_EG_NODE_ID) {
                    ack_count++;
                    printf("DIAG_ACK,%u,OK\n", seq);
                } else {
                    rx_errors++;
                    printf("DIAG_ACK,%u,INVALID\n", seq);
                }
            }
        }
        vTaskDelayUntil(&next_wake, pdMS_TO_TICKS(period_ms));
    }
    printf("DIAG_END,%u,%u,%u,%u,%u,%u\n", frames, (unsigned)tx_count, (unsigned)ack_count,
           (unsigned)timeout_count, (unsigned)tx_errors, (unsigned)rx_errors);
    print_e220_diagnostics();
    s_rx_diag_stop_requested = false;
    s_run_active = false;
    vTaskDelete(NULL);
}
#endif

#else
typedef struct { uint8_t node_id; uint16_t sequence; uint16_t sample_id; } seen_packet_t;
static seen_packet_t s_seen[MAX_TRACE];
static size_t s_seen_count;
static size_t s_gateway_expected;
static bool s_armed;
static uint32_t s_rx_count, s_delivery_count, s_duplicate_count, s_ack_tx_count, s_crc_errors, s_drop_count;
static uint32_t s_sequence_gaps, s_out_of_order;
static uint16_t s_last_sequence[256];
static bool s_have_sequence[256];
static volatile bool s_rx_pause_requested;
#if CONFIG_EG_DIAGNOSTIC_MODE
static volatile bool s_rx_diag_mode;
static volatile char s_rx_diag_test;
static volatile uint16_t s_rx_diag_poll_timeout_ms = 250;
#endif

static bool seen_before(uint8_t node, uint16_t sequence) {
    for (size_t i = 0; i < s_seen_count; ++i) if (s_seen[i].node_id == node && s_seen[i].sequence == sequence) return true;
    if (s_seen_count < MAX_TRACE) s_seen[s_seen_count++] = (seen_packet_t){node, sequence, 0};
    return false;
}

static void clear_gateway_run(void) {
    s_rx_pause_requested = true;
    memset(s_seen, 0, sizeof(s_seen)); s_seen_count = 0;
    memset(s_last_sequence, 0, sizeof(s_last_sequence)); memset(s_have_sequence, 0, sizeof(s_have_sequence));
    s_rx_count = s_delivery_count = s_duplicate_count = s_ack_tx_count = s_crc_errors = s_drop_count = 0;
    s_sequence_gaps = s_out_of_order = 0;
    eg_e220_reset_diagnostics();
    s_rx_pause_requested = false;
}

static void gateway_rx_task(void *unused) {
    (void)unused;
    uint8_t frame[EG_DATA_FRAME_SIZE];
    while (true) {
        if (s_rx_pause_requested) { vTaskDelay(1); continue; }
#if CONFIG_EG_DIAGNOSTIC_MODE
        uint32_t poll_timeout_ms = s_rx_diag_mode ? s_rx_diag_poll_timeout_ms : 250;
        int length = eg_e220_receive(frame, sizeof(frame), poll_timeout_ms);
#else
        int length = eg_e220_receive(frame, sizeof(frame), 250);
#endif
        if (s_rx_pause_requested) continue;
        if (length == 0) continue;
        if (length < 0) { printf("ERR,UART_RX\n"); continue; }
        eg_data_packet_t packet;
        if (!eg_decode_data(frame, length, &packet)) {
            bool crc_only = length == EG_DATA_FRAME_SIZE && frame[0] == 'E' && frame[1] == 'G' &&
                frame[2] == EG_PROTOCOL_VERSION && frame[3] == EG_FRAME_DATA && eg_crc16(frame, EG_DATA_FRAME_SIZE - 2) != (uint16_t)(frame[EG_DATA_FRAME_SIZE - 2] | ((uint16_t)frame[EG_DATA_FRAME_SIZE - 1] << 8));
            if (crc_only) { s_crc_errors++; printf("ERR,CRC\n"); }
            else printf("ERR,PACKET\n");
            continue;
        }
        if (!s_armed) continue;
        if (packet.node_id != CONFIG_EG_NODE_ID || packet.sample_id >= s_gateway_expected || packet.copy_count < 1 ||
            packet.copy_count > EG_MAX_COPIES || packet.copy_index >= packet.copy_count || packet.importance > 2) {
            printf("ERR,PACKET\n"); continue;
        }
        if (eg_fault_drop(&s_fault_table, false, packet.sample_id, packet.copy_index)) {
#if CONFIG_EG_DIAGNOSTIC_MODE
            if (s_rx_diag_mode) printf("D_RX_INJECT_DROP,%u,%u,%u,%lld\n", packet.sample_id,
                packet.copy_index, packet.sequence, (long long)esp_timer_get_time());
#endif
            s_drop_count++;
            printf("DROP,DATA,%u,%u,%u\n", packet.sequence, packet.sample_id, packet.copy_index);
            continue;
        }
        s_rx_count++;
        bool duplicate = seen_before(packet.node_id, packet.sequence);
        if (duplicate) s_duplicate_count++;
        printf("RX,%u,%u,%u,%s\n", packet.sequence, packet.sample_id, packet.copy_index, duplicate ? "DUP" : "NEW");
        if (!duplicate) {
            if (s_have_sequence[packet.node_id] && packet.sequence > (uint16_t)(s_last_sequence[packet.node_id] + 1)) {
                uint16_t gap = (uint16_t)(packet.sequence - s_last_sequence[packet.node_id] - 1);
                s_sequence_gaps += gap;
                printf("GAP,%u,%u,%u,%u\n", packet.node_id, s_last_sequence[packet.node_id], packet.sequence, gap);
            } else if (s_have_sequence[packet.node_id] && packet.sequence < s_last_sequence[packet.node_id]) {
                s_out_of_order++; printf("ORDER,%u,%u,%u\n", packet.node_id, packet.sequence, s_last_sequence[packet.node_id]);
            }
            if (!s_have_sequence[packet.node_id] || packet.sequence > s_last_sequence[packet.node_id]) s_last_sequence[packet.node_id] = packet.sequence;
            s_have_sequence[packet.node_id] = true;
            s_delivery_count++; printf("DELIVER,%u,%u\n", packet.sample_id, packet.sequence);
        }
#if CONFIG_EG_DIAGNOSTIC_MODE
        if (s_rx_diag_mode && s_rx_diag_test == 'B' && packet.sequence % 5 == 0) {
            printf("ACK_SUPPRESS,%u\n", packet.sequence);
            continue;
        }
#endif
        eg_ack_packet_t ack = {.node_id = packet.node_id, .sequence = packet.sequence, .sample_id = packet.sample_id,
                               .status = 0, .copy_index = packet.copy_index};
        uint8_t response[EG_ACK_FRAME_SIZE];
        size_t ack_len = eg_encode_ack(&ack, response, sizeof(response));
#if CONFIG_EG_DIAGNOSTIC_MODE
        if (s_rx_diag_mode) printf("D_ACK_BEGIN,%u,%u,%u,%lld\n", packet.sample_id,
            packet.copy_index, packet.sequence, (long long)esp_timer_get_time());
#endif
        esp_err_t err = eg_e220_send(response, ack_len, 1000);
        if (err != ESP_OK) { printf("ERR,ACK_UART,%u,%d\n", packet.sequence, err); continue; }
        s_ack_tx_count++;
#if CONFIG_EG_DIAGNOSTIC_MODE
        if (s_rx_diag_mode) printf("D_ACK_DONE,%u,%u,%u,%lld\n", packet.sample_id,
            packet.copy_index, packet.sequence, (long long)esp_timer_get_time());
#endif
        printf("ACK_TX,%u,%u,%u,%u\n", packet.sequence, packet.sample_id, packet.copy_index, (unsigned)ack_len);
    }
}
#endif

static bool parse_strategy(const char *text, eg_strategy_t *out) {
    if (!strcasecmp(text, "NO_PROTECTION")) *out = EG_NO_PROTECTION;
    else if (!strcasecmp(text, "FIXED_REDUNDANCY")) *out = EG_FIXED_REDUNDANCY;
    else if (!strcasecmp(text, "EVENTGUARD")) *out = EG_EVENTGUARD;
    else if (!strcasecmp(text, "FIXED_1")) *out = EG_FIXED_1;
    else if (!strcasecmp(text, "FIXED_2")) *out = EG_FIXED_2;
    else if (!strcasecmp(text, "FIXED_3")) *out = EG_FIXED_3;
    else if (!strcasecmp(text, "IMPORTANCE_ONLY")) *out = EG_IMPORTANCE_ONLY;
    else if (!strcasecmp(text, "LINK_ONLY")) *out = EG_LINK_ONLY;
    else if (!strcasecmp(text, "UNIFORM_BUDGET")) *out = EG_UNIFORM_BUDGET;
    else if (!strcasecmp(text, "RANDOM_BUDGET")) *out = EG_RANDOM_BUDGET;
    else return false;
    return true;
}

static void handle_config(const char *line) {
    char strategy[24] = {0}, model[16] = {0};
    unsigned long seed = 0;
    unsigned int burst = 0, fixed = 2, maximum = 3, window = 12, fail_streak = 3, ack_timeout = CONFIG_EG_ACK_TIMEOUT_MS;
    unsigned int copy_budget = 0;
    double rate = 0, degraded = 0.80, bad = 0.50, important = 0.85, critical = 3.0;
    double scale_temp = 0.5, scale_humidity = 5.0, scale_light = 100.0, scale_soil = 8.0, alpha = 0.06;
    int fields = sscanf(line, "%23[^,],%lf,%15[^,],%lu,%u,%u,%u,%u,%lf,%lf,%u,%lf,%lf,%lf,%lf,%lf,%lf,%lf,%u,%u",
                        strategy, &rate, model, &seed, &burst, &fixed, &maximum, &window, &degraded, &bad,
                        &fail_streak, &important, &critical, &scale_temp, &scale_humidity, &scale_light, &scale_soil, &alpha, &ack_timeout, &copy_budget);
    if (fields != 19 && fields != 20) { printf("ERR,CONFIG_FIELDS,%d\n", fields); return; }
    if (!parse_strategy(strategy, &s_config.strategy)) { printf("ERR,STRATEGY\n"); return; }
    if (rate < 0 || rate > 1 ||
        burst < 2 || burst > 5 || (burst != 2 && burst != 3 && burst != 5) || fixed < 1 || fixed > 3 ||
        maximum < 1 || maximum > 3 || window < 1 || window > 24 || bad < 0 || degraded > 1 || bad > degraded ||
        fail_streak < 1 || fail_streak > 255 || important <= 0 || critical <= important ||
        scale_temp <= 0 || scale_humidity <= 0 || scale_light <= 0 || scale_soil <= 0 || alpha < 0 || alpha > 1 ||
        ack_timeout < 20 || ack_timeout > 2000) { printf("ERR,CONFIG_VALUE\n"); return; }
    s_config.loss_rate = rate;
    if (!strcasecmp(model, "BURST") || !strcasecmp(model, "BURST_COPY")) s_config.loss_model = EG_BURST_COPY;
    else if (!strcasecmp(model, "RANDOM") || !strcasecmp(model, "RANDOM_COPY")) s_config.loss_model = EG_RANDOM_COPY;
    else if (!strcasecmp(model, "BURST_SAMPLE")) s_config.loss_model = EG_BURST_SAMPLE;
    else { printf("ERR,LOSS_MODEL\n"); return; }
    s_config.seed = (uint32_t)seed; s_config.burst_length = (uint8_t)burst;
    s_config.fixed_redundancy = (uint8_t)fixed; s_config.max_redundancy = (uint8_t)maximum;
    s_config.link_window = (uint8_t)window; s_config.link_degraded_threshold = degraded;
    s_config.link_bad_threshold = bad; s_config.link_bad_fail_streak = (uint8_t)fail_streak;
    s_config.importance.important_threshold = (float)important;
    s_config.importance.critical_threshold = (float)critical;
    s_config.importance.scales[0] = (float)scale_temp; s_config.importance.scales[1] = (float)scale_humidity;
    s_config.importance.scales[2] = (float)scale_light; s_config.importance.scales[3] = (float)scale_soil;
    s_config.importance.baseline_alpha = (float)alpha; s_config.ack_timeout_ms = (uint16_t)ack_timeout;
    s_config.data_copy_budget = copy_budget;
    printf("CONFIGURED,%s,%.4f,%s,%lu,%u,%u,%u\n", strategy, rate, model, seed, burst, fixed, maximum);
}

#if CONFIG_EG_ROLE_SENSOR
static void sensor_run_task(void *unused) {
    (void)unused;
    if (!strcmp(s_mode, "REAL_SENSOR_MODE")) run_real_demo();
    else if (s_trace_loaded) run_trace();
    else printf("ERR,NO_TRACE\n");
    s_run_active = false;
    vTaskDelete(NULL);
}
#endif

static void handle_command(char *line) {
    char *save = NULL;
    const char *delimiters = ", \t\r\n";
    char *command = strtok_r(line, delimiters, &save);
    if (!command) return;
    if (!strcasecmp(command, "STATUS")) {
#if CONFIG_EG_ROLE_SENSOR
        printf("ROLE,SENSOR,%d,%d,%d\n", CONFIG_EG_NODE_ID, CONFIG_EG_E220_TX_GPIO, CONFIG_EG_E220_RX_GPIO);
#else
        printf("ROLE,GATEWAY,%d,%d,%d\n", CONFIG_EG_GATEWAY_ID, CONFIG_EG_E220_TX_GPIO, CONFIG_EG_E220_RX_GPIO);
#endif
        if (s_e220_ready) printf("E220_READY\n");
        else printf("E220_ERROR,%s\n", esp_err_to_name(s_e220_error));
        return;
    }
    if (!strcasecmp(command, "CONFIG")) { handle_config(save ? save : ""); return; }
    if (!strcasecmp(command, "SET_STRATEGY")) {
        char *value = strtok_r(NULL, delimiters, &save);
        if (!value || !parse_strategy(value, &s_config.strategy)) { printf("ERR,STRATEGY\n"); return; }
        printf("SET_STRATEGY,%s\n", value); return;
    }
    if (!strcasecmp(command, "SET_LOSS")) {
        char *value = strtok_r(NULL, delimiters, &save);
        double rate = value ? strtod(value, NULL) : -1.0;
        if (rate < 0.0 || rate > 1.0) { printf("ERR,LOSS_RATE\n"); return; }
        s_config.loss_rate = rate; printf("SET_LOSS,%.6f\n", rate); return;
    }
    if (!strcasecmp(command, "SET_SEED")) {
        char *value = strtok_r(NULL, delimiters, &save);
        if (!value) { printf("ERR,SEED\n"); return; }
        s_config.seed = (uint32_t)strtoul(value, NULL, 10);
        printf("SET_SEED,%lu\n", (unsigned long)s_config.seed); return;
    }
    if (!strcasecmp(command, "SET_LOSS_MODEL")) {
        char *value = strtok_r(NULL, delimiters, &save);
        if (!value) { printf("ERR,LOSS_MODEL\n"); return; }
        if (!strcasecmp(value, "RANDOM") || !strcasecmp(value, "RANDOM_COPY")) s_config.loss_model = EG_RANDOM_COPY;
        else if (!strcasecmp(value, "BURST") || !strcasecmp(value, "BURST_COPY")) s_config.loss_model = EG_BURST_COPY;
        else if (!strcasecmp(value, "BURST_SAMPLE")) s_config.loss_model = EG_BURST_SAMPLE;
        else { printf("ERR,LOSS_MODEL\n"); return; }
        printf("SET_LOSS_MODEL,%s\n", value); return;
    }
    if (!strcasecmp(command, "SET_BURST")) {
        char *value = strtok_r(NULL, delimiters, &save);
        unsigned long length = value ? strtoul(value, NULL, 10) : 0;
        if (length != 2 && length != 3 && length != 5) { printf("ERR,BURST_LENGTH\n"); return; }
        s_config.burst_length = (uint8_t)length; printf("SET_BURST,%lu\n", length); return;
    }
    if (!strcasecmp(command, "TRACE_MODE") || !strcasecmp(command, "REAL_SENSOR_MODE")) {
        snprintf(s_mode, sizeof(s_mode), "%s", command); printf("MODE,%s\n", s_mode); return;
    }
    if (!strcasecmp(command, "RESET")) {
#if CONFIG_EG_ROLE_SENSOR
#if CONFIG_EG_DIAGNOSTIC_MODE
        s_rx_diag_stop_requested = true;
#endif
        s_stop_requested = true; reset_sensor_run();
#else
        s_armed = false; clear_gateway_run();
#if CONFIG_EG_DIAGNOSTIC_MODE
        s_rx_diag_mode = false; s_rx_diag_test = 0; s_rx_diag_poll_timeout_ms = 250;
#endif
#endif
        printf("RESET,OK\n"); return;
    }
#if CONFIG_EG_ROLE_SENSOR
#if CONFIG_EG_DIAGNOSTIC_MODE
    if (!strcasecmp(command, "E220_RX_DIAGNOSTIC")) {
        char *test_text = strtok_r(NULL, delimiters, &save);
        char *frames_text = strtok_r(NULL, delimiters, &save);
        char *period_text = strtok_r(NULL, delimiters, &save);
        char *timeout_text = strtok_r(NULL, delimiters, &save);
        char test = test_text ? (char)toupper((unsigned char)test_text[0]) : 0;
        unsigned frames = frames_text ? (unsigned)strtoul(frames_text, NULL, 10) : 500;
        unsigned period_ms = period_text ? (unsigned)strtoul(period_text, NULL, 10) : 1000;
        unsigned ack_timeout_ms = timeout_text ? (unsigned)strtoul(timeout_text, NULL, 10) : 1000;
        if ((test != 'A' && test != 'B' && test != 'C') || frames == 0 || frames > MAX_TRACE ||
            period_ms == 0 || period_ms > 60000 || ack_timeout_ms < 20 || ack_timeout_ms > 2000) {
            printf("ERR,DIAG_ARGUMENT\n"); return;
        }
        if (!s_e220_ready || s_run_active) { printf("ERR,DIAG_NOT_READY\n"); return; }
        unsigned *parameters = malloc(3 * sizeof(unsigned));
        if (!parameters) { printf("ERR,DIAG_ALLOC\n"); return; }
        parameters[0] = frames; parameters[1] = period_ms; parameters[2] = ack_timeout_ms;
        s_rx_diag_test = test; s_rx_diag_stop_requested = false; s_run_active = true;
        if (xTaskCreate(e220_rx_diagnostic_task, "e220_rx_diag", 6144, parameters, 8, NULL) != pdPASS) {
            free(parameters); s_run_active = false; printf("ERR,DIAG_TASK\n"); return;
        }
        printf("E220_RX_DIAGNOSTIC,STARTED,%c,%u,%u,%u\n", test, frames, period_ms, ack_timeout_ms);
        return;
    }
#endif
    if (!strcasecmp(command, "TRACE_BEGIN")) {
        char *arg = strtok_r(NULL, delimiters, &save);
        s_trace_expected = arg ? strtoul(arg, NULL, 10) : 0;
        if (s_trace_expected > MAX_TRACE) { printf("ERR,TRACE_TOO_LONG\n"); s_trace_expected = 0; return; }
        s_trace_loaded = 0;
        eg_fault_build_model(&s_fault_table, s_config.seed, s_config.loss_rate, s_config.loss_model, s_config.burst_length, s_trace_expected);
        printf("TRACE_BEGIN,%u\n", (unsigned)s_trace_expected); return;
    }
    if (!strcasecmp(command, "S")) {
        unsigned int id, timestamp, truth; float t, h, light, soil;
        int fields = sscanf(save ? save : "", "%u,%u,%f,%f,%f,%f,%u", &id, &timestamp, &t, &h, &light, &soil, &truth);
        if (fields != 7 || s_trace_loaded >= s_trace_expected || id >= MAX_TRACE || truth > 2 ||
            (s_trace_loaded && timestamp <= s_trace[s_trace_loaded - 1].timestamp_ms)) { printf("ERR,TRACE_ROW\n"); return; }
        s_trace[s_trace_loaded++] = (trace_item_t){.id = (uint16_t)id, .timestamp_ms = timestamp,
            .values = {t, h, light, soil}, .truth = (uint8_t)truth};
        return;
    }
    if (!strcasecmp(command, "TRACE_END")) {
        if (s_trace_loaded != s_trace_expected) { printf("ERR,TRACE_COUNT,%u,%u\n", (unsigned)s_trace_loaded, (unsigned)s_trace_expected); return; }
        printf("TRACE_READY,%u\n", (unsigned)s_trace_loaded); return;
    }
    if (!strcasecmp(command, "START")) {
        if (!s_e220_ready) { printf("ERR,E220_NOT_READY\n"); return; }
        if (s_run_active) { printf("ERR,RUN_ACTIVE\n"); return; }
        s_stop_requested = false; s_run_active = true;
        if (xTaskCreate(sensor_run_task, "eg_sensor_run", 8192, NULL, 8, NULL) != pdPASS) {
            s_run_active = false; printf("ERR,RUN_TASK\n");
        }
        return;
    }
    if (!strcasecmp(command, "STOP")) { s_stop_requested = true; printf("STOP,REQUESTED\n"); return; }
#else
#if CONFIG_EG_DIAGNOSTIC_MODE
    if (!strcasecmp(command, "DIAG_GATEWAY")) {
        char *test_text = strtok_r(NULL, delimiters, &save);
        char *poll_text = strtok_r(NULL, delimiters, &save);
        char test = test_text ? (char)toupper((unsigned char)test_text[0]) : 0;
        unsigned poll_ms = poll_text ? (unsigned)strtoul(poll_text, NULL, 10) : 250;
        if ((test != 'A' && test != 'B' && test != 'C') || s_armed ||
            (poll_ms != 100 && poll_ms != 200 && poll_ms != 250 && poll_ms != 300)) {
            printf("ERR,DIAG_GATEWAY_ARGUMENT\n"); return;
        }
        s_rx_diag_mode = true; s_rx_diag_test = test; s_rx_diag_poll_timeout_ms = (uint16_t)poll_ms;
        printf("DIAG_GATEWAY_READY,%c,%u\n", test, poll_ms);
        return;
    }
#endif
    if (!strcasecmp(command, "TRACE_COUNT")) {
        char *arg = strtok_r(NULL, delimiters, &save);
        s_gateway_expected = arg ? strtoul(arg, NULL, 10) : 0;
        if (s_gateway_expected > MAX_TRACE) { printf("ERR,TRACE_TOO_LONG\n"); s_gateway_expected = 0; return; }
        eg_fault_build_model(&s_fault_table, s_config.seed, s_config.loss_rate, s_config.loss_model, s_config.burst_length, s_gateway_expected);
        printf("TRACE_COUNT,%u\n", (unsigned)s_gateway_expected); return;
    }
    if (!strcasecmp(command, "ARM")) {
        if (!s_e220_ready) { printf("ERR,E220_NOT_READY\n"); return; }
        clear_gateway_run(); s_armed = true; printf("ARMED,%u\n", (unsigned)s_gateway_expected); return;
    }
    if (!strcasecmp(command, "STOP")) { s_armed = false; printf("STOP,OK\n"); return; }
    if (!strcasecmp(command, "ENDRUN")) {
        s_armed = false;
        s_rx_pause_requested = true;
        print_e220_diagnostics();
#if CONFIG_EG_DIAGNOSTIC_MODE
        if (s_rx_diag_mode) printf("DIAG_GATEWAY_END,%u,%u,%u\n", (unsigned)s_rx_count,
            (unsigned)s_drop_count, (unsigned)s_ack_tx_count);
#endif
        s_rx_pause_requested = false;
        printf("END,%u,%u,%u,%u,%u,%u,%u,%u,%u\n", (unsigned)s_gateway_expected, (unsigned)s_rx_count, (unsigned)s_delivery_count,
               (unsigned)s_duplicate_count, (unsigned)s_ack_tx_count, (unsigned)s_crc_errors, (unsigned)s_drop_count,
               (unsigned)s_sequence_gaps, (unsigned)s_out_of_order); return;
    }
#endif
    printf("ERR,UNKNOWN_COMMAND,%s\n", command);
}

static void console_loop(void) {
    uint8_t input[64];
    char line[CONSOLE_LINE_MAX];
    size_t used = 0;
    bool overflow = false;
    for (;;) {
        int count = (int)read(STDIN_FILENO, input, sizeof(input));
        if (count <= 0) {
            // USB Serial/JTAG can return short reads between USB packets or while
            // no terminal is connected. Retain partial commands and retry.
            vTaskDelay(pdMS_TO_TICKS(20));
            continue;
        }
        for (int i = 0; i < count; ++i) {
            uint8_t ch = input[i];
            if (ch == '\r' || ch == '\n') {
                if (overflow) printf("ERR,COMMAND_TOO_LONG\n");
                else if (used) { line[used] = '\0'; handle_command(line); }
                used = 0;
                overflow = false;
            } else if (ch >= 0x20 && ch <= 0x7e) {
                if (used < sizeof(line) - 1 && !overflow) line[used++] = (char)ch;
                else overflow = true;
            }
        }
    }
}

void app_main(void) {
    ESP_LOGI(TAG, "EventGuard-LoRa firmware 0.1.0 starting");
    s_e220_error = eg_e220_init();
    s_e220_ready = s_e220_error == ESP_OK;
    if (!s_e220_ready) {
        ESP_LOGE(TAG, "E220 initialization failed: %s", esp_err_to_name(s_e220_error));
        printf("E220_ERROR,%s\n", esp_err_to_name(s_e220_error));
    } else {
        printf("E220_READY\n");
    }
#if CONFIG_EG_ROLE_SENSOR
    uint8_t mac[6]; esp_read_mac(mac, ESP_MAC_WIFI_STA);
    printf("ROLE,SENSOR,%02x:%02x:%02x:%02x:%02x:%02x\n", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    console_loop();
#else
    uint8_t mac[6]; esp_read_mac(mac, ESP_MAC_WIFI_STA);
    printf("ROLE,GATEWAY,%02x:%02x:%02x:%02x:%02x:%02x\n", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    xTaskCreate(gateway_rx_task, "eg_gateway_rx", 4096, NULL, 10, NULL);
    console_loop();
#endif
}
