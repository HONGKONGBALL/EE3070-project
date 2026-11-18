set(CLIENT_SOURCES mcp_client.cpp tool_discovery.cpp schema_headers.cpp geometry_completion.cpp geometry_url.cpp)
if(COMMAND idf_component_register)
    idf_component_register(SRCS ${CLIENT_SOURCES} esp_transport.cpp esp_geometry.cpp INCLUDE_DIRS include
                           PRIV_INCLUDE_DIRS ../route_parser
                           REQUIRES route_parser nav_core json esp_http_client esp-tls mbedtls esp_timer)
    target_compile_features(${COMPONENT_LIB} PUBLIC cxx_std_17)
else()
    add_library(mcp_client STATIC ${CLIENT_SOURCES})
    target_include_directories(mcp_client PUBLIC include PRIVATE ../route_parser ../route_parser/third_party)
    target_link_libraries(mcp_client PUBLIC route_parser)
    target_compile_features(mcp_client PUBLIC cxx_std_17)
    target_compile_options(mcp_client PRIVATE -Wall -Wextra -Werror)
endif()
