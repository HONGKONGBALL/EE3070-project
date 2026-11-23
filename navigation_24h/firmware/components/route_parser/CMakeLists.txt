set(PARSER_SOURCES rpc_stream.cpp route_parser.cpp provider_status.cpp route_summary.cpp)
if(COMMAND idf_component_register)
    idf_component_register(SRCS ${PARSER_SOURCES} INCLUDE_DIRS include
                           REQUIRES json nav_core)
    target_compile_features(${COMPONENT_LIB} PUBLIC cxx_std_17)
else()
    add_library(route_parser STATIC ${PARSER_SOURCES} third_party/cJSON.c)
    set_source_files_properties(third_party/cJSON.c PROPERTIES COMPILE_OPTIONS "-Wno-deprecated-declarations")
    target_include_directories(route_parser PUBLIC include ../nav_core/include
                               PRIVATE third_party)
    target_compile_definitions(route_parser PRIVATE CJSON_NESTING_LIMIT=64)
    target_compile_features(route_parser PUBLIC cxx_std_17)
    target_compile_options(route_parser PRIVATE -Wall -Wextra -Werror)
endif()
