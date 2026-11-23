#pragma once
#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>

namespace ble_link {
using Receiver = bool (*)(const char*, std::size_t, void*);
void start(Receiver receiver, void* context);
bool connected();
bool send(const std::string& message, bool event_channel = false,
          std::optional<std::uint32_t> expected_epoch = std::nullopt);
}
