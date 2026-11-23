#include "app_runtime.hpp"
#include "ble_link.hpp"
#include "usb_framing.hpp"
#include "driver/usb_serial_jtag.h"
#include "esp_heap_caps.h"
#include "esp_system.h"
#include "esp_app_desc.h"
#include "esp_random.h"
#include "nvs_flash.h"
#include <array>
#include <cstring>
#include <limits>

namespace app {
static QueueHandle_t inputs = nullptr;
static QueueHandle_t pairing_notices = nullptr;
static void receive_pairing(const ble_link::PairingStatus& status, void*) {
    auto notice = std::make_unique<ble_link::PairingStatus>(status);
    auto* pointer = notice.get();
    if (xQueueSend(pairing_notices, &pointer, 0) == pdTRUE) (void)notice.release();
}
static bool receive(const char* bytes, std::size_t length, void*) {
    if (length == 0 || length > 512) return false;
    Input input;
    std::memcpy(input.bytes, bytes, length); input.length = length; input.received_at = clock_ms();
    input.ble_epoch = ble_link::epoch();
    return xQueueSend(inputs, &input, 0) == pdTRUE;
}
static void usb(void*) {
    UsbLineFramer framer;
    for (;;) {
        char byte = 0;
        if (usb_serial_jtag_read_bytes(&byte, 1, pdMS_TO_TICKS(100)) != 1) continue;
        const auto result = framer.feed(byte);
        if (result == UsbFrameResult::Capacity) diagnostic("fault", "FRAME_CAPACITY");
        if (result != UsbFrameResult::Complete) continue;
        const auto frame = framer.message();
        Input input;
        std::memcpy(input.bytes, frame.data(), frame.size());
        input.length = frame.size(); input.usb = true; input.received_at = clock_ms();
        (void)xQueueSend(inputs, &input, pdMS_TO_TICKS(20));
    }
}
static void resources() {
    static const auto boot_id = std::to_string(esp_random());
    Json log(cJSON_CreateObject(), cJSON_Delete);
    cJSON_AddStringToObject(log.get(), "type", "resources");
    cJSON_AddNumberToObject(log.get(), "monotonic_ms", clock_ms());
    cJSON_AddStringToObject(log.get(), "boot_id", boot_id.c_str());
    cJSON_AddStringToObject(log.get(), "build_id", esp_app_get_description()->version);
    cJSON_AddStringToObject(log.get(), "config_version", "navigation-1");
    cJSON_AddNumberToObject(log.get(), "free_internal", heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
    cJSON_AddNumberToObject(log.get(), "largest_internal", heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
    cJSON_AddNumberToObject(log.get(), "free_psram", heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
    cJSON_AddNumberToObject(log.get(), "reset_reason", esp_reset_reason());
    cJSON_AddNumberToObject(log.get(), "main_stack_free", uxTaskGetStackHighWaterMark(nullptr));
    output(encode(log.get()));
}
void loop() {
    auto owned_engine = std::make_unique<nav::Engine>();
    auto& engine = *owned_engine;
    engine.set_integration(settings().integrated, clock_ms());
    const auto requests = xQueueCreate(1, sizeof(nav::RequestToken*));
    const auto results = xQueueCreate(4, sizeof(NetworkResult*));
    inputs = xQueueCreate(12, sizeof(Input));
    pairing_notices = xQueueCreate(8, sizeof(ble_link::PairingStatus*));
    if (!inputs || !requests || !results || !pairing_notices) abort();
    ble_link::observe_pairing(receive_pairing, nullptr);
    initialize_network(requests, results);
    ble_link::start(receive, nullptr);
    if (!start_button_uart()) diagnostic("fault", "BUTTON_UART_INIT");
    (void)xTaskCreate(usb, "usb_input", 4096, nullptr, 5, nullptr);
    (void)xTaskCreate(network_task, "mcp_network", 12288, nullptr, 3, nullptr);
    diagnostic("boot", "NAVIGATION_READY");
    std::uint64_t submitted = 0;
    nav::Millis last_status = 0, last_resource = 0;
    for (;;) {
        Input input;
        if (xQueueReceive(inputs, &input, pdMS_TO_TICKS(20)) == pdTRUE && !handle(engine, input)) diagnostic("fault", "INVALID_MESSAGE");
        ble_link::PairingStatus* notice = nullptr;
        while (xQueueReceive(pairing_notices, &notice, 0) == pdTRUE) {
            std::unique_ptr<ble_link::PairingStatus> owned(notice); pairing_status(*owned);
        }
        engine.set_link(ble_link::connected(), clock_ms());
        engine.tick(clock_ms());
        NetworkResult* raw = nullptr;
        while (xQueueReceive(results, &raw, 0) == pdTRUE) {
            std::unique_ptr<NetworkResult> result(raw);
            if (result->reply.status == nav::mcp::ClientStatus::Ok) {
                const bool committed = engine.commit_route(result->reply.token, result->reply.candidate.route, clock_ms());
                diagnostic("route_commit", committed ? "COMMITTED" : "STALE_CANDIDATE");
            } else {
                const auto* reason = result->failure_reason ? result->failure_reason :
                    nav::mcp::status_name(result->reply.status);
                engine.fail_request(result->reply.token, reason, clock_ms());
            }
        }
        const auto request = engine.pending_request();
        if (request && request->request_id != submitted) {
            auto job = std::make_unique<nav::RequestToken>(*request);
            auto* pointer = job.get();
            if (xQueueSend(requests, &pointer, 0) == pdTRUE) { submitted = request->request_id; (void)job.release(); }
        }
        for (const auto& item : engine.drain_events(clock_ms())) event(item, engine.snapshot());
        if (clock_ms() - last_status >= 1000) { status(engine); last_status = clock_ms(); }
        if (clock_ms() - last_resource >= 5000) { resources(); last_resource = clock_ms(); }
    }
}
}
extern "C" void app_main() {
    const auto nvs = nvs_flash_init();
    if (nvs != ESP_OK) { ESP_ERROR_CHECK(nvs); return; }
    usb_serial_jtag_driver_config_t usb{};
    usb.rx_buffer_size = 2048; usb.tx_buffer_size = 4096;
    ESP_ERROR_CHECK(usb_serial_jtag_driver_install(&usb));
    app::loop();
}
