idf_component_register(SRCS "app_main.cpp" "wire_json.cpp" "wire_input.cpp" "network.cpp" "config.cpp" "button_uart.cpp" INCLUDE_DIRS "." REQUIRES nav_core ble_link mcp_client route_parser coordinates esp_wifi esp_netif esp_event esp_timer esp_http_client esp-tls nvs_flash driver esp_driver_uart esp_driver_gpio json app_update)
target_compile_features(${COMPONENT_LIB} PUBLIC cxx_std_17)
