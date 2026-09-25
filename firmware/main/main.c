#include <math.h>
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
#include "sdkconfig.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define TAG "eventguard"
#define CONSOLE_LINE_MAX 256
#define MAX_TRACE EG_MAX_TRACE

typedef enum { STRATEGY_NONE = 0, STRATEGY_FIXED = 1, STRATEGY_EVENTGUARD = 2 } strategy_t;
typedef enum { TRUTH_NORMAL = 0, TRUTH_IMPORTANT = 1, TRUTH_CRITICAL = 2 } truth_t;
typedef enum { LINK_GOOD = 0, LINK_DEGRADED = 1, LINK_BAD = 2 } link_state_t;

typedef struct {
    uint16_t id;
    uint32_t timestamp_ms;
    float values[4];
    uint8_t truth;
} trace_item_t;

typedef struct {
    strategy_t strategy;
    double loss_rate;
    bool burst;
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
} run_config_t;

static run_config_t s_config = {.strategy = STRATEGY_EVENTGUARD, .loss_rate = 0.0f, .burst = false,
    .seed = 11, .burst_length = 3, .fixed_redundancy = 2, .max_redundancy = 3, .link_window = 12,
    .link_degraded_threshold = 0.80, .link_bad_threshold = 0.50, .link_bad_fail_streak = 3,
    .importance = {.important_threshold = 0.85f, .critical_threshold = 2.40f,
                   .scales = {0.5f, 5.0f, 100.0f, 8.0f}, .baseline_alpha = 0.06f},
    .ack_timeout_ms = CONFIG_EG_ACK_TIMEOUT_MS};
static eg_fault_table_t s_fault_table;
static char s_mode[20] = "TRACE_MODE";
static bool s_e220_ready;
static esp_err_t s_e220_error = ESP_FAIL;

#if CONFIG_EG_ROLE_SENSOR
static trace_item_t s_trace[MAX_TRACE];
static size_t s_trace_expected;
static size_t s_trace_loaded;
static uint16_t s_next_sequence;
static uint32_t s_sensor_tx_count;
static uint32_t s_sensor_ack_count;
static eg_importance_state_t s_classifier;
static bool s_ack_history[12];
static size_t s_ack_count;
static size_t s_ack_next;
static uint8_t s_fail_streak;
static volatile bool s_stop_requested;
static volatile bool s_run_active;

static const char *importance_name(uint8_t importance) {
    return importance == 2 ? "CRITICAL" : (importance == 1 ? "IMPORTANT" : "NORMAL");
}
static const char *truth_name(uint8_t truth) {
    return truth == 2 ? "CRITICAL" : (truth == 1 ? "IMPORTANT" : "NORMAL");
}
static const char *link_name(link_state_t link) {
    return link == LINK_BAD ? "BAD" : (link == LINK_DEGRADED ? "DEGRADED" : "GOOD");
}

static link_state_t link_state(void) {
    if (s_fail_streak >= s_config.link_bad_fail_streak) return LINK_BAD;
    if (!s_ack_count) return LINK_GOOD;
    size_t successes = 0;
    for (size_t i = 0; i < s_ack_count; ++i) successes += s_ack_history[i];
    float ratio = (float)successes / s_ack_count;
    if (ratio < s_config.link_bad_threshold) return LINK_BAD;
    if (ratio < s_config.link_degraded_threshold) return LINK_DEGRADED;
    return LINK_GOOD;
}

static void link_observe(bool success) {
    s_ack_history[s_ack_next] = success;
    s_ack_next = (s_ack_next + 1) % s_config.link_window;
    if (s_ack_count < s_config.link_window) s_ack_count++;
    s_fail_streak = success ? 0 : (s_fail_streak < 255 ? s_fail_streak + 1 : 255);
}

static uint8_t redundancy_for(uint8_t importance, link_state_t link) {
    if (s_config.strategy == STRATEGY_NONE) return 1;
    if (s_config.strategy == STRATEGY_FIXED) return s_config.fixed_redundancy > s_config.max_redundancy ? s_config.max_redundancy : s_config.fixed_redundancy;
    uint8_t count = importance > 2 ? 1 : (uint8_t)(importance + 1);
    if (link == LINK_DEGRADED) count++;
    else if (link == LINK_BAD && importance > 0) count += 2;
    if (count > s_config.max_redundancy) count = s_config.max_redundancy;
    return count ? count : 1;
}

static void reset_sensor_run(void) {
    s_next_sequence = 0; s_sensor_tx_count = 0; s_sensor_ack_count = 0;
    memset(&s_classifier, 0, sizeof(s_classifier));
    memset(s_ack_history, 0, sizeof(s_ack_history)); s_ack_count = 0; s_ack_next = 0; s_fail_streak = 0;
}

static void run_trace(void) {
    reset_sensor_run();
    size_t completed = 0;
    for (size_t index = 0; index < s_trace_loaded; ++index) {
        if (s_stop_requested) break;
        trace_item_t *sample = &s_trace[index];
        eg_observation_t observation = {.timestamp_ms = sample->timestamp_ms};
        memcpy(observation.values, sample->values, sizeof(observation.values));
        float score = 0.0f;
        uint8_t importance = eg_classify(&s_classifier, &observation, &s_config.importance, &score);
        link_state_t current_link = link_state();
        uint8_t copies = redundancy_for(importance, current_link);
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
            int err = eg_e220_send(frame, frame_len, 1000);
            if (err != ESP_OK) { printf("ERR,UART_SEND,%u,%u,%d\n", sequence, copy, err); continue; }
            sent++; s_sensor_tx_count++;
            printf("TX,%u,%u,%u,%u,%u\n", sample->id, sequence, copy, copies, (unsigned)frame_len);
            uint8_t ack_frame[EG_DATA_FRAME_SIZE];
            int received = eg_e220_receive(ack_frame, sizeof(ack_frame), s_config.ack_timeout_ms);
            if (received == 0) { printf("TIMEOUT,%u,%u\n", sequence, copy); continue; }
            if (received < 0) { printf("ERR,UART_RX,%u,%u\n", sequence, copy); continue; }
            eg_ack_packet_t ack;
            if (!eg_decode_ack(ack_frame, received, &ack)) { printf("ERR,CRC,%u,%u\n", sequence, copy); continue; }
            if (ack.node_id != CONFIG_EG_NODE_ID || ack.sequence != sequence || ack.sample_id != sample->id) {
                printf("ERR,ACK_MISMATCH,%u,%u\n", sequence, copy); continue;
            }
            if (eg_fault_drop(&s_fault_table, true, sample->id, copy)) {
                printf("ACK,%u,%u,%u,DROP\n", sequence, sample->id, copy);
                continue;
            }
            printf("ACK,%u,%u,%u,OK\n", sequence, sample->id, copy);
            s_sensor_ack_count++; ack_success = true;
        }
        float latency_ms = (float)(esp_timer_get_time() - sample_start) / 1000.0f;
        printf("SAMPLE,%u,%s,%s,%.4f,%s,%u,%u,%u,%.3f\n", sample->id, truth_name(sample->truth), importance_name(importance), score,
               link_name(current_link), copies, sent, ack_success ? 1 : 0, latency_ms);
        link_observe(ack_success);
        completed++;
    }
    printf("END,%u,%u,%u,%u\n", (unsigned)completed, (unsigned)s_sensor_tx_count, (unsigned)s_sensor_ack_count, s_stop_requested ? 1 : 0);
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

static bool seen_before(uint8_t node, uint16_t sequence) {
    for (size_t i = 0; i < s_seen_count; ++i) if (s_seen[i].node_id == node && s_seen[i].sequence == sequence) return true;
    if (s_seen_count < MAX_TRACE) s_seen[s_seen_count++] = (seen_packet_t){node, sequence, 0};
    return false;
}

static void clear_gateway_run(void) {
    memset(s_seen, 0, sizeof(s_seen)); s_seen_count = 0;
    memset(s_last_sequence, 0, sizeof(s_last_sequence)); memset(s_have_sequence, 0, sizeof(s_have_sequence));
    s_rx_count = s_delivery_count = s_duplicate_count = s_ack_tx_count = s_crc_errors = s_drop_count = 0;
    s_sequence_gaps = s_out_of_order = 0;
}

static void gateway_rx_task(void *unused) {
    (void)unused;
    uint8_t frame[EG_DATA_FRAME_SIZE];
    while (true) {
        int length = eg_e220_receive(frame, sizeof(frame), 250);
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
        eg_ack_packet_t ack = {.node_id = packet.node_id, .sequence = packet.sequence, .sample_id = packet.sample_id,
                               .status = 0, .copy_index = packet.copy_index};
        uint8_t response[EG_ACK_FRAME_SIZE];
        size_t ack_len = eg_encode_ack(&ack, response, sizeof(response));
        esp_err_t err = eg_e220_send(response, ack_len, 1000);
        if (err != ESP_OK) { printf("ERR,ACK_UART,%u,%d\n", packet.sequence, err); continue; }
        s_ack_tx_count++;
        printf("ACK_TX,%u,%u,%u,%u\n", packet.sequence, packet.sample_id, packet.copy_index, (unsigned)ack_len);
    }
}
#endif

static bool parse_strategy(const char *text, strategy_t *out) {
    if (!strcasecmp(text, "NO_PROTECTION")) *out = STRATEGY_NONE;
    else if (!strcasecmp(text, "FIXED_REDUNDANCY")) *out = STRATEGY_FIXED;
    else if (!strcasecmp(text, "EVENTGUARD")) *out = STRATEGY_EVENTGUARD;
    else return false;
    return true;
}

static void handle_config(const char *line) {
    char strategy[24] = {0}, model[16] = {0};
    unsigned long seed = 0;
    unsigned int burst = 0, fixed = 2, maximum = 3, window = 12, fail_streak = 3, ack_timeout = CONFIG_EG_ACK_TIMEOUT_MS;
    double rate = 0, degraded = 0.80, bad = 0.50, important = 0.85, critical = 2.40;
    double scale_temp = 0.5, scale_humidity = 5.0, scale_light = 100.0, scale_soil = 8.0, alpha = 0.06;
    int fields = sscanf(line, "%23[^,],%lf,%15[^,],%lu,%u,%u,%u,%u,%lf,%lf,%u,%lf,%lf,%lf,%lf,%lf,%lf,%lf,%u",
                        strategy, &rate, model, &seed, &burst, &fixed, &maximum, &window, &degraded, &bad,
                        &fail_streak, &important, &critical, &scale_temp, &scale_humidity, &scale_light, &scale_soil, &alpha, &ack_timeout);
    if (fields != 19) { printf("ERR,CONFIG_FIELDS,%d\n", fields); return; }
    if (!parse_strategy(strategy, &s_config.strategy)) { printf("ERR,STRATEGY\n"); return; }
    if (rate < 0 || rate > 1 ||
        burst < 2 || burst > 5 || (burst != 2 && burst != 3 && burst != 5) || fixed < 1 || fixed > 3 ||
        maximum < 1 || maximum > 3 || window < 1 || window > 12 || bad < 0 || degraded > 1 || bad > degraded ||
        fail_streak < 1 || fail_streak > 255 || important <= 0 || critical <= important ||
        scale_temp <= 0 || scale_humidity <= 0 || scale_light <= 0 || scale_soil <= 0 || alpha < 0 || alpha > 1 ||
        ack_timeout < 20 || ack_timeout > 2000) { printf("ERR,CONFIG_VALUE\n"); return; }
    s_config.loss_rate = rate; s_config.burst = !strcasecmp(model, "BURST");
    if (strcasecmp(model, "BURST") && strcasecmp(model, "RANDOM")) { printf("ERR,LOSS_MODEL\n"); return; }
    s_config.seed = (uint32_t)seed; s_config.burst_length = (uint8_t)burst;
    s_config.fixed_redundancy = (uint8_t)fixed; s_config.max_redundancy = (uint8_t)maximum;
    s_config.link_window = (uint8_t)window; s_config.link_degraded_threshold = degraded;
    s_config.link_bad_threshold = bad; s_config.link_bad_fail_streak = (uint8_t)fail_streak;
    s_config.importance.important_threshold = (float)important;
    s_config.importance.critical_threshold = (float)critical;
    s_config.importance.scales[0] = (float)scale_temp; s_config.importance.scales[1] = (float)scale_humidity;
    s_config.importance.scales[2] = (float)scale_light; s_config.importance.scales[3] = (float)scale_soil;
    s_config.importance.baseline_alpha = (float)alpha; s_config.ack_timeout_ms = (uint16_t)ack_timeout;
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
        if (!value || (strcasecmp(value, "RANDOM") && strcasecmp(value, "BURST"))) { printf("ERR,LOSS_MODEL\n"); return; }
        s_config.burst = !strcasecmp(value, "BURST"); printf("SET_LOSS_MODEL,%s\n", value); return;
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
        s_stop_requested = true; reset_sensor_run();
#else
        s_armed = false; clear_gateway_run();
#endif
        printf("RESET,OK\n"); return;
    }
#if CONFIG_EG_ROLE_SENSOR
    if (!strcasecmp(command, "TRACE_BEGIN")) {
        char *arg = strtok_r(NULL, delimiters, &save);
        s_trace_expected = arg ? strtoul(arg, NULL, 10) : 0;
        if (s_trace_expected > MAX_TRACE) { printf("ERR,TRACE_TOO_LONG\n"); s_trace_expected = 0; return; }
        s_trace_loaded = 0;
        eg_fault_build(&s_fault_table, s_config.seed, s_config.loss_rate, s_config.burst, s_config.burst_length, s_trace_expected);
        printf("TRACE_BEGIN,%u\n", (unsigned)s_trace_expected); return;
    }
    if (!strcasecmp(command, "S")) {
        unsigned int id, timestamp, truth; float t, h, light, soil;
        int fields = sscanf(save ? save : "", "%u,%u,%f,%f,%f,%f,%u", &id, &timestamp, &t, &h, &light, &soil, &truth);
        if (fields != 7 || s_trace_loaded >= s_trace_expected || id >= MAX_TRACE || truth > 2) { printf("ERR,TRACE_ROW\n"); return; }
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
    if (!strcasecmp(command, "TRACE_COUNT")) {
        char *arg = strtok_r(NULL, delimiters, &save);
        s_gateway_expected = arg ? strtoul(arg, NULL, 10) : 0;
        if (s_gateway_expected > MAX_TRACE) { printf("ERR,TRACE_TOO_LONG\n"); s_gateway_expected = 0; return; }
        eg_fault_build(&s_fault_table, s_config.seed, s_config.loss_rate, s_config.burst, s_config.burst_length, s_gateway_expected);
        printf("TRACE_COUNT,%u\n", (unsigned)s_gateway_expected); return;
    }
    if (!strcasecmp(command, "ARM")) {
        if (!s_e220_ready) { printf("ERR,E220_NOT_READY\n"); return; }
        clear_gateway_run(); s_armed = true; printf("ARMED,%u\n", (unsigned)s_gateway_expected); return;
    }
    if (!strcasecmp(command, "STOP")) { s_armed = false; printf("STOP,OK\n"); return; }
    if (!strcasecmp(command, "ENDRUN")) {
        s_armed = false;
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
