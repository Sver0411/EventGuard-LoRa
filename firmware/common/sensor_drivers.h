#pragma once

#include "esp_err.h"

typedef struct {
    float temperature;
    float humidity;
    float light;
    float soil_moisture;
} eg_sensor_values_t;

esp_err_t eg_sensors_init(void);
esp_err_t eg_sensors_read(eg_sensor_values_t *values);
