#pragma once
#include <cstdint>
using BaseType_t = int;
using TickType_t = std::uint32_t;
inline constexpr BaseType_t pdTRUE = 1;
inline constexpr TickType_t pdMS_TO_TICKS(std::uint32_t value) { return value; }
