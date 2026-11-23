# Navigation Protocol 1

JSON 消息使用 UTF-8。BLE 应用消息和 USB 输入帧每条最多 512 字节；USB 状态与诊断输出每条最多 1024 字节。坐标数字使用经度 `lng`、纬度 `lat`；手机来源标记 `WGS84`。设备单调时钟单位为毫秒。

## GATT

| 通道 | UUID | 属性 |
| --- | --- | --- |
| Service | 8F7D0001-7B1E-4F29-8B21-000000003070 | Primary |
| Location | 8F7D0002-7B1E-4F29-8B21-000000003070 | Write with response |
| Command | 8F7D0003-7B1E-4F29-8B21-000000003070 | Write with response |
| Status | 8F7D0004-7B1E-4F29-8B21-000000003070 | Notify |
| Event | 8F7D0005-7B1E-4F29-8B21-000000003070 | Notify |
| Capabilities | 8F7D0006-7B1E-4F29-8B21-000000003070 | Read |

分片头为 10 字节：magic `4E 31`，version `01`，flags `00`，message_id uint16、total_length uint16、offset uint16，整数按小端排列。每片余下字节为 JSON 内容。单条消息按 offset 连续重组，每条通道拥有一个槽位；起片 offset 为 0，期限从起片起 1000 ms，完成后交付解析。总长 1–512。message_id 每条递增并以 uint16 回绕。收到完整消息后通过 status 返回应用层 ACK，ACK 关联 command_id 或 fix_seq。

## 消息

位置：`{"type":"location","phone_session_id":"UUID","fix_seq":1,"source_timestamp_ms":0,"measurement_age_ms":0,"coord_frame":"WGS84","lng":114.17,"lat":22.33,"horizontal_accuracy_m":5,"speed_mps":1,"course_deg":90}`。来源时间是位置测量的 Unix 毫秒；年龄是手机提交时测量年龄。ESP32 从接收完成时的单调时钟继续计龄，按独立序号判断新测量。

命令共有字段 `type:"command"`、`command`、`command_id`、`session_id`、`generation`。start 另含 `phone_session_id` 和 `destination:{name,lng,lat,coord_frame}`。手机生成导航 session_id；generation 从 1 开始，cancel 增加代次。动作命令携带当前 `route_version` 和 `event_id`。安全许可通过 `permission` 命令携带 `lease_ms`（上限 3000）。绑定通过 `bind`、设备确认及绑定身份记录完成。

command 可取 start、cancel、pause、resume、repeat、confirm_turn、confirm_crossing、confirm_arrival、get_status、permission、bind。确认来源为 manual_confirm 或 valid_movement_evidence。

状态字段：`type:"status"`、session_id、generation、route_version、state、fix_seq、fix_age_ms、quality、ble、wifi、mcp、config_version。状态枚举：IDLE、WAIT_FIX、ROUTING、FOLLOW、APPROACH、ALIGN_CONFIRM、TURN、VERIFY、RELOCALIZE、REROUTING、CROSSING_WAIT、PAUSED、ARRIVAL_NEAR、ARRIVED。

事件字段：type（route_ready/maneuver/relocalize/rerouting/arrival_near/arrived/fault）、session_id、generation、route_version、event_id、fix_seq、distance_m、confirmation_source、ttl_ms、issued_at_ms。动作 TTL 为 2000 ms。USB CDC 使用一行一个完整 JSON；主机根据接收单调时刻继续计龄，ACK 包含关联事件 ID。

ACK：`{"type":"ack","command_id":"UUID","session_id":"UUID","generation":1,"accepted":true,"reason":"OK"}`。位置 ACK 使用 fix_seq。恢复命令 get_status 交换 phone_session_id、最新 fix_seq 和当前摘要，再以新位置继续。

时钟同步：get_status 带 phone_send_monotonic_ms，ACK 回显并提供接收时 device_monotonic_ms。手机使用发送/接收中点估计偏移，以半 RTT 表示不确定度。BLE 位置帧在首片提交时携带 device_sent_lower_bound_ms（手机提交单调时间加偏移下界）；固件接收时间减该下界，加接收队列年龄和源测量年龄，得到位置年龄上界。同步有效期 30 s、RTT 上限 1000 ms；重连后建立新同步。

## 执行阈值

位置有效精度 20 m，附近判定 10 m，过期门限 5000 ms。偏航距离 25 m、三次独立测量且持续 3000 ms，冷却 15000 ms。最后路段 15 m 区域内连续三次有效测量后提示附近，入口人工确认后到达。响应容量 262144 字节、2048 折线点、256 步骤。候选路线提交核对 session_id、generation、request_id 及有效测量。

## USB 绑定确认

电脑在设备 IDLE 时发送 `{"type":"pairing","action":"open","command_id":"pair-open-1"}`，开启 60 秒的单次配对窗口。手机选中设备后进入安全连接数字比较；电脑与 iPhone 系统分别显示六位数字。操作者核对并确认，比较阶段有效期为 25 秒，并受当前窗口期限约束。

配对控制使用 `type:"pairing"`，action 为 open、confirm、reject、status 或 close。confirm、reject 携带当前 `window_id`、`connection_epoch` 和 `number`（0–999999）。响应使用 `type:"pairing_status"`，包含 state、command_id、window_id、connection_epoch、deadline_ms、accepted 和 reason；COMPARING 状态携带 number。状态顺序为 ARMED、CANDIDATE、COMPARING、APPROVED、AUTHORIZED，窗口结束为 CLOSED。结构定义位于 `protocol/pairing.schema.json`。

设备将批准绑定到当前启动窗口、连接代次、连接句柄、对端身份和比较数字，在 NimBLE host 队列执行时再次核对。安全连接完成加密、认证和身份保存后，应用通道获得授权。USB 配对通知由独立输出路径交付电脑。手机收到当前可信状态后发送 bind，并核对当前 peripheral、链路代次及对应 command_id；成功 ACK 为 BOUND。已保存的对端身份用于重连恢复。
