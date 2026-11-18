#pragma once
#include <array>
#include <cstddef>
#include <string_view>

namespace app {
enum class UsbFrameResult { Waiting, Complete, Capacity };

class UsbLineFramer {
public:
    UsbFrameResult feed(char byte) {
        if (byte == '\n') {
            complete_length_ = overflow_ ? 0 : length_;
            const auto result = overflow_ ? UsbFrameResult::Capacity :
                (length_ > 0 ? UsbFrameResult::Complete : UsbFrameResult::Waiting);
            length_ = 0;
            overflow_ = false;
            return result;
        }
        complete_length_ = 0;
        if (byte != '\r' && !overflow_) {
            if (length_ < bytes_.size()) bytes_[length_++] = byte;
            else overflow_ = true;
        }
        return UsbFrameResult::Waiting;
    }

    std::string_view message() const { return {bytes_.data(), complete_length_}; }

private:
    std::array<char, 512> bytes_{};
    std::size_t length_ = 0;
    std::size_t complete_length_ = 0;
    bool overflow_ = false;
};
}
