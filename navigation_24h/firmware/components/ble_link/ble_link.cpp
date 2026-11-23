#include "ble_link.hpp"
#include "frame.hpp"
#include "pairing_control.hpp"
#include <array>
#include <atomic>
#include <cstring>
extern "C" {
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "host/ble_hs.h"
#include "host/ble_store.h"
#include "host/util/util.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"
void ble_store_config_init(void);
void ble_gatts_bonding_restored(std::uint16_t conn_handle);
}

namespace ble_link {
static constexpr const char* log_tag = "EE3070_BLE";
static Receiver receive_callback = nullptr;
static void* receive_context = nullptr;
static std::atomic<std::uint16_t> connection{BLE_HS_CONN_HANDLE_NONE};
static std::atomic<bool> authorized{false};
static std::atomic<bool> encrypted{false};
static std::atomic<bool> status_subscribed{false}, event_subscribed{false};
static std::atomic<std::uint32_t> connection_epoch{0};
static bool known_at_connection = false;
static ble_npl_callout parameter_update_timer{};
static bool parameter_timer_ready = false;
static unsigned parameter_attempts = 0;
static SemaphoreHandle_t send_mutex = nullptr;
static std::array<link_frame::Assembler, 2> assemblers;
static std::uint8_t own_address = 0;
static std::uint16_t status_handle = 0, event_handle = 0, sequence = 0;
class SendLock {
public:
    SendLock() : acquired_(send_mutex && xSemaphoreTake(send_mutex, pdMS_TO_TICKS(20)) == pdTRUE) {}
    ~SendLock() { if (acquired_) xSemaphoreGive(send_mutex); }
    explicit operator bool() const { return acquired_; }
    SendLock(const SendLock&) = delete;
    SendLock& operator=(const SendLock&) = delete;
private:
    bool acquired_;
};
static ble_gatt_chr_def characteristics[6]{};
static ble_gatt_svc_def services[2]{};
static const ble_uuid128_t uuids[] = {
    BLE_UUID128_INIT(0x70,0x30,0,0,0,0,0x21,0x8b,0x29,0x4f,0x1e,0x7b,1,0,0x7d,0x8f),
    BLE_UUID128_INIT(0x70,0x30,0,0,0,0,0x21,0x8b,0x29,0x4f,0x1e,0x7b,2,0,0x7d,0x8f),
    BLE_UUID128_INIT(0x70,0x30,0,0,0,0,0x21,0x8b,0x29,0x4f,0x1e,0x7b,3,0,0x7d,0x8f),
    BLE_UUID128_INIT(0x70,0x30,0,0,0,0,0x21,0x8b,0x29,0x4f,0x1e,0x7b,4,0,0x7d,0x8f),
    BLE_UUID128_INIT(0x70,0x30,0,0,0,0,0x21,0x8b,0x29,0x4f,0x1e,0x7b,5,0,0x7d,0x8f),
    BLE_UUID128_INIT(0x70,0x30,0,0,0,0,0x21,0x8b,0x29,0x4f,0x1e,0x7b,6,0,0x7d,0x8f)
};
static void advertise();
static void log_parameters(const char* event, int status, std::uint16_t handle) {
    ble_gap_conn_desc description{};
    const int lookup = ble_gap_conn_find(handle, &description);
    ESP_LOGI(log_tag, "event=%s epoch=%lu status=%d lookup=%d interval=%u latency=%u supervision=%u",
             event, static_cast<unsigned long>(connection_epoch.load()), status, lookup,
             description.conn_itvl, description.conn_latency, description.supervision_timeout);
}
static void schedule_parameter_update() {
    if (parameter_timer_ready)
        (void)ble_npl_callout_reset(&parameter_update_timer, ble_npl_time_ms_to_ticks32(1500));
}
static void request_link_parameters(ble_npl_event*) {
    ble_gap_conn_desc link{};
    if (!connected() || ble_gap_conn_find(connection.load(), &link) != 0 ||
        link.supervision_timeout >= 200 || parameter_attempts >= 2) return;
    ++parameter_attempts;
    ble_gap_upd_params preferred{};
    preferred.itvl_min = 24;
    preferred.itvl_max = 36;
    preferred.supervision_timeout = 300;
    const int status = ble_gap_update_params(link.conn_handle, &preferred);
    ESP_LOGI(log_tag, "event=parameters_requested epoch=%lu status=%d supervision=%u",
             static_cast<unsigned long>(connection_epoch.load()), status, preferred.supervision_timeout);
    if ((status == BLE_HS_EBUSY || status == BLE_HS_EALREADY) && parameter_attempts == 1)
        schedule_parameter_update();
}
static int access(std::uint16_t conn, std::uint16_t, ble_gatt_access_ctxt* context, void* argument) {
    const auto slot = reinterpret_cast<std::uintptr_t>(argument);
    if (context->op == BLE_GATT_ACCESS_OP_READ_CHR && slot == 4) {
        const char* capabilities = "{\"protocol_version\":1,\"max_frame\":512,\"fragment_timeout_ms\":1000,\"config_version\":\"navigation-1\"}";
        return os_mbuf_append(context->om, capabilities, std::strlen(capabilities)) == 0 ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
    }
    if (context->op != BLE_GATT_ACCESS_OP_WRITE_CHR || slot > 1) return BLE_ATT_ERR_WRITE_NOT_PERMITTED;
    ble_gap_conn_desc description{};
    if (conn != connection.load() || !authorized || ble_gap_conn_find(conn, &description) != 0 || !description.sec_state.encrypted) return BLE_ATT_ERR_INSUFFICIENT_AUTHEN;
    std::array<std::uint8_t, 512> bytes{};
    std::uint16_t length = 0;
    if (ble_hs_mbuf_to_flat(context->om, bytes.data(), bytes.size(), &length) != 0) return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
    const auto result = assemblers[slot].feed(bytes.data(), length, esp_timer_get_time() / 1000);
    if (result == link_frame::Result::Complete) {
        const auto message = assemblers[slot].message();
        return receive_callback(message.data(), message.size(), receive_context) ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
    }
    return result == link_frame::Result::Partial ? 0 : BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
}
static int gap(ble_gap_event* event, void*) {
    switch (event->type) {
    case BLE_GAP_EVENT_CONNECT:
        if (event->connect.status != 0) {
            ESP_LOGI(log_tag, "event=connect_failed status=%d", event->connect.status);
            advertise();
            break;
        }
        ++connection_epoch;
        connection = event->connect.conn_handle;
        encrypted = false; status_subscribed = false; event_subscribed = false;
        parameter_attempts = 0;
        log_parameters("connect", event->connect.status, event->connect.conn_handle);
        {
            const auto admission = pairing_adapter::connected(connection, connection_epoch);
            known_at_connection = admission == pairing_adapter::Admission::Known;
            authorized = known_at_connection;
            if (admission != pairing_adapter::Admission::Denied) {
                const int security_start = ble_gap_security_initiate(connection);
                ESP_LOGI(log_tag, "event=security_start epoch=%lu known=%d status=%d",
                         static_cast<unsigned long>(connection_epoch.load()), authorized.load(), security_start);
                if (security_start != 0 && security_start != BLE_HS_EALREADY)
                    (void)ble_gap_terminate(connection, BLE_ERR_REM_USER_CONN_TERM);
            } else {
                ESP_LOGI(log_tag, "event=peer_denied epoch=%lu",
                         static_cast<unsigned long>(connection_epoch.load()));
                (void)ble_gap_terminate(connection, BLE_ERR_REM_USER_CONN_TERM);
            }
        }
        break;
    case BLE_GAP_EVENT_DISCONNECT:
        ESP_LOGI(log_tag, "event=disconnect epoch=%lu reason=%d encrypted=%d authenticated=%d bonded=%d",
                 static_cast<unsigned long>(connection_epoch.load()), event->disconnect.reason,
                 event->disconnect.conn.sec_state.encrypted, event->disconnect.conn.sec_state.authenticated,
                 event->disconnect.conn.sec_state.bonded);
        pairing_adapter::disconnected(connection, connection_epoch);
        ++connection_epoch;
        connection = BLE_HS_CONN_HANDLE_NONE;
        authorized = false; encrypted = false;
        known_at_connection = false;
        if (parameter_timer_ready) ble_npl_callout_stop(&parameter_update_timer);
        status_subscribed = false; event_subscribed = false;
        for (auto& assembler : assemblers) assembler.reset();
        advertise();
        break;
    case BLE_GAP_EVENT_ENC_CHANGE:
        if (event->enc_change.conn_handle == connection.load()) {
            ble_gap_conn_desc description{};
            const int lookup = ble_gap_conn_find(connection.load(), &description);
            const auto& security = description.sec_state;
            // A redundant security procedure can time out after encryption was restored.
            const bool already_secure = event->enc_change.status == BLE_HS_ETIMEOUT &&
                known_at_connection && lookup == 0 && security.encrypted &&
                security.authenticated && security.bonded && security.key_size == 16;
            const bool security_present = event->enc_change.status == 0 || already_secure;
            encrypted = security_present && lookup == 0 && security.encrypted;
            authorized = pairing_adapter::encrypted(connection, connection_epoch, security_present);
            ESP_LOGI(log_tag, "event=encryption epoch=%lu status=%d lookup=%d encrypted=%d authenticated=%d bonded=%d key_size=%u retained=%d authorized=%d",
                     static_cast<unsigned long>(connection_epoch.load()), event->enc_change.status, lookup,
                     security.encrypted, security.authenticated, security.bonded, security.key_size,
                     already_secure, authorized.load());
            if (!authorized) {
                (void)ble_gap_terminate(connection, BLE_ERR_REM_USER_CONN_TERM);
            } else if (encrypted) {
                // NimBLE normally restores persisted CCCDs after a successful security callback.
                if (already_secure) ble_gatts_bonding_restored(description.conn_handle);
                if (parameter_attempts == 0 && description.supervision_timeout < 200)
                    schedule_parameter_update();
            }
        }
        break;
    case BLE_GAP_EVENT_CONN_UPDATE:
        log_parameters("parameters_updated", event->conn_update.status, event->conn_update.conn_handle);
        if (event->conn_update.conn_handle == connection.load() && parameter_attempts == 1 &&
            event->conn_update.status == BLE_HS_HCI_ERR(BLE_ERR_DIFF_TRANS_COLL)) schedule_parameter_update();
        break;
    case BLE_GAP_EVENT_PASSKEY_ACTION:
        if (event->passkey.conn_handle == connection.load())
            pairing_adapter::comparison(connection, connection_epoch, event->passkey.params.action,
                                         event->passkey.params.numcmp);
        break;
    case BLE_GAP_EVENT_SUBSCRIBE:
        if (event->subscribe.conn_handle == connection.load()) {
            const char* channel = nullptr;
            if (event->subscribe.attr_handle == status_handle) {
                status_subscribed = event->subscribe.cur_notify;
                channel = "status";
            }
            if (event->subscribe.attr_handle == event_handle) {
                event_subscribed = event->subscribe.cur_notify;
                channel = "button";
            }
            if (channel) {
                ESP_LOGI(log_tag, "event=subscription epoch=%lu channel=%s reason=%d enabled=%d",
                         static_cast<unsigned long>(connection_epoch.load()), channel,
                         event->subscribe.reason, event->subscribe.cur_notify);
            }
        }
        break;
    case BLE_GAP_EVENT_ADV_COMPLETE: advertise(); break;
    case BLE_GAP_EVENT_REPEAT_PAIRING:
        ESP_LOGI(log_tag, "event=repeat_pairing epoch=%lu", static_cast<unsigned long>(connection_epoch.load()));
        return BLE_GAP_REPEAT_PAIRING_IGNORE;
    default: break;
    }
    return 0;
}
static void advertise() {
    ble_hs_adv_fields fields{};
    fields.flags = BLE_HS_ADV_F_DISC_GEN | BLE_HS_ADV_F_BREDR_UNSUP;
    fields.uuids128 = &uuids[0]; fields.num_uuids128 = 1; fields.uuids128_is_complete = 1;
    if (ble_gap_adv_set_fields(&fields) != 0) return;
    ble_hs_adv_fields response{};
    const char* name = "EE3070 Navigation";
    response.name = reinterpret_cast<const std::uint8_t*>(name);
    response.name_len = std::strlen(name); response.name_is_complete = 1;
    (void)ble_gap_adv_rsp_set_fields(&response);
    ble_gap_adv_params parameters{};
    parameters.conn_mode = BLE_GAP_CONN_MODE_UND; parameters.disc_mode = BLE_GAP_DISC_MODE_GEN;
    (void)ble_gap_adv_start(own_address, nullptr, BLE_HS_FOREVER, &parameters, gap, nullptr);
}
static void sync() {
    if (ble_hs_util_ensure_addr(0) == 0 && ble_hs_id_infer_auto(0, &own_address) == 0) advertise();
}
static void host(void*) { nimble_port_run(); nimble_port_freertos_deinit(); }
static void reset(int reason) {
    ESP_LOGI(log_tag, "event=host_reset reason=%d", reason);
    ++connection_epoch; connection = BLE_HS_CONN_HANDLE_NONE;
    authorized = false; encrypted = false; status_subscribed = false; event_subscribed = false;
    known_at_connection = false;
    if (parameter_timer_ready) ble_npl_callout_stop(&parameter_update_timer);
    for (auto& assembler : assemblers) assembler.reset();
    pairing_adapter::reset();
}
void start(Receiver receiver, void* context) {
    receive_callback = receiver; receive_context = context;
    send_mutex = xSemaphoreCreateMutex();
    ESP_ERROR_CHECK(send_mutex ? ESP_OK : ESP_ERR_NO_MEM);
    ESP_ERROR_CHECK(nimble_port_init());
    pairing_adapter::initialize();
    parameter_timer_ready = ble_npl_callout_init(&parameter_update_timer,
        nimble_port_get_dflt_eventq(), request_link_parameters, nullptr) == 0;
    ble_svc_gap_init(); ble_svc_gatt_init();
    for (std::size_t index = 0; index < 5; ++index) {
        characteristics[index].uuid = &uuids[index + 1].u;
        characteristics[index].access_cb = access;
        characteristics[index].arg = reinterpret_cast<void*>(index);
        characteristics[index].flags = index < 2 ? BLE_GATT_CHR_F_WRITE | BLE_GATT_CHR_F_WRITE_ENC : index < 4 ? BLE_GATT_CHR_F_NOTIFY : BLE_GATT_CHR_F_READ;
    }
    characteristics[2].val_handle = &status_handle; characteristics[3].val_handle = &event_handle;
    services[0].type = BLE_GATT_SVC_TYPE_PRIMARY; services[0].uuid = &uuids[0].u; services[0].characteristics = characteristics;
    ESP_ERROR_CHECK(ble_gatts_count_cfg(services)); ESP_ERROR_CHECK(ble_gatts_add_svcs(services));
    ble_hs_cfg.sync_cb = sync; ble_hs_cfg.reset_cb = reset; ble_hs_cfg.store_status_cb = ble_store_util_status_rr;
    ble_hs_cfg.sm_io_cap = BLE_HS_IO_DISPLAY_YESNO; ble_hs_cfg.sm_bonding = 1;
    ble_hs_cfg.sm_sc = 1; ble_hs_cfg.sm_sc_only = 1; ble_hs_cfg.sm_mitm = 1;
    ble_hs_cfg.sm_our_key_dist = BLE_SM_PAIR_KEY_DIST_ENC | BLE_SM_PAIR_KEY_DIST_ID;
    ble_hs_cfg.sm_their_key_dist = BLE_SM_PAIR_KEY_DIST_ENC | BLE_SM_PAIR_KEY_DIST_ID;
    ble_store_config_init();
    (void)ble_svc_gap_device_name_set("EE3070 Navigation");
    nimble_port_freertos_init(host);
}
bool connected() { return connection != BLE_HS_CONN_HANDLE_NONE && authorized && encrypted; }
std::uint32_t epoch() { return connection_epoch.load(); }
bool authorized_epoch(std::uint32_t value) { return value == connection_epoch.load() && connected(); }
namespace detail {
std::uint16_t current_handle() { return connection.load(); }
}
bool send(const std::string& message, bool event_channel,
          std::optional<std::uint32_t> expected_epoch) {
    if (message.empty() || message.size() > 512) return false;
    SendLock lock;
    if (!lock || !connected() || !(event_channel ? event_subscribed.load() : status_subscribed.load())) return false;
    const auto handle = connection.load();
    const auto epoch = connection_epoch.load();
    if (expected_epoch && epoch != *expected_epoch) return false;
    const auto mtu = ble_att_mtu(handle);
    if (mtu <= 13) return false;
    for (const auto& part : link_frame::fragment(message, ++sequence, mtu - 3)) {
        if (connection.load() != handle || connection_epoch.load() != epoch || !connected()) return false;
        auto* buffer = ble_hs_mbuf_from_flat(part.data(), part.size());
        if (!buffer || ble_gatts_notify_custom(handle, event_channel ? event_handle : status_handle, buffer) != 0) return false;
    }
    return true;
}
}
