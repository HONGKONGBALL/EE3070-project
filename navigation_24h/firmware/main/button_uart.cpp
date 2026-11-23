#include "app_runtime.hpp"
#include "ble_link.hpp"
#include "button_uart_framing.hpp"
#include <array>
#include <cstdint>
#include <limits>
#include <optional>
#include <string>
extern "C" {
#include "driver/gpio.h"
#include "driver/uart.h"
#include "freertos/task.h"
}

namespace app {
namespace {
constexpr auto port = UART_NUM_1;
constexpr auto receive_pin = GPIO_NUM_44;
constexpr int baud_rate = 115200;
constexpr nav::Millis repeat_guard_ms = 125;

void log_button(char key, const char* outcome) {
    std::string message = "{\"type\":\"button_uart\",\"button\":\"K";
    message += key;
    message += "\",\"outcome\":\"";
    message += outcome;
    message += "\"}";
    output_usb(message);
}

void receive_buttons(void*) {
    ButtonLineFramer framer;
    std::array<char, 128> bytes{};
    std::array<std::optional<nav::Millis>, 3> last_press{};
    std::optional<nav::Millis> last_untrusted_log;
    std::uint64_t sequence = 0;
    for (;;) {
        const int count = uart_read_bytes(port, bytes.data(), bytes.size(), pdMS_TO_TICKS(100));
        if (count <= 0) {
            if (count < 0) vTaskDelay(pdMS_TO_TICKS(10));
            continue;
        }
        for (std::size_t index = 0; index < static_cast<std::size_t>(count); ++index) {
            const auto key = framer.feed(bytes[index]);
            if (!key) continue;
            const auto now = clock_ms();
            const auto epoch = ble_link::epoch();
            if (!ble_link::authorized_epoch(epoch)) {
                if (!last_untrusted_log || now - *last_untrusted_log >= repeat_guard_ms) {
                    last_untrusted_log = now;
                    log_button(*key, "NO_TRUSTED_LINK");
                }
                continue;
            }
            auto& last = last_press[static_cast<std::size_t>(*key - '1')];
            if (last && now - *last < repeat_guard_ms) continue;
            if (sequence == std::numeric_limits<std::uint64_t>::max()) continue;
            last = now;
            std::string message = "{\"type\":\"physical_button\",\"button\":\"K";
            message += *key;
            message += "\",\"button_seq\":" + std::to_string(++sequence) + "}";
            const bool sent = ble_link::send(message, true, epoch);
            log_button(*key, sent ? "SENT" : "SEND_FAILED");
        }
        vTaskDelay(1);
    }
}
}

bool start_button_uart() {
    uart_config_t config{};
    config.baud_rate = baud_rate;
    config.data_bits = UART_DATA_8_BITS;
    config.parity = UART_PARITY_DISABLE;
    config.stop_bits = UART_STOP_BITS_1;
    config.flow_ctrl = UART_HW_FLOWCTRL_DISABLE;
    config.source_clk = UART_SCLK_DEFAULT;
    if (uart_param_config(port, &config) != ESP_OK ||
        uart_set_pin(port, UART_PIN_NO_CHANGE, receive_pin, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE) != ESP_OK ||
        gpio_pullup_en(receive_pin) != ESP_OK ||
        uart_driver_install(port, 256, 0, 0, nullptr, 0) != ESP_OK) return false;
    if (xTaskCreate(receive_buttons, "button_uart", 4096, nullptr, 4, nullptr) == pdPASS) return true;
    (void)uart_driver_delete(port);
    return false;
}
}
