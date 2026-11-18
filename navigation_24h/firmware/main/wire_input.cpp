#include "app_runtime.hpp"
#include <array>
#include <cstring>

namespace app {
static void ack(const cJSON* input, const nav::Engine& engine, bool accepted, const char* reason, nav::Millis received) {
    Json root(cJSON_CreateObject(), cJSON_Delete);
    cJSON_AddStringToObject(root.get(), "type", "ack");
    cJSON_AddStringToObject(root.get(), "command_id", string_field(input, "command_id").c_str());
    cJSON_AddStringToObject(root.get(), "session_id", engine.snapshot().session_id.c_str());
    cJSON_AddNumberToObject(root.get(), "generation", engine.snapshot().generation);
    cJSON_AddBoolToObject(root.get(), "accepted", accepted);
    cJSON_AddStringToObject(root.get(), "reason", reason);
    cJSON_AddNumberToObject(root.get(), "device_monotonic_ms", received);
    cJSON_AddNumberToObject(root.get(), "ack_ms", clock_ms() - received);
    std::uint64_t phone = 0;
    if (integer_field(input, "phone_send_monotonic_ms", phone)) cJSON_AddNumberToObject(root.get(), "phone_send_monotonic_ms", phone);
    std::uint64_t fix_seq = 0;
    if (integer_field(input, "fix_seq", fix_seq)) cJSON_AddNumberToObject(root.get(), "fix_seq", fix_seq);
    output(encode(root.get()));
}
static bool location(nav::Engine& engine, const cJSON* root, nav::Millis received, bool usb) {
    nav::Fix fix;
    std::uint64_t timestamp = 0;
    fix.phone_session_id = string_field(root, "phone_session_id");
    if (string_field(root, "coord_frame") != "WGS84" || fix.phone_session_id.empty() ||
        !integer_field(root, "fix_seq", fix.fix_seq) || !integer_field(root, "source_timestamp_ms", timestamp) ||
        !integer_field(root, "measurement_age_ms", fix.measurement_age_ms) ||
        !number_field(root, "lng", fix.position.lng) || !number_field(root, "lat", fix.position.lat) ||
        !number_field(root, "horizontal_accuracy_m", fix.horizontal_accuracy_m)) return false;
    fix.source_timestamp_ms = static_cast<std::int64_t>(timestamp);
    double optional = 0;
    if (cJSON_HasObjectItem(root, "speed_mps")) {
        if (!number_field(root, "speed_mps", optional)) return false;
        fix.speed_mps = optional;
    }
    if (cJSON_HasObjectItem(root, "course_deg")) {
        if (!number_field(root, "course_deg", optional)) return false;
        fix.course_deg = optional;
    }
    const auto now = clock_ms();
    if (now < received) return false;
    std::uint64_t lower_bound = 0;
    const bool has_bound = cJSON_HasObjectItem(root, "device_sent_lower_bound_ms");
    if ((!usb || has_bound) && (!integer_field(root, "device_sent_lower_bound_ms", lower_bound) || lower_bound > received)) return false;
    const auto delivery_age = has_bound ? now - lower_bound : now - received;
    if (fix.measurement_age_ms > UINT64_MAX - delivery_age) return false;
    const auto source_age = fix.measurement_age_ms;
    fix.measurement_age_ms += delivery_age;
    const auto result = engine.ingest(fix, now);
    ack(root, engine, result.accepted, nav::name(result.reason), received);
    Json log(cJSON_CreateObject(), cJSON_Delete);
    cJSON_AddStringToObject(log.get(), "type", "fix_received");
    cJSON_AddNumberToObject(log.get(), "monotonic_ms", clock_ms());
    cJSON_AddNumberToObject(log.get(), "fix_seq", fix.fix_seq);
    cJSON_AddNumberToObject(log.get(), "fix_age_ms", fix.measurement_age_ms);
    cJSON_AddNumberToObject(log.get(), "source_age_ms", source_age);
    cJSON_AddNumberToObject(log.get(), "transport_age_upper_ms", delivery_age);
    cJSON_AddNumberToObject(log.get(), "received_at_ms", received);
    cJSON_AddStringToObject(log.get(), "phone_session_id", fix.phone_session_id.c_str());
    cJSON_AddStringToObject(log.get(), "session_id", engine.snapshot().session_id.c_str());
    cJSON_AddNumberToObject(log.get(), "source_timestamp_ms", fix.source_timestamp_ms);
    cJSON_AddNumberToObject(log.get(), "lng", fix.position.lng);
    cJSON_AddNumberToObject(log.get(), "lat", fix.position.lat);
    cJSON_AddNumberToObject(log.get(), "horizontal_accuracy_m", fix.horizontal_accuracy_m);
    cJSON_AddNumberToObject(log.get(), "processing_ms", clock_ms() - received);
    cJSON_AddStringToObject(log.get(), "reason", nav::name(result.reason));
    cJSON_AddBoolToObject(log.get(), "accepted", result.accepted);
    output(encode(log.get()));
    return true;
}
static bool command(nav::Engine& engine, const cJSON* root, const Input& input) {
    const auto received = input.received_at;
    const auto kind = string_field(root, "command");
    const auto id = string_field(root, "command_id");
    const auto session = string_field(root, "session_id");
    std::uint64_t generation = 0;
    if (id.empty() || !integer_field(root, "generation", generation)) return false;
    if (kind == "bind") {
        const bool confirmed = !input.usb && ble_link::authorized_epoch(input.ble_epoch);
        ack(root, engine, confirmed, confirmed ? "BOUND" : "DEVICE_CONFIRMATION_REQUIRED", received);
        return true;
    }
    if (kind == "get_status") {
        ack(root, engine, true, "OK", received); status(engine); return true;
    }
    if (kind == "start") {
        const auto* destination = cJSON_GetObjectItemCaseSensitive(root, "destination");
        nav::Destination goal;
        goal.name = string_field(destination, "name");
        const auto phone = string_field(root, "phone_session_id");
        if (goal.name.empty() || phone.empty() || session.empty() || string_field(destination, "coord_frame") != "WGS84" ||
            !number_field(destination, "lng", goal.position.lng) || !number_field(destination, "lat", goal.position.lat)) return false;
        const auto result = engine.start(session, phone, generation, goal, clock_ms(), id);
        ack(root, engine, result.accepted, result.reason.c_str(), received); return true;
    }
    const std::array<std::pair<const char*, nav::CommandKind>, 9> commands{{
        {"cancel", nav::CommandKind::Cancel}, {"pause", nav::CommandKind::Pause}, {"resume", nav::CommandKind::Resume},
        {"repeat", nav::CommandKind::Repeat}, {"confirm_turn", nav::CommandKind::ConfirmTurn},
        {"confirm_crossing", nav::CommandKind::ConfirmCrossing}, {"confirm_arrival", nav::CommandKind::ConfirmArrival},
        {"permission", nav::CommandKind::Permission}, {"get_status", nav::CommandKind::GetStatus}}};
    for (const auto& entry : commands) {
        if (kind != entry.first) continue;
        nav::Command command{entry.second, id, session, generation};
        if (cJSON_HasObjectItem(root, "route_version") &&
            !integer_field(root, "route_version", command.route_version)) return false;
        if (cJSON_HasObjectItem(root, "event_id") &&
            !integer_field(root, "event_id", command.event_id)) return false;
        if ((cJSON_HasObjectItem(root, "lease_ms") || entry.second == nav::CommandKind::Permission) &&
            !integer_field(root, "lease_ms", command.lease_ms)) return false;
        const auto result = engine.command(command, clock_ms());
        ack(root, engine, result.accepted, result.reason.c_str(), received); return true;
    }
    return false;
}
static bool pairing(nav::Engine& engine, const cJSON* root) {
    ble_link::PairingCommand request;
    request.command_id = string_field(root, "command_id");
    if (request.command_id.empty()) return false;
    const auto action = string_field(root, "action");
    if (action == "open") {
        if (engine.snapshot().state != nav::State::Idle) return false;
        request.action = ble_link::PairingAction::Open;
    } else if (action == "status") request.action = ble_link::PairingAction::Status;
    else if (action == "close") request.action = ble_link::PairingAction::Close;
    else if (action == "confirm" || action == "reject") {
        std::uint64_t generation = 0, number = 0;
        request.window_id = string_field(root, "window_id");
        if (request.window_id.empty() || !integer_field(root, "connection_epoch", generation) ||
            generation > UINT32_MAX || !integer_field(root, "number", number) || number > 999999) return false;
        request.connection_epoch = static_cast<std::uint32_t>(generation);
        request.number = static_cast<std::uint32_t>(number);
        request.action = action == "confirm" ? ble_link::PairingAction::Confirm : ble_link::PairingAction::Reject;
    } else return false;
    return ble_link::submit_pairing(request);
}
bool handle(nav::Engine& engine, const Input& input) {
    if (!input.usb && !ble_link::authorized_epoch(input.ble_epoch)) return false;
    auto root = parse(input.bytes, input.length);
    if (!root) return false;
    const auto type = string_field(root.get(), "type");
    if (type == "location") return location(engine, root.get(), input.received_at, input.usb);
    if (type == "command") return command(engine, root.get(), input);
    if (type == "pairing") return input.usb && pairing(engine, root.get());
    if (type == "configure" && input.usb && engine.snapshot().state == nav::State::Idle) {
        const bool accepted = provision(root.get());
        ack(root.get(), engine, accepted, accepted ? "CONFIGURED_RESTART_REQUIRED" : "CONFIG_CONFIRMATION_REQUIRED", input.received_at);
        return true;
    }
    if (type == "ack") {
        std::uint64_t generation = 0, route = 0, event = 0;
        const auto session = string_field(root.get(), "session_id");
        if (session.empty() || !integer_field(root.get(), "generation", generation) ||
            !integer_field(root.get(), "route_version", route) ||
            !integer_field(root.get(), "event_id", event)) return false;
        const auto result = engine.acknowledge_event(session, generation, route, event, clock_ms());
        Json log(cJSON_CreateObject(), cJSON_Delete);
        cJSON_AddStringToObject(log.get(), "type", "host_ack");
        cJSON_AddStringToObject(log.get(), "session_id", session.c_str());
        cJSON_AddNumberToObject(log.get(), "generation", generation);
        cJSON_AddNumberToObject(log.get(), "route_version", route);
        cJSON_AddNumberToObject(log.get(), "event_id", event);
        cJSON_AddBoolToObject(log.get(), "accepted", result.accepted);
        cJSON_AddBoolToObject(log.get(), "duplicate", result.duplicate);
        cJSON_AddStringToObject(log.get(), "reason", result.reason.c_str());
        cJSON_AddNumberToObject(log.get(), "monotonic_ms", clock_ms());
        output(encode(log.get())); return result.accepted;
    }
    return false;
}
}
