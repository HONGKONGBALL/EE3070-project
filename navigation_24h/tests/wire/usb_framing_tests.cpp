#include "usb_framing.hpp"
#include <cstdlib>
#include <iostream>
#include <string>
#include <string_view>

using app::UsbFrameResult;
static void require(bool value, const char* description) {
    if (!value) { std::cerr << description << '\n'; std::exit(1); }
}
int main() {
    app::UsbLineFramer framer;
    for (unsigned index = 0; index < 512; ++index)
        require(framer.feed('a') == UsbFrameResult::Waiting, "exact capacity accumulation");
    require(framer.feed('\n') == UsbFrameResult::Complete && framer.message() == std::string(512, 'a'),
            "512-byte frame delivery");
    unsigned faults = 0, complete = 0;
    const auto oversized = std::string(513, 'b') + "\r\n";
    for (const auto byte : oversized) {
        const auto result = framer.feed(byte);
        faults += result == UsbFrameResult::Capacity;
        complete += result == UsbFrameResult::Complete;
    }
    require(faults == 1 && complete == 0 && framer.message().empty(), "one capacity observation per oversized frame");
    const std::string_view normal = R"({"type":"command","command":"get_status"})";
    for (const auto byte : normal) require(framer.feed(byte) == UsbFrameResult::Waiting, "recovery accumulation");
    require(framer.feed('\r') == UsbFrameResult::Waiting && framer.feed('\n') == UsbFrameResult::Complete &&
            framer.message() == normal, "next frame recovery");
    faults = 0;
    for (unsigned frame = 0; frame < 3; ++frame) {
        for (unsigned index = 0; index < 4096; ++index)
            require(framer.feed('x') == UsbFrameResult::Waiting, "discard interval retains bounded buffer");
        faults += framer.feed('\n') == UsbFrameResult::Capacity;
    }
    require(faults == 3, "consecutive oversized frame accounting");
    require(framer.feed('\n') == UsbFrameResult::Waiting, "empty delimiter");
    require(framer.feed('{') == UsbFrameResult::Waiting && framer.feed('\n') == UsbFrameResult::Complete &&
            framer.message() == "{", "JSON parsing receives bounded truncated content");
    for (const auto byte : normal) (void)framer.feed(byte);
    require(framer.feed('\n') == UsbFrameResult::Complete && framer.message() == normal, "post-truncation recovery");
    std::cout << "USB frame capacity and recovery passed\n";
}
