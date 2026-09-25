#include "sensor_drivers.h"

#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "sdkconfig.h"

#define TAG "sensors"

static i2c_master_bus_handle_t s_bus;
static i2c_master_dev_handle_t s_sht30;
static i2c_master_dev_handle_t s_bh1750;
static adc_oneshot_unit_handle_t s_adc;

esp_err_t eg_sensors_init(void) {
    i2c_master_bus_config_t bus_cfg = {.i2c_port = I2C_NUM_0,
        .sda_io_num = CONFIG_EG_I2C_SDA_GPIO, .scl_io_num = CONFIG_EG_I2C_SCL_GPIO,
        .clk_source = I2C_CLK_SRC_DEFAULT, .glitch_ignore_cnt = 7, .flags.enable_internal_pullup = true};
    ESP_RETURN_ON_ERROR(i2c_new_master_bus(&bus_cfg, &s_bus), TAG, "I2C bus init");
    i2c_device_config_t sht_cfg = {.dev_addr_length = I2C_ADDR_BIT_LEN_7, .device_address = 0x44, .scl_speed_hz = 100000};
    i2c_device_config_t light_cfg = {.dev_addr_length = I2C_ADDR_BIT_LEN_7, .device_address = 0x23, .scl_speed_hz = 100000};
    ESP_RETURN_ON_ERROR(i2c_master_bus_add_device(s_bus, &sht_cfg, &s_sht30), TAG, "SHT30 attach");
    ESP_RETURN_ON_ERROR(i2c_master_bus_add_device(s_bus, &light_cfg, &s_bh1750), TAG, "BH1750 attach");
    uint8_t power_on = 0x01, continuous_high_res = 0x10;
    ESP_RETURN_ON_ERROR(i2c_master_transmit(s_bh1750, &power_on, 1, 100), TAG, "BH1750 power on");
    ESP_RETURN_ON_ERROR(i2c_master_transmit(s_bh1750, &continuous_high_res, 1, 100), TAG, "BH1750 mode");
    adc_oneshot_unit_init_cfg_t adc_cfg = {.unit_id = ADC_UNIT_1, .ulp_mode = ADC_ULP_MODE_DISABLE};
    ESP_RETURN_ON_ERROR(adc_oneshot_new_unit(&adc_cfg, &s_adc), TAG, "ADC init");
    adc_oneshot_chan_cfg_t chan_cfg = {.atten = ADC_ATTEN_DB_12, .bitwidth = ADC_BITWIDTH_DEFAULT};
    ESP_RETURN_ON_ERROR(adc_oneshot_config_channel(s_adc, ADC_CHANNEL_3, &chan_cfg), TAG, "soil ADC config");
    ESP_LOGI(TAG, "SHT30 and BH1750 at I2C 0x44/0x23; soil ADC GPIO%d", CONFIG_EG_SOIL_ADC_GPIO);
    return ESP_OK;
}

static uint8_t sht_crc8(const uint8_t *data) {
    uint8_t crc = 0xFF;
    for (int i = 0; i < 2; ++i) {
        crc ^= data[i];
        for (int bit = 0; bit < 8; ++bit) crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x31) : (uint8_t)(crc << 1);
    }
    return crc;
}

esp_err_t eg_sensors_read(eg_sensor_values_t *values) {
    if (!values) return ESP_ERR_INVALID_ARG;
    uint8_t command[] = {0x24, 0x00};
    uint8_t raw[6];
    ESP_RETURN_ON_ERROR(i2c_master_transmit(s_sht30, command, sizeof(command), 100), TAG, "SHT30 measure command");
    esp_rom_delay_us(15000);
    ESP_RETURN_ON_ERROR(i2c_master_receive(s_sht30, raw, sizeof(raw), 100), TAG, "SHT30 read");
    if (sht_crc8(raw) != raw[2] || sht_crc8(raw + 3) != raw[5]) return ESP_ERR_INVALID_CRC;
    uint16_t t = (uint16_t)raw[0] << 8 | raw[1];
    uint16_t h = (uint16_t)raw[3] << 8 | raw[4];
    values->temperature = -45.0f + 175.0f * t / 65535.0f;
    values->humidity = 100.0f * h / 65535.0f;
    uint8_t lux_bytes[2];
    ESP_RETURN_ON_ERROR(i2c_master_receive(s_bh1750, lux_bytes, sizeof(lux_bytes), 100), TAG, "BH1750 read");
    values->light = (((uint16_t)lux_bytes[0] << 8) | lux_bytes[1]) / 1.2f;
    int soil_raw = 0;
    ESP_RETURN_ON_ERROR(adc_oneshot_read(s_adc, ADC_CHANNEL_3, &soil_raw), TAG, "soil read");
    values->soil_moisture = (float)soil_raw * 100.0f / 4095.0f;
    return ESP_OK;
}
