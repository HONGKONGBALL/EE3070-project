#include "discovery_schedule.hpp"
#include <cstdlib>
#include <iostream>

using namespace nav::mcp;
static void require(bool value, const char* description) {
    if (!value) { std::cerr << description << '\n'; std::exit(1); }
}
int main() {
    require(amap_configuration_ready("https://mcp.amap.com/mcp?key=synthetic"), "configured original MCP endpoint");
    require(!amap_configuration_ready("https://mcp.amap.com/mcp"), "credential configuration gate");
    require(!amap_configuration_ready("https://mcp.amap.com.evil/mcp?key=synthetic"), "endpoint origin gate");
    DiscoverySchedule schedule(true);
    require(!schedule.due(0), "disconnected discovery waits for link");
    require(schedule.set_link(true, 0) && schedule.due(0), "connected startup discovery needs no route inputs");
    require(schedule.finish(ClientStatus::Authentication, 10, 0), "authentication result recorded");
    require(schedule.status() == ClientStatus::Authentication && !schedule.due(2009) && schedule.due(2010),
            "authentication visibility and backoff");
    require(schedule.finish(ClientStatus::Quota, 2010, 0), "quota result recorded");
    require(schedule.status() == ClientStatus::Quota && !schedule.due(6009) && schedule.due(6010),
            "quota visibility and backoff");
    require(schedule.finish(ClientStatus::Ok, 6010, 0) && schedule.ready() && !schedule.due(100000),
            "successful discovery remains ready");
    require(schedule.set_link(false, 1) && !schedule.ready() && schedule.status() == ClientStatus::Transport,
            "disconnect invalidates successful discovery");
    require(!schedule.finish(ClientStatus::Ok, 7000, 0), "late discovery result checks Wi-Fi epoch");
    require(schedule.set_link(true, 1) && schedule.due(7000), "reconnect creates fresh discovery opportunity");
    require(schedule.finish(ClientStatus::Tls, 7000, 1), "TLS verification failure recorded");
    require(schedule.set_link(false, 2) && schedule.status() == ClientStatus::Tls,
            "disconnect preserves actual service failure");
    require(schedule.set_link(true, 2), "second reconnect");
    nav::Millis now = 10000;
    for (unsigned failure = 0; failure < 20; ++failure) {
        require(schedule.finish(ClientStatus::Timeout, now, 2), "bounded retry result");
        const auto deadline = schedule.next_attempt_at();
        require(deadline > now && deadline - now <= 30000 && !schedule.due(deadline - 1) && schedule.due(deadline),
                "backoff deadline remains bounded");
        now = deadline;
    }
    DiscoverySchedule configuration(false);
    require(configuration.set_link(true, 0) && !configuration.due(0) &&
        configuration.status() == ClientStatus::Configuration, "startup configuration readiness");
    RouteRetrySchedule routes;
    routes.finish(ClientStatus::Timeout, 10000);
    require(!routes.due(10001) && routes.status() == ClientStatus::Timeout,
            "route failure retains its retry window and final status");
    routes.reset();
    require(routes.due(10001), "new link or successful discovery renews route submission eligibility");
    require(RouteRetrySchedule::deferred_status(ClientStatus::Ok) == ClientStatus::Transport,
            "deferred ready service returns explicit transport unavailability");
    require(RouteRetrySchedule::deferred_status(ClientStatus::Authentication) == ClientStatus::Authentication,
            "deferred request preserves actual authentication result");
    routes.finish(ClientStatus::Configuration, 11000);
    require(routes.status() == ClientStatus::Configuration && !routes.due(11001) &&
        RouteRetrySchedule::deferred_status(routes.status()) == ClientStatus::Configuration,
        "coordinate normalization failure is the published final route outcome");
    routes.finish(ClientStatus::Ok, 12000);
    require(routes.status() == ClientStatus::Ok && routes.due(12000),
            "successful route clears its retry window");
    std::cout << "startup discovery schedule passed\n";
}
