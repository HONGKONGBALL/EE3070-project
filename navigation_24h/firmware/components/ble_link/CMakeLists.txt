idf_component_register(SRCS "ble_link.cpp" "frame.cpp" "pairing_policy.cpp" "pairing_adapter.cpp" INCLUDE_DIRS "include" REQUIRES bt nvs_flash esp_timer driver)
target_compile_features(${COMPONENT_LIB} PUBLIC cxx_std_17)
