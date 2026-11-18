#pragma once
#include "cJSON.h"
#include <cmath>
#include <cstdlib>
#include <limits>
#include <memory>
#include <string>
#include <string_view>

namespace nav::mcp::detail {
using Json = std::unique_ptr<cJSON, decltype(&cJSON_Delete)>;
inline bool bounded_utf8_json(std::string_view source) {
    unsigned depth = 0;
    bool quoted = false, escaped = false;
    for (std::size_t i = 0; i < source.size(); ++i) {
        const auto c = static_cast<unsigned char>(source[i]);
        if (c >= 0x80) {
            const unsigned continuation = c >= 0xc2 && c <= 0xdf ? 1 : c >= 0xe0 && c <= 0xef ? 2 :
                c >= 0xf0 && c <= 0xf4 ? 3 : 0;
            if (!continuation || i + continuation >= source.size()) return false;
            const auto first = static_cast<unsigned char>(source[i + 1]);
            if ((c == 0xe0 && first < 0xa0) || (c == 0xed && first >= 0xa0) ||
                (c == 0xf0 && first < 0x90) || (c == 0xf4 && first >= 0x90)) return false;
            for (unsigned j = 1; j <= continuation; ++j) {
                const auto next = static_cast<unsigned char>(source[i + j]);
                if (next < 0x80 || next > 0xbf) return false;
            }
            i += continuation;
            continue;
        }
        if (escaped) { escaped = false; continue; }
        if (quoted && c == '\\') { escaped = true; continue; }
        if (c == '"') { quoted = !quoted; continue; }
        if (!quoted && (c == '{' || c == '[') && ++depth > 64) return false;
        if (!quoted && (c == '}' || c == ']')) { if (!depth) return false; --depth; }
    }
    return !quoted && depth == 0;
}
inline Json parse(std::string_view source) {
    if (!bounded_utf8_json(source)) return Json(nullptr, cJSON_Delete);
    const char* end = nullptr;
    Json result(cJSON_ParseWithLengthOpts(source.data(), source.size(), &end, false), cJSON_Delete);
    if (!result || !end) return Json(nullptr, cJSON_Delete);
    const auto* limit = source.data() + source.size();
    while (end < limit && (*end == ' ' || *end == '\r' || *end == '\n' || *end == '\t')) ++end;
    if (end != limit) return Json(nullptr, cJSON_Delete);
    return result;
}
inline const cJSON* member(const cJSON* source, const char* key) {
    return cJSON_GetObjectItemCaseSensitive(source, key);
}
inline std::string_view text(const cJSON* source) {
    return cJSON_IsString(source) && source->valuestring ? source->valuestring : "";
}
inline bool number(const cJSON* source, double& output) {
    if (cJSON_IsNumber(source)) { output = source->valuedouble; return std::isfinite(output); }
    if (!cJSON_IsString(source) || !source->valuestring || !*source->valuestring) return false;
    char* end = nullptr;
    output = std::strtod(source->valuestring, &end);
    return end && *end == '\0' && std::isfinite(output);
}
inline bool id_matches(const cJSON* id, std::string_view expected) {
    if (cJSON_IsString(id)) return text(id) == expected;
    if (!cJSON_IsNumber(id) || !std::isfinite(id->valuedouble) ||
        std::floor(id->valuedouble) != id->valuedouble ||
        std::abs(id->valuedouble) > 9007199254740991.0) return false;
    return std::to_string(static_cast<long long>(id->valuedouble)) == expected;
}
}
