add_executable(esp_transport_tests transport_tests.cpp http_fake.cpp
  ../../firmware/components/mcp_client/esp_transport.cpp ../../firmware/components/mcp_client/esp_geometry.cpp)
target_compile_definitions(esp_transport_tests PRIVATE ESP_PLATFORM)
target_include_directories(esp_transport_tests PRIVATE mocks ../host
  ../../firmware/components/mcp_client ../../firmware/components/route_parser/third_party)
target_link_libraries(esp_transport_tests PRIVATE mcp_client)
target_compile_options(esp_transport_tests PRIVATE -Wall -Wextra -Wpedantic -Werror)
foreach(fixture IN ITEMS sdk_cache cached_json cached_chunked_sse partial_json uncached_json
    mime_header session_header empty_json)
  add_test(NAME esp_transport_${fixture} COMMAND esp_transport_tests --fixture ${fixture})
  set_tests_properties(esp_transport_${fixture} PROPERTIES LABELS "transport;http")
endforeach()
add_executable(geometry_transport_tests geometry_transport_tests.cpp http_fake.cpp
  ../../firmware/components/mcp_client/esp_transport.cpp ../../firmware/components/mcp_client/esp_geometry.cpp)
target_compile_definitions(geometry_transport_tests PRIVATE ESP_PLATFORM)
target_include_directories(geometry_transport_tests PRIVATE mocks ../host
  ../../firmware/components/mcp_client ../../firmware/components/route_parser/third_party)
target_link_libraries(geometry_transport_tests PRIVATE mcp_client)
target_compile_options(geometry_transport_tests PRIVATE -Wall -Wextra -Wpedantic -Werror)
foreach(fixture IN ITEMS cached chunked mime quota capacity endpoint key duplicate coordinates)
  add_test(NAME geometry_http_${fixture} COMMAND geometry_transport_tests ${fixture})
endforeach()
