#pragma once
#include <array>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace nav {
using Millis = std::uint64_t;
enum class State { Idle, WaitFix, Routing, Follow, Approach, AlignConfirm, Turn,
    Verify, Relocalize, Rerouting, CrossingWait, Paused, ArrivalNear, Arrived };
enum class Quality { Valid, Degraded, Lost };
enum class FixReason { Accepted, Identity, Sequence, SourceDuplicate, Expired, Accuracy, Jump,
    Coordinates, Clock, Link };
enum class CommandKind { Cancel, Pause, Resume, Repeat, ConfirmTurn,
    ConfirmCrossing, ConfirmArrival, GetStatus, Permission };
struct Point { double lng = 0; double lat = 0; };
struct Destination { std::string name; Point position; };
struct Fix {
    std::string phone_session_id;
    std::uint64_t fix_seq = 0;
    std::int64_t source_timestamp_ms = 0;
    Millis measurement_age_ms = 0;
    Point position;
    double horizontal_accuracy_m = 0;
    std::optional<double> speed_mps;
    std::optional<double> course_deg;
};
struct FixResult { bool accepted; FixReason reason; };
struct Step { std::size_t end_point = 0; std::string action; bool crossing = false; };
struct Route {
    std::vector<Point> points;
    std::vector<Step> steps;
    std::string coord_frame = "WGS84";
};
struct RequestToken {
    std::string session_id;
    std::uint64_t generation = 0;
    std::uint64_t request_id = 0;
    Point origin;
    Destination destination;
};
struct Command {
    CommandKind kind = CommandKind::GetStatus;
    std::string command_id;
    std::string session_id;
    std::uint64_t generation = 0;
    std::uint64_t route_version = 0;
    std::uint64_t event_id = 0;
    Millis lease_ms = 3000;
};
struct Ack { bool accepted; bool duplicate; std::string reason; };
struct Event {
    std::string type;
    std::string session_id;
    std::uint64_t generation = 0;
    std::uint64_t route_version = 0;
    std::uint64_t event_id = 0;
    std::uint64_t fix_seq = 0;
    double distance_m = 0;
    std::string confirmation_source;
    Millis ttl_ms = 2000;
    Millis issued_at_ms = 0;
    std::string reason{};
};
struct Snapshot {
    State state = State::Idle;
    Quality quality = Quality::Lost;
    std::string session_id;
    std::uint64_t generation = 0;
    std::uint64_t route_version = 0;
    std::uint64_t fix_seq = 0;
    Millis fix_age_ms = 0;
    double progress_m = 0;
    double lateral_m = 0;
    std::size_t segment = 0;
    std::uint64_t current_event_id = 0;
    std::string reason = "READY";
};
struct MatchCandidate {
    std::size_t segment = 0;
    double progress_m = 0;
    double lateral_m = 0;
    double score = 0;
    double heading_error_deg = 0;
    bool reachable = false;
};
struct Match {
    bool found = false;
    bool ambiguous = false;
    bool backward = false;
    std::size_t segment = 0;
    double progress_m = 0;
    double lateral_m = 0;
    double total_m = 0;
    std::array<MatchCandidate, 4> candidates{};
    std::size_t candidate_count = 0;
};
double distance_m(Point a, Point b);
double bearing_deg(Point a, Point b);
bool valid_point(Point point);
const char* name(State state);
const char* name(Quality quality);
const char* name(FixReason reason);
class Matcher {
public:
    Match match(const Route& route, const Fix& fix, Millis now);
    void reset();
private:
    std::optional<Match> previous_;
    Millis previous_time_ = 0;
    double previous_accuracy_ = 0;
    unsigned backward_count_ = 0;
};
class Engine {
public:
    Ack start(std::string session_id, std::string phone_session_id,
              std::uint64_t generation, Destination destination, Millis now,
              const std::string& command_id = "");
    FixResult ingest(const Fix& fix, Millis now);
    Ack command(const Command& command, Millis now);
    std::optional<RequestToken> pending_request() const { return request_; }
    bool commit_route(const RequestToken& token, const Route& route, Millis now);
    void fail_request(const RequestToken& token, const std::string& reason, Millis now);
    void tick(Millis now);
    void set_link(bool connected, Millis now);
    void set_integration(bool enabled, Millis now);
    const Snapshot& snapshot() const { return snapshot_; }
    const Route& active_route() const { return route_; }
    const Match& matching() const { return last_match_; }
    std::vector<Event> drain_events(Millis now);
    bool event_valid(const Event& event, Millis now) const;
    Ack acknowledge_event(const std::string& session_id, std::uint64_t generation,
                          std::uint64_t route_version, std::uint64_t event_id, Millis now);
private:
    struct StartRecord {
        std::string command_id, session_id, phone_session_id;
        std::uint64_t generation = 0;
        Destination destination;
    };
    void request_route(Millis now, bool reroute);
    void follow(const Fix& fix, Millis now);
    void transition(State state, const std::string& reason, Millis now);
    void emit(const std::string& type, Millis now, const std::string& source = "");
    void update_turn(const Fix& fix, const Match& match, Millis now);
    void update_arrival(const Fix& fix, const Match& match, Millis now);
    void update_offroute(const Match& match, Millis now);
    bool current_token(const RequestToken& token) const;
    bool fresh(Millis now) const;
    bool permitted(Millis now) const;
    Ack apply_command(const Command& command, Millis now);
    Snapshot snapshot_;
    std::string phone_session_id_;
    Destination destination_;
    Route route_;
    Matcher matcher_;
    Match last_match_;
    std::optional<Fix> last_fix_;
    Millis last_received_at_ = 0;
    Millis clock_ = 0;
    bool active_ = false;
    bool link_connected_ = true;
    bool integrated_ = false;
    bool awaiting_new_fix_ = false;
    bool user_paused_ = false;
    bool resume_required_ = false;
    Millis permission_until_ = 0;
    std::uint64_t seen_seq_ = 0;
    std::optional<RequestToken> request_;
    std::uint64_t next_request_ = 1;
    std::uint64_t next_event_ = 1;
    std::vector<Event> events_;
    std::optional<Event> current_event_;
    std::array<Command, 32> commands_{};
    std::array<Ack, 32> command_acks_{};
    std::size_t command_cursor_ = 0;
    std::array<StartRecord, 16> starts_{};
    std::size_t start_cursor_ = 0;
    std::uint64_t acknowledged_event_ = 0;
    std::size_t turn_step_ = 0;
    bool crossing_confirmed_ = false;
    unsigned arrival_count_ = 0;
    unsigned offroute_count_ = 0;
    Millis offroute_since_ = 0;
    Millis last_reroute_ = 0;
    bool has_rerouted_ = false;
};
}
