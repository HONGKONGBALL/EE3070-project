#pragma once
#include <cstddef>
#include <cstdint>
inline constexpr std::uint16_t BLE_HS_CONN_HANDLE_NONE = 0xffff;
inline constexpr int BLE_ERR_REM_USER_CONN_TERM = 0x13;
inline constexpr std::uint8_t BLE_SM_IOACT_NUMCMP = 4;
struct ble_addr_t { std::uint8_t type; std::uint8_t val[6]; };
struct ble_gap_sec_state { bool encrypted; bool authenticated; bool bonded; };
struct ble_gap_conn_desc { ble_addr_t peer_ota_addr; ble_addr_t peer_id_addr; ble_gap_sec_state sec_state; };
struct ble_sm_io { std::uint8_t action; std::uint8_t numcmp_accept; };
struct ble_npl_event { void (*callback)(ble_npl_event*); void* argument; bool allocated; };
struct ble_npl_eventq { int identifier; };
struct ble_npl_callout { ble_npl_event event; std::uint64_t due; };
int ble_gap_conn_find(std::uint16_t handle, ble_gap_conn_desc* description);
int ble_store_util_bonded_peers(ble_addr_t* peers, int* count, int maximum);
int ble_addr_cmp(const ble_addr_t* first, const ble_addr_t* second);
int ble_gap_terminate(std::uint16_t handle, int reason);
int ble_sm_inject_io(std::uint16_t handle, ble_sm_io* io);
void ble_npl_event_init(ble_npl_event* event, void (*callback)(ble_npl_event*), void* argument);
void ble_npl_event_deinit(ble_npl_event* event);
void* ble_npl_event_get_arg(ble_npl_event* event);
void ble_npl_eventq_put(ble_npl_eventq* queue, ble_npl_event* event);
ble_npl_eventq* nimble_port_get_dflt_eventq();
int ble_npl_callout_init(ble_npl_callout* timer, ble_npl_eventq*, void (*callback)(ble_npl_event*), void* argument);
int ble_npl_callout_reset(ble_npl_callout* timer, std::uint32_t ticks);
std::uint32_t ble_npl_time_ms_to_ticks32(std::uint32_t milliseconds);
