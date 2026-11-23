#pragma once
#include <cstdint>
using TickType_t = std::uint32_t;
inline constexpr TickType_t pdMS_TO_TICKS(std::uint32_t value) { return value; }
