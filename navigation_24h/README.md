# 步行导航模块

工程连接 iPhone、XIAO ESP32S3、高德 MCP 与电脑/Pico。iPhone 采集后台位置，ESP32 维护路线和导航状态，主机接收带会话、版本与时效的事件。

| 入口 | 内容 |
| --- | --- |
| `ios/NavigationCompanion.xcodeproj` | SwiftUI 会话、Core Location 与 Core Bluetooth |
| `firmware/` | ESP-IDF 固件、NimBLE、HTTPS MCP 与共享导航核心 |
| `tools/navctl.py` | 构建、回归、服务探测、设备采集与快照 |
| `docs/protocol.md` | GATT、分片、会话、位置年龄与事件确认 |
| `progress.json`、`checkpoint.md` | 任务实际状态及继续入口 |
| `evidence/` | 原始命令、断言、设备日志与覆盖记录 |

启动：

```sh
python3 navigation_24h/tools/navctl.py status
python3 navigation_24h/tools/navctl.py check --task T02
python3 navigation_24h/tools/navctl.py regression --suite host
```

本地配置位于 `config/local.json`；环境变量文件位于 `config/secrets.env`。运行 `configure-key` 打开系统安全输入窗口。高德 MCP 使用 Web服务 Key 与 Streamable HTTP；实际能力探测结果保存到 `fixtures/mcp/provider.json`。

运行 `configure-wifi` 输入测试网络名称及密码。通过 USB 连接 ESP32，确认设备处于 IDLE 后运行 `provision`，将本地配置写入设备；收到 `CONFIGURED_RESTART_REQUIRED` ACK 后重启并读取串口状态。

设备配置流程见 `docs/device_setup.md`，工程接入与证据说明见 `docs/handoff.md`。T01–T24 的输入、依赖和验收依据分别保存在 `config/task_catalog.json` 与 `config/test_registry.json`。
