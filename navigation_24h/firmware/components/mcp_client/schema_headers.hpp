#pragma once
#include "cJSON.h"
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace nav::mcp::detail {
std::string header_value(std::string_view value);
bool schema_headers(const cJSON* schema, const cJSON* arguments,
                    std::vector<std::pair<std::string, std::string>>& headers);
bool valid_arguments(const cJSON* schema, const cJSON* arguments);
}
