#include "wire_mocks.hpp"
#include "test_support.hpp"
#include <cstring>
#include <iostream>

namespace {
app::Input input(const std::string& bytes, bool usb = true, std::uint32_t epoch = 0) {
    test::require(bytes.size() <= 512, "pairing fixture fits frame");
    app::Input result; std::memcpy(result.bytes, bytes.data(), bytes.size());
    result.length = bytes.size(); result.usb = usb; result.ble_epoch = epoch;
    result.received_at = wire_test::now; return result;
}
const std::string open = R"({"type":"pairing","action":"open","command_id":"usb-open"})";
const std::string bind = R"({"type":"command","command":"bind","command_id":"phone-bind","session_id":"","generation":0})";
}
int main() {
    try {
        wire_test::reset(); nav::Engine idle;
        test::require(app::handle(idle, input(open)), "USB idle pairing open queued");
        test::require(wire_test::pairing_requests.size() == 1 &&
                      wire_test::pairing_requests[0].action == ble_link::PairingAction::Open,
                      "open reaches host queue boundary");
        test::require(!app::handle(idle, input(open, false)), "BLE pairing control disposition");
        nav::Engine active;
        test::require(active.start("nav", "phone", 1, {"entry", {114, 22}}, 1000).accepted, "session starts");
        test::require(!app::handle(active, input(open)), "active session pairing disposition");
        test::require(wire_test::pairing_requests.size() == 1, "USB idle gate retains one submitted command");
        const auto confirmation = input(R"({"type":"pairing","action":"confirm","command_id":"usb-confirm","window_id":"boot-a-1","connection_epoch":7,"number":123456})");
        test::require(app::handle(idle, confirmation), "typed USB numeric decision queued");
        const auto& submitted = wire_test::pairing_requests.back();
        test::require(submitted.window_id == "boot-a-1" && submitted.connection_epoch == 7 && submitted.number == 123456,
                      "confirmation identity copied to host request");
        test::require(!app::handle(idle, input(R"({"type":"pairing","action":"confirm","command_id":"bad","window_id":"boot-a-1","connection_epoch":7,"number":1000000})")),
                      "six-digit number boundary retained");
        wire_test::connection_epoch = 7;
        test::require(!app::handle(idle, input(bind, false, 6)), "queued previous epoch disposition");
        test::require(app::handle(idle, input(bind, false, 7)), "current approved peer bind handled");
        const auto& ack = wire_test::outputs.back();
        auto parsed = app::parse(ack.data(), ack.size());
        test::require(cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(parsed.get(), "accepted")) &&
                      app::string_field(parsed.get(), "reason") == "BOUND", "current peer receives correlated BOUND");
        wire_test::link_authorized = false;
        test::require(!app::handle(idle, input(bind, false, 7)), "pending security peer disposition");
        wire_test::reset();
        ble_link::PairingStatus notice;
        notice.snapshot.state = ble_link::pairing::State::Comparing;
        notice.snapshot.number = 123456; notice.command_id = "usb-open"; notice.reason = "PAIRING_COMPARE_NUMBER";
        app::pairing_status(notice);
        test::require(wire_test::outputs.size() == 1 && wire_test::ble_outputs == 0,
                      "pairing status uses USB-directed output");
        std::cout << "pairing USB/Idle, identity, epoch and output fixtures passed\n";
    } catch (const test::Failure& failure) {
        std::cerr << failure.what() << '\n'; return 1;
    }
    return 0;
}
