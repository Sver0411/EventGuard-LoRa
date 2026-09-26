idf_component_register(SRCS "e220.c" "e220_stream_parser.c" "protocol.c" "importance.c" "faults.c" "strategy.c" "sensor_drivers.c"
                       INCLUDE_DIRS "."
                       REQUIRES driver esp_timer esp_adc esp_rom)
