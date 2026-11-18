#include "app_runtime.hpp"
#include "coordinates.hpp"
#include "discovery_schedule.hpp"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_sntp.h"
#include "esp_wifi.h"
#include <atomic>
#include <algorithm>
#include <cstring>
#include <ctime>

namespace app {
static QueueHandle_t jobs = nullptr, results = nullptr;
static std::atomic<bool> wifi_ready{false};
static std::atomic<bool> wifi_configured{false};
static std::atomic<unsigned> wifi_epoch{0};
static std::atomic<nav::mcp::ClientStatus> service{nav::mcp::ClientStatus::Configuration};
std::string wifi_state() { return wifi_ready ? "CONNECTED" : wifi_configured ? "CONNECTING" : "CONFIGURATION"; }
std::string mcp_state() {
    const auto observed = service.load();
    return nav::mcp::status_name(!wifi_ready && observed == nav::mcp::ClientStatus::Ok ?
        nav::mcp::ClientStatus::Transport : observed);
}
void initialize_network(QueueHandle_t requests, QueueHandle_t responses) { jobs = requests; results = responses; }
static void wifi_event(void*, esp_event_base_t base, std::int32_t id, void*) {
    if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) wifi_ready = true;
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) { wifi_ready = false; ++wifi_epoch; (void)esp_wifi_connect(); }
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) (void)esp_wifi_connect();
}
static void summary(const nav::mcp::ExchangeSummary& value, void*) {
    Json root(cJSON_CreateObject(), cJSON_Delete);
    cJSON_AddStringToObject(root.get(), "type", "mcp_exchange");
    cJSON_AddNumberToObject(root.get(), "monotonic_ms", clock_ms());
    cJSON_AddStringToObject(root.get(), "service_alias", value.service_alias);
    cJSON_AddStringToObject(root.get(), "method", value.method);
    cJSON_AddStringToObject(root.get(), "request_id", value.request_id);
    cJSON_AddNumberToObject(root.get(), "http_status", value.http_status);
    cJSON_AddNumberToObject(root.get(), "attempt", value.attempt);
    cJSON_AddNumberToObject(root.get(), "duration_ms", value.elapsed_ms);
    cJSON_AddNumberToObject(root.get(), "response_bytes", value.response_bytes);
    cJSON_AddStringToObject(root.get(), "status", nav::mcp::status_name(value.status));
    output(encode(root.get()));
}
static void provider_description(const nav::mcp::Discovery& provider) {
    Json root(cJSON_CreateObject(), cJSON_Delete);
    cJSON_AddStringToObject(root.get(), "type", "mcp_provider");
    cJSON_AddNumberToObject(root.get(), "monotonic_ms", clock_ms());
    cJSON_AddStringToObject(root.get(), "protocol_version", provider.protocol_version.c_str());
    cJSON_AddStringToObject(root.get(), "walking_tool", provider.walking_tool.c_str());
    cJSON_AddNumberToObject(root.get(), "utc_epoch_s", std::time(nullptr));
    output(encode(root.get()));
    for (const auto& entry : {std::pair<const char*, const std::string&>{"input_schema", provider.input_schema_json},
                             {"output_schema", provider.output_schema_json}}) {
        const auto& text = entry.second;
        for (std::size_t offset = 0; offset < text.size(); offset += 256) {
            constexpr char digits[] = "0123456789abcdef";
            std::string hex;
            for (std::size_t index = offset; index < std::min(offset + 256, text.size()); ++index) {
                const auto byte = static_cast<unsigned char>(text[index]);
                hex += digits[byte >> 4]; hex += digits[byte & 15];
            }
            Json chunk(cJSON_CreateObject(), cJSON_Delete);
            cJSON_AddStringToObject(chunk.get(), "type", "mcp_schema");
            cJSON_AddStringToObject(chunk.get(), "field", entry.first);
            cJSON_AddNumberToObject(chunk.get(), "offset", offset);
            cJSON_AddNumberToObject(chunk.get(), "total_bytes", text.size());
            cJSON_AddStringToObject(chunk.get(), "encoding", "hex");
            cJSON_AddStringToObject(chunk.get(), "data", hex.c_str());
            output(encode(chunk.get()));
        }
    }
}
static void coordinate_evidence(const nav::RequestToken& token, const Settings& local,
                                const nav::coordinates::TaggedPoint& origin,
                                const nav::coordinates::TaggedPoint& destination,
                                const nav::coordinates::NormalizedRoute& route) {
    Json root(cJSON_CreateObject(), cJSON_Delete);
    cJSON_AddStringToObject(root.get(), "type", "coordinate_boundary");
    cJSON_AddNumberToObject(root.get(), "monotonic_ms", clock_ms());
    cJSON_AddStringToObject(root.get(), "request_id", std::to_string(token.request_id).c_str());
    cJSON_AddStringToObject(root.get(), "session_id", token.session_id.c_str());
    cJSON_AddStringToObject(root.get(), "control_reference", local.coordinate_control_reference.c_str());
    cJSON_AddStringToObject(root.get(), "policy", local.coordinate_policy.c_str());
    cJSON_AddStringToObject(root.get(), "source_frame", "WGS84");
    cJSON_AddStringToObject(root.get(), "provider_frame", local.frame.c_str());
    cJSON_AddStringToObject(root.get(), "normalized_frame", route.route.coord_frame.c_str());
    cJSON_AddNumberToObject(root.get(), "origin_conversion_count", origin.conversion_count);
    cJSON_AddNumberToObject(root.get(), "destination_conversion_count", destination.conversion_count);
    cJSON_AddNumberToObject(root.get(), "route_point_count", route.route.points.size());
    cJSON_AddNumberToObject(root.get(), "route_point_conversion_count", route.point_conversion_counts.empty() ? 0 : route.point_conversion_counts.front());
    output(encode(root.get()));
}
void network_task(void*) {
    const auto local = settings();
    if (!local.ssid.empty() && local.ssid.size() <= 32 && local.password.size() <= 64) {
        wifi_configured = true;
        ESP_ERROR_CHECK(esp_netif_init());
        ESP_ERROR_CHECK(esp_event_loop_create_default());
        (void)esp_netif_create_default_wifi_sta();
        wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
        ESP_ERROR_CHECK(esp_wifi_init(&init));
        ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event, nullptr));
        ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event, nullptr));
        wifi_config_t configuration{};
        std::memcpy(configuration.sta.ssid, local.ssid.data(), local.ssid.size());
        std::memcpy(configuration.sta.password, local.password.data(), local.password.size());
        ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
        ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &configuration));
        ESP_ERROR_CHECK(esp_wifi_start());
        esp_sntp_setoperatingmode(SNTP_OPMODE_POLL);
        esp_sntp_setservername(0, "pool.ntp.org"); esp_sntp_init();
    }
    nav::mcp::ClientConfig config{local.endpoint, local.protocol, local.walking_tool, "amap-walking"};
    nav::mcp::EspHttpTransport transport(config, summary);
    nav::mcp::Client client(config, transport);
    const nav::coordinates::Boundary boundary(nav::coordinates::parse_policy(local.coordinate_policy),
        nav::coordinates::parse_frame(local.frame), local.coordinate_control_reference);
    nav::mcp::DiscoverySchedule discovery(nav::mcp::amap_configuration_ready(local.endpoint));
    nav::mcp::RouteRetrySchedule route_retry;
    for (;;) {
        if (discovery.set_link(wifi_ready.load(), wifi_epoch.load())) {
            client.reset();
            route_retry.reset();
            service = discovery.status();
        }
        if (discovery.due(clock_ms())) {
            const auto operation_epoch = wifi_epoch.load();
            const auto observed_status = client.discover();
            if (discovery.set_link(wifi_ready.load(), wifi_epoch.load())) {
                client.reset(); route_retry.reset();
            }
            if (discovery.finish(observed_status, clock_ms(), operation_epoch)) {
                if (discovery.ready()) {
                    route_retry.reset(); provider_description(client.discovery());
                }
            }
            service = discovery.status();
        }
        nav::RequestToken* raw = nullptr;
        if (xQueueReceive(jobs, &raw, pdMS_TO_TICKS(1000)) != pdTRUE) continue;
        std::unique_ptr<nav::RequestToken> job(raw);
        auto result = std::make_unique<NetworkResult>();
        result->reply.token = *job;
        nav::coordinates::TaggedPoint origin, destination;
        auto coordinates = boundary.to_provider({job->origin, nav::coordinates::Frame::Wgs84, 0}, origin);
        if (coordinates == nav::coordinates::Status::Ok)
            coordinates = boundary.to_provider({job->destination.position, nav::coordinates::Frame::Wgs84, 0}, destination);
        if (!wifi_ready) result->reply.status = nav::mcp::ClientStatus::Transport;
        else if (coordinates != nav::coordinates::Status::Ok) {
            result->reply.status = nav::mcp::ClientStatus::Configuration;
            result->failure_reason = nav::coordinates::name(coordinates);
            diagnostic("fault", result->failure_reason);
        } else if (!discovery.ready() || !route_retry.due(clock_ms())) {
            result->reply.status = nav::mcp::RouteRetrySchedule::deferred_status(service.load());
            diagnostic("status", "MCP_RETRY_BACKOFF");
        } else {
            result->reply = client.walking(*job, origin.position, destination.position);
            if (result->reply.status == nav::mcp::ClientStatus::Ok) {
                nav::coordinates::NormalizedRoute normalized;
                coordinates = boundary.normalize_route(result->reply.candidate.route, normalized);
                if (coordinates == nav::coordinates::Status::Ok) {
                    coordinate_evidence(*job, local, origin, destination, normalized);
                    result->reply.candidate.route = std::move(normalized.route);
                } else {
                    result->reply.status = nav::mcp::ClientStatus::Configuration;
                    result->failure_reason = nav::coordinates::name(coordinates);
                    diagnostic("fault", result->failure_reason);
                }
            }
            route_retry.finish(result->reply.status, clock_ms());
            service = route_retry.status();
            if (result->reply.status != nav::mcp::ClientStatus::Ok) {
                if (result->reply.status == nav::mcp::ClientStatus::Protocol ||
                    result->reply.status == nav::mcp::ClientStatus::Configuration) {
                    client.reset(); discovery.invalidate(result->reply.status, clock_ms());
                }
            }
        }
        auto* pointer = result.release();
        if (xQueueSend(results, &pointer, pdMS_TO_TICKS(1000)) != pdTRUE) { delete pointer; diagnostic("fault", "RESULT_QUEUE_CAPACITY"); }
    }
}
}
