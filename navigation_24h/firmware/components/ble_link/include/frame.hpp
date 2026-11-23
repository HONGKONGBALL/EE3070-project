#pragma once
#include <array>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace link_frame {
enum class Result { Partial, Complete, Invalid, Expired };
std::vector<std::vector<std::uint8_t>> fragment(const std::string& payload,
    std::uint16_t message_id, std::size_t maximum_write);
class Assembler {
public:
    Result feed(const std::uint8_t* bytes, std::size_t length, std::uint64_t now_ms);
    std::string message() const { return {buffer_.data(), received_}; }
    void reset();
private:
    std::array<char, 512> buffer_{};
    std::size_t received_ = 0;
    std::uint16_t total_ = 0;
    std::uint16_t message_id_ = 0;
    std::uint64_t started_at_ = 0;
    bool active_ = false;
};
}
