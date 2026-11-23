#include "frame.hpp"
#include <cstdlib>
#include <iostream>
#include <string>

void require(bool value, const char* name) {
    if (!value) { std::cerr << name << '\n'; std::exit(1); }
}

int main() {
    // Given a UTF-8 message and the minimum ATT payload.
    const std::string payload = "{\"type\":\"command\",\"command\":\"get_status\"}";
    auto fragments = link_frame::fragment(payload, 7, 20);
    link_frame::Assembler assembler;
    // When fragments arrive in order.
    for (std::size_t index = 0; index < fragments.size(); ++index) {
        const auto result = assembler.feed(fragments[index].data(), fragments[index].size(), index * 10);
        // Then only the complete frame is emitted.
        require(result == (index + 1 == fragments.size() ? link_frame::Result::Complete : link_frame::Result::Partial), "TC10 roundtrip");
    }
    require(assembler.message() == payload, "TC10 payload");
    // Given an assembly that has reached its deadline.
    require(assembler.feed(fragments[0].data(), fragments[0].size(), 100) == link_frame::Result::Partial, "TC53 start");
    // When a later fragment arrives at 1000 ms from the start.
    require(assembler.feed(fragments[1].data(), fragments[1].size(), 1100) == link_frame::Result::Expired, "TC53 deadline");
    // Then a fresh message can use the released slot.
    require(assembler.feed(fragments[0].data(), fragments[0].size(), 1200) == link_frame::Result::Partial, "TC53 reuse");
    require(assembler.feed(fragments[2].data(), fragments[2].size(), 1210) == link_frame::Result::Invalid, "TC53 offset");
    auto maximum = link_frame::fragment(std::string(512, 'x'), 65535, 20);
    for (const auto& part : maximum) { (void)assembler.feed(part.data(), part.size(), 1300); }
    require(assembler.message().size() == 512, "TC53 maximum");
    require(link_frame::fragment(std::string(513, 'x'), 1, 20).empty(), "TC53 capacity");
    require(link_frame::fragment(payload, 1, 10).empty(), "TC10 ATT header capacity");
    require(assembler.feed(nullptr, 11, 1400) == link_frame::Result::Invalid,
            "TC53 invalid buffer boundary");
    auto invalid_flags = fragments[0]; invalid_flags[3] = 1;
    require(assembler.feed(invalid_flags.data(), invalid_flags.size(), 1500) ==
            link_frame::Result::Invalid, "TC53 reserved header flags");
    const std::string unicode = "{\"目的地\":\"入口\"}";
    auto unicode_parts = link_frame::fragment(unicode, 0, 11);
    for (const auto& part : unicode_parts) {
        require(part.size() <= 11, "TC10 negotiated write length");
        static_cast<void>(assembler.feed(part.data(), part.size(), 1600));
    }
    require(assembler.message() == unicode, "TC10 UTF8 split roundtrip");
    std::cout << "{\"suite\":\"ble_framing\",\"cases\":[\"TC10\",\"TC53\"],\"result\":\"passed\"}\n";
}
