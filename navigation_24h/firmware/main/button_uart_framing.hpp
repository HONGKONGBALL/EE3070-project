#pragma once
#include <array>
#include <cstddef>
#include <optional>

namespace app {
class ButtonLineFramer {
public:
    std::optional<char> feed(char byte) {
        if (byte != '\n') {
            if (length_ < bytes_.size()) bytes_[length_++] = byte;
            else overflow_ = true;
            return std::nullopt;
        }
        const bool valid = !overflow_ && length_ == bytes_.size() &&
            bytes_[0] == 'K' && bytes_[1] >= '1' && bytes_[1] <= '3';
        length_ = 0;
        overflow_ = false;
        return valid ? std::optional<char>{bytes_[1]} : std::nullopt;
    }

private:
    std::array<char, 2> bytes_{};
    std::size_t length_ = 0;
    bool overflow_ = false;
};
}
