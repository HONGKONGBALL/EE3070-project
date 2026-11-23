#pragma once
#include "route_parser.hpp"
#include "cJSON.h"
namespace nav::mcp::detail {
ServiceEnvelope provider_envelope(const cJSON* data);
ParseStatus provider_parse_status(const cJSON* data);
}
