#include "route_parser.hpp"
#include "json_helpers.hpp"

namespace nav::mcp {
const char* status_name(ParseStatus status) {
    switch (status) {
    case ParseStatus::Ok: return "OK";
    case ParseStatus::InvalidJson: return "INVALID_JSON";
    case ParseStatus::InvalidRpc: return "INVALID_RPC";
    case ParseStatus::IdMismatch: return "REQUEST_ID_MISMATCH";
    case ParseStatus::RouteError: return "ROUTE_ERROR";
    case ParseStatus::Capacity: return "ROUTE_CAPACITY";
    case ParseStatus::InvalidRoute: return "INVALID_ROUTE";
    case ParseStatus::ServiceQuota: return "SERVICE_QUOTA";
    case ParseStatus::GeometryRequired: return "ROUTE_GEOMETRY_REQUIRED";
    }
    return "INVALID_STATUS";
}
RpcStream::RpcStream(Format format, std::string request_id)
    : format_(format), request_id_(std::move(request_id)) {}
ParseStatus RpcStream::accept_response(std::string_view message) {
    auto json = detail::parse(message);
    if (!json) return ParseStatus::InvalidJson;
    if (!cJSON_IsObject(json.get()) || detail::text(detail::member(json.get(), "jsonrpc")) != "2.0")
        return ParseStatus::InvalidRpc;
    const auto* id = detail::member(json.get(), "id");
    if (!id && cJSON_IsString(detail::member(json.get(), "method"))) return ParseStatus::Ok;
    if (!id) return ParseStatus::InvalidRpc;
    if (!detail::id_matches(id, request_id_)) return ParseStatus::IdMismatch;
    const bool has_result = cJSON_IsObject(detail::member(json.get(), "result"));
    const bool has_error = cJSON_IsObject(detail::member(json.get(), "error"));
    if (has_result == has_error || complete_) return ParseStatus::InvalidRpc;
    response_.assign(message);
    complete_ = true;
    return ParseStatus::Ok;
}
ParseStatus RpcStream::event_complete() {
    if (event_.empty()) return ParseStatus::Ok;
    const auto result = accept_response(event_);
    event_.clear();
    return result;
}
ParseStatus RpcStream::line_complete() {
    if (!line_.empty() && line_.back() == '\r') line_.pop_back();
    if (line_.empty()) return event_complete();
    if (line_.compare(0, 5, "data:") == 0) {
        auto data = std::string_view(line_).substr(5);
        if (!data.empty() && data.front() == ' ') data.remove_prefix(1);
        if (!event_.empty()) event_ += '\n';
        event_.append(data);
    }
    line_.clear();
    return ParseStatus::Ok;
}
ParseStatus RpcStream::feed(std::string_view bytes) {
    if (status_ != ParseStatus::Ok) return status_;
    if (bytes.size() > kResponseCapacity - received_bytes_) return status_ = ParseStatus::Capacity;
    received_bytes_ += bytes.size();
    if (format_ == Format::Json) { line_.append(bytes); return status_; }
    for (const auto byte : bytes) {
        if (byte == '\n' && previous_cr_) { previous_cr_ = false; continue; }
        previous_cr_ = byte == '\r';
        if (byte == '\n' || byte == '\r') {
            status_ = line_complete();
            if (status_ != ParseStatus::Ok) return status_;
        } else line_ += byte;
    }
    return status_;
}
ParseStatus RpcStream::finish() {
    if (status_ != ParseStatus::Ok) return status_;
    if (format_ == Format::Json) status_ = accept_response(line_);
    else {
        if (!line_.empty()) status_ = line_complete();
        if (status_ == ParseStatus::Ok) status_ = event_complete();
    }
    if (status_ == ParseStatus::Ok && !complete_) status_ = ParseStatus::InvalidRpc;
    line_.clear(); event_.clear();
    return status_;
}
}
