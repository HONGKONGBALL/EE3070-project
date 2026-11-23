#include "schema_headers.hpp"
#include "client_json.hpp"
#include <algorithm>
#include <cmath>
#include <cctype>
#include <set>

namespace nav::mcp::detail {
std::string header_value(std::string_view value) {
    const bool whitespace_edge = !value.empty() && (value.front() == ' ' || value.front() == '\t' || value.back() == ' ' || value.back() == '\t');
    const bool safe = std::all_of(value.begin(), value.end(), [](unsigned char c) { return (c >= 0x20 && c <= 0x7e) || c == '\t'; });
    const bool sentinel = value.size() >= 11 && value.substr(0, 9) == "=?base64?" && value.substr(value.size() - 2) == "?=";
    if (safe && !whitespace_edge && !sentinel) return std::string(value);
    constexpr char alphabet[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string encoded = "=?base64?";
    for (std::size_t offset = 0; offset < value.size(); offset += 3) {
        const auto a = static_cast<unsigned char>(value[offset]);
        const auto b = offset + 1 < value.size() ? static_cast<unsigned char>(value[offset + 1]) : 0;
        const auto c = offset + 2 < value.size() ? static_cast<unsigned char>(value[offset + 2]) : 0;
        encoded += alphabet[a >> 2];
        encoded += alphabet[((a & 3) << 4) | (b >> 4)];
        encoded += offset + 1 < value.size() ? alphabet[((b & 15) << 2) | (c >> 6)] : '=';
        encoded += offset + 2 < value.size() ? alphabet[c & 63] : '=';
    }
    return encoded + "?=";
}
namespace {
bool token(std::string_view value) {
    constexpr std::string_view punctuation = "!#$%&'*+-.^_`|~";
    return !value.empty() && value.size() <= 128 && std::all_of(value.begin(), value.end(), [punctuation](unsigned char c) {
        return (c >= '0' && c <= '9') || (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
            punctuation.find(static_cast<char>(c)) != std::string_view::npos;
    });
}
bool extract(const cJSON* schema, const cJSON* instance, bool reachable,
             std::set<std::string>& seen, std::vector<std::pair<std::string, std::string>>& headers) {
    const auto* annotation = member(schema, "x-mcp-header");
    if (annotation) {
        const auto name = text(annotation);
        const auto type = text(member(schema, "type"));
        if (!reachable || !token(name) || (type != "string" && type != "integer" && type != "boolean")) return false;
        std::string lower(name);
        std::transform(lower.begin(), lower.end(), lower.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
        if (!seen.insert(lower).second) return false;
        if (instance && !cJSON_IsNull(instance)) {
            std::string value;
            if (type == "string" && cJSON_IsString(instance)) value = std::string(text(instance));
            else if (type == "boolean" && cJSON_IsBool(instance)) value = cJSON_IsTrue(instance) ? "true" : "false";
            else if (type == "integer" && cJSON_IsNumber(instance) && std::isfinite(instance->valuedouble) &&
                     std::floor(instance->valuedouble) == instance->valuedouble && std::abs(instance->valuedouble) <= 9007199254740991.0)
                value = std::to_string(static_cast<long long>(instance->valuedouble));
            else return false;
            headers.emplace_back("Mcp-Param-" + std::string(name), header_value(value));
        }
    }
    if (cJSON_IsObject(schema)) {
        const cJSON* child = nullptr;
        cJSON_ArrayForEach(child, schema) {
            if (child->string && std::string_view(child->string) == "properties") {
                const cJSON* property = nullptr;
                cJSON_ArrayForEach(property, child) {
                    if (!property->string || !extract(property, member(instance, property->string), reachable, seen, headers)) return false;
                }
            } else if (cJSON_IsObject(child) || cJSON_IsArray(child)) {
                if (!extract(child, nullptr, false, seen, headers)) return false;
            }
        }
    } else if (cJSON_IsArray(schema)) {
        const cJSON* child = nullptr;
        cJSON_ArrayForEach(child, schema) if (!extract(child, nullptr, false, seen, headers)) return false;
    }
    return true;
}
}
bool schema_headers(const cJSON* schema, const cJSON* arguments,
                    std::vector<std::pair<std::string, std::string>>& headers) {
    std::set<std::string> seen;
    return extract(schema, arguments, true, seen, headers);
}
bool valid_arguments(const cJSON* schema, const cJSON* arguments) {
    for (const auto* key : {"$ref", "allOf", "anyOf", "oneOf", "not", "if", "pattern"})
        if (member(schema, key)) return false;
    const auto type = text(member(schema, "type"));
    if (type == "object") {
        if (!cJSON_IsObject(arguments)) return false;
        const cJSON* required = nullptr;
        cJSON_ArrayForEach(required, member(schema, "required")) {
            if (!cJSON_IsString(required) || !member(arguments, required->valuestring)) return false;
        }
        const cJSON* value = nullptr;
        cJSON_ArrayForEach(value, arguments) {
            const auto* property = member(member(schema, "properties"), value->string);
            if (!property) { if (cJSON_IsFalse(member(schema, "additionalProperties"))) return false; }
            else if (!valid_arguments(property, value)) return false;
        }
        return true;
    }
    if (type != "string" || !cJSON_IsString(arguments)) return false;
    const auto value = text(arguments);
    double bound = 0;
    if (number(member(schema, "minLength"), bound) && static_cast<double>(value.size()) < bound) return false;
    if (number(member(schema, "maxLength"), bound) && static_cast<double>(value.size()) > bound) return false;
    const auto* constant = member(schema, "const");
    if (constant && text(constant) != value) return false;
    const auto* choices = member(schema, "enum");
    if (choices) {
        bool found = false;
        const cJSON* choice = nullptr;
        cJSON_ArrayForEach(choice, choices) if (cJSON_IsString(choice) && text(choice) == value) found = true;
        if (!found) return false;
    }
    return true;
}
}
