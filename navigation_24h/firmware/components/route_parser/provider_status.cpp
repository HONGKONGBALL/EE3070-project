#include "provider_status.hpp"
#include "json_helpers.hpp"
#include <limits>

namespace nav::mcp {
namespace detail {
ServiceEnvelope provider_envelope(const cJSON* data) {
    ServiceEnvelope envelope;
    double code = 0, status = 0;
    if (!number(member(data, "infocode"), code) || code < 0 ||
        code > std::numeric_limits<unsigned>::max() || std::floor(code) != code ||
        !number(member(data, "status"), status) || (status != 0 && status != 1)) return envelope;
    envelope.infocode = static_cast<unsigned>(code);
    envelope.info = std::string(text(member(data, "info")));
    if (envelope.info.empty() || envelope.info.size() > 256) return envelope;
    if (status == 1) {
        if (envelope.infocode == 10000) envelope.state = ServiceState::Ready;
    } else envelope.state = envelope.infocode == 10045 || envelope.infocode == 10021 ?
        ServiceState::Quota : ServiceState::Error;
    return envelope;
}
ParseStatus provider_parse_status(const cJSON* data) {
    if (!member(data, "status")) return ParseStatus::Ok;
    switch (provider_envelope(data).state) {
    case ServiceState::Ready: return ParseStatus::Ok;
    case ServiceState::Quota: return ParseStatus::ServiceQuota;
    case ServiceState::Error: return ParseStatus::RouteError;
    case ServiceState::InvalidEnvelope: return ParseStatus::InvalidRoute;
    }
    return ParseStatus::InvalidRoute;
}
}
ServiceEnvelope classify_provider_response(std::string_view response) {
    if (response.size() > kResponseCapacity) return {};
    const auto document = detail::parse(response);
    if (!document || !cJSON_IsObject(document.get())) return {};
    return detail::provider_envelope(document.get());
}
}
