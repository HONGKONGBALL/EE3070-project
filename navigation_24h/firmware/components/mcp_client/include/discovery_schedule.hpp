#pragma once
#include "mcp_client.hpp"
#include <algorithm>
#include <limits>

namespace nav::mcp {
inline bool amap_configuration_ready(std::string_view endpoint) {
    constexpr std::string_view base = "https://mcp.amap.com/mcp";
    if (endpoint.size() <= base.size() || endpoint.substr(0, base.size()) != base ||
        endpoint[base.size()] != '?' || endpoint.find_first_of("\r\n#") != std::string_view::npos)
        return false;
    auto query = endpoint.substr(base.size() + 1);
    while (!query.empty()) {
        const auto separator = query.find('&');
        const auto field = query.substr(0, separator);
        if (field.substr(0, 4) == "key=" && field.size() > 4) return true;
        if (separator == std::string_view::npos) break;
        query.remove_prefix(separator + 1);
    }
    return false;
}
class DiscoverySchedule {
public:
    explicit DiscoverySchedule(bool configured) : configured_(configured) {}
    bool set_link(bool connected, unsigned epoch) {
        if (connected == connected_ && epoch == epoch_) return false;
        connected_ = connected; epoch_ = epoch; ready_ = false;
        failures_ = 0; next_attempt_ = 0;
        if (status_ == ClientStatus::Ok) status_ = ClientStatus::Transport;
        return true;
    }
    bool due(Millis now) const {
        return configured_ && connected_ && !ready_ && now >= next_attempt_;
    }
    bool finish(ClientStatus status, Millis now, unsigned epoch) {
        if (!connected_ || epoch != epoch_) return false;
        status_ = status; ready_ = status == ClientStatus::Ok;
        if (ready_) { failures_ = 0; next_attempt_ = 0; }
        else {
            failures_ = std::min(failures_ + 1, 5U);
            const auto delay = std::min(30000U, 1000U << failures_);
            next_attempt_ = now > std::numeric_limits<Millis>::max() - delay ?
                std::numeric_limits<Millis>::max() : now + delay;
        }
        return true;
    }
    void invalidate(ClientStatus status, Millis now) {
        if (connected_) (void)finish(status, now, epoch_);
        ready_ = false;
    }
    bool ready() const { return connected_ && ready_; }
    ClientStatus status() const { return status_; }
    Millis next_attempt_at() const { return next_attempt_; }
private:
    bool configured_ = false;
    bool connected_ = false;
    bool ready_ = false;
    unsigned epoch_ = 0;
    unsigned failures_ = 0;
    Millis next_attempt_ = 0;
    ClientStatus status_ = ClientStatus::Configuration;
};
class RouteRetrySchedule {
public:
    void reset() { failures_ = 0; next_attempt_ = 0; status_ = ClientStatus::Configuration; }
    bool due(Millis now) const { return now >= next_attempt_; }
    void finish(ClientStatus final_status, Millis now) {
        status_ = final_status;
        if (status_ == ClientStatus::Ok) { failures_ = 0; next_attempt_ = 0; return; }
        failures_ = std::min(failures_ + 1, 5U);
        const auto delay = std::min(30000U, 1000U << failures_);
        next_attempt_ = now > std::numeric_limits<Millis>::max() - delay ?
            std::numeric_limits<Millis>::max() : now + delay;
    }
    ClientStatus status() const { return status_; }
    static ClientStatus deferred_status(ClientStatus observed_service) {
        return observed_service == ClientStatus::Ok ? ClientStatus::Transport : observed_service;
    }
private:
    unsigned failures_ = 0;
    Millis next_attempt_ = 0;
    ClientStatus status_ = ClientStatus::Configuration;
};
}
