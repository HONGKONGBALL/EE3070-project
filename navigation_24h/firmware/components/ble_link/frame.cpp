#include "frame.hpp"
#include <algorithm>
#include <cstring>

namespace link_frame {
static std::uint16_t read16(const std::uint8_t* data) {
    return static_cast<std::uint16_t>(data[0] | (data[1] << 8));
}
static void append16(std::vector<std::uint8_t>& data, std::uint16_t number) {
    data.push_back(static_cast<std::uint8_t>(number & 255));
    data.push_back(static_cast<std::uint8_t>(number >> 8));
}
std::vector<std::vector<std::uint8_t>> fragment(const std::string& payload,
    std::uint16_t id, std::size_t maximum_write) {
    std::vector<std::vector<std::uint8_t>> output;
    if (payload.empty() || payload.size() > 512 || maximum_write <= 10) return output;
    maximum_write = std::min<std::size_t>(maximum_write, 512);
    for (std::size_t offset = 0; offset < payload.size(); offset += maximum_write - 10) {
        std::vector<std::uint8_t> part{0x4e, 0x31, 1, 0};
        append16(part, id);
        append16(part, static_cast<std::uint16_t>(payload.size()));
        append16(part, static_cast<std::uint16_t>(offset));
        const auto amount = std::min(maximum_write - 10, payload.size() - offset);
        part.insert(part.end(), payload.begin() + offset, payload.begin() + offset + amount);
        output.push_back(std::move(part));
    }
    return output;
}
void Assembler::reset() { received_ = 0; total_ = 0; active_ = false; }
Result Assembler::feed(const std::uint8_t* data, std::size_t length, std::uint64_t now) {
    if (data == nullptr || length <= 10 || data[0] != 0x4e || data[1] != 0x31 || data[2] != 1 || data[3] != 0) {
        reset(); return Result::Invalid;
    }
    const auto id = read16(data + 4), total = read16(data + 6), offset = read16(data + 8);
    if (total == 0 || total > buffer_.size() || length - 10 > total || offset + length - 10 > total) {
        reset(); return Result::Invalid;
    }
    if (active_ && (now < started_at_ || now - started_at_ >= 1000)) {
        reset();
        if (offset != 0) return Result::Expired;
    }
    if (offset == 0) { reset(); active_ = true; total_ = total; message_id_ = id; started_at_ = now; }
    if (!active_ || id != message_id_ || total != total_ || offset != received_) {
        reset(); return Result::Invalid;
    }
    std::memcpy(buffer_.data() + received_, data + 10, length - 10);
    received_ += length - 10;
    if (received_ == total_) { active_ = false; return Result::Complete; }
    return Result::Partial;
}
}
