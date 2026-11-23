#include "wire_mocks.hpp"
#include "test_support.hpp"
#include <cstring>
#include <iostream>

namespace {
app::Input input(const std::string& bytes) {
    test::require(bytes.size() <= 512, "wire fixture within frame capacity");
    app::Input result;
    std::memcpy(result.bytes, bytes.data(), bytes.size());
    result.length = bytes.size(); result.received_at = wire_test::now; result.usb = true;
    return result;
}
app::Json output(std::size_t index) {
    const auto& bytes = wire_test::outputs.at(index);
    return app::parse(bytes.c_str(), bytes.size());
}
bool accepted(std::size_t index) {
    auto document = output(index);
    return cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(document.get(), "accepted"));
}
const std::string start = R"({"type":"command","command":"start","command_id":"start-1","session_id":"nav-A","phone_session_id":"phone-A","generation":1,"destination":{"name":"entry","lng":114.001,"lat":22,"coord_frame":"WGS84"}})";
}
int main(int argc, char** argv) {
    const std::string requested = argc == 3 && std::string(argv[1]) == "--case" ? argv[2] : "";
    const std::vector<test::Case> cases{
        {"TC09", "exact_length_json_boundary", [] {
            const char bounded[3]{'{', '}', 'X'};
            test::require(bool(app::parse(bounded, 2)), "exact bytes parse independently of following memory");
            const std::string embedded("{}\0x", 4);
            test::require(!app::parse(embedded.data(), embedded.size()), "embedded NUL rejected");
        }},
        {"TC02", "start_command_retransmission", [] {
            wire_test::reset(); nav::Engine engine;
            test::require(app::handle(engine, input(start)), "first start parsed");
            test::require(app::handle(engine, input(start)), "repeated start parsed");
            test::require(accepted(0) && accepted(1), "same start retains original acknowledgment");
        }},
        {"TC33", "canceled_start_retransmission_preserves_idle", [] {
            wire_test::reset(); nav::Engine engine;
            test::require(app::handle(engine, input(start)), "start parsed");
            test::require(app::handle(engine, input(R"({"type":"command","command":"cancel","command_id":"cancel-1","session_id":"nav-A","generation":1})")),
                          "cancel parsed");
            test::require(app::handle(engine, input(start)), "late start retry parsed");
            test::state(engine, nav::State::Idle);
            test::require(engine.snapshot().generation == 2, "canceled generation preserved");
        }},
        {"TC09", "permission_lease_has_numeric_type", [] {
            wire_test::reset(); nav::Engine engine; test::activate(engine, test::straight());
            engine.set_integration(true, 1000);
            test::require(!app::handle(engine, input(R"({"type":"command","command":"permission","command_id":"permit-1","session_id":"nav-A","generation":1,"lease_ms":"3000"})")),
                          "typed lease field required");
            test::state(engine, nav::State::Paused);
        }},
        {"TC09", "malformed_optional_measurement_field", [] {
            wire_test::reset(); nav::Engine engine; test::begin(engine);
            auto malformed = input(R"({"type":"location","phone_session_id":"phone-A","fix_seq":2,"source_timestamp_ms":1700000002000,"measurement_age_ms":0,"coord_frame":"WGS84","lng":114,"lat":22,"horizontal_accuracy_m":3,"course_deg":"east"})");
            test::require(!app::handle(engine, malformed), "present optional field must have numeric type");
            test::require(engine.snapshot().fix_seq == 1, "invalid measurement preserves latest sequence");
        }},
        {"TC44", "host_ack_requires_current_event_identity", [] {
            wire_test::reset(); nav::Engine engine; test::activate(engine, test::straight());
            test::require(!app::handle(engine, input(R"({"type":"ack","session_id":"foreign","generation":1,"route_version":1,"event_id":1})")),
                          "foreign host event acknowledgment rejected");
            test::require(!app::handle(engine, input(R"({"type":"ack"})")),
                          "event identity fields required");
        }},
        {"TC44", "host_ack_ttl_and_retransmission", [] {
            wire_test::reset(); nav::Engine engine; test::activate(engine, test::straight());
            const std::string ack = R"({"type":"ack","session_id":"nav-A","generation":1,"route_version":1,"event_id":1})";
            test::require(app::handle(engine, input(ack)), "current event acknowledged");
            test::require(app::handle(engine, input(ack)), "current ACK retransmission accepted");
            auto repeated = output(1);
            test::require(cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(repeated.get(), "duplicate")),
                          "ACK retransmission recorded once");
            wire_test::now = 3000;
            test::require(!app::handle(engine, input(ack)), "ACK rejected at event TTL boundary");
        }},
        {"TC02", "start_id_binds_command_kind", [] {
            wire_test::reset(); nav::Engine engine;
            test::require(app::handle(engine, input(start)), "start parsed");
            test::require(app::handle(engine, input(R"({"type":"command","command":"pause","command_id":"start-1","session_id":"nav-A","generation":1})")),
                          "reused command parsed");
            test::require(!accepted(1), "start ID binds original command kind");
        }},
        {"TC11", "ble_transport_age_includes_clock_bound", [] {
            wire_test::reset(); nav::Engine engine; test::begin(engine);
            auto frame = input(R"({"type":"location","phone_session_id":"phone-A","fix_seq":2,"source_timestamp_ms":1700000002000,"measurement_age_ms":20,"device_sent_lower_bound_ms":600,"coord_frame":"WGS84","lng":114,"lat":22,"horizontal_accuracy_m":3})");
            frame.usb = false;
            test::require(app::handle(engine, frame), "synchronized BLE fix parsed");
            test::require(engine.snapshot().fix_age_ms == 420, "source age plus conservative link transit");
        }},
        {"TC11", "ble_clock_bound_future_rejected", [] {
            wire_test::reset(); nav::Engine engine; test::begin(engine);
            auto frame = input(R"({"type":"location","phone_session_id":"phone-A","fix_seq":2,"source_timestamp_ms":1700000002000,"measurement_age_ms":20,"device_sent_lower_bound_ms":1001,"coord_frame":"WGS84","lng":114,"lat":22,"horizontal_accuracy_m":3})");
            frame.usb = false;
            test::require(!app::handle(engine, frame), "future clock bound rejected");
        }}
    };
    unsigned failed = 0;
    for (const auto& item : cases) {
        if (!requested.empty() && requested != item.id) continue;
        try {
            item.run();
            std::cout << "{\"case_id\":\"" << item.id << "\",\"fixture\":\"" << item.name << "\",\"result\":\"passed\"}\n";
        } catch (const test::Failure& failure) {
            ++failed;
            std::cout << "{\"case_id\":\"" << item.id << "\",\"fixture\":\"" << item.name
                      << "\",\"result\":\"failed\",\"detail\":\"" << failure.what() << "\"}\n";
        }
    }
    return failed == 0 ? 0 : 1;
}
