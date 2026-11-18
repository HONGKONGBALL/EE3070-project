# 高德 MCP 客户端

网络任务持有 `EspHttpTransport` 和 `Client`，通过 `discover()` 登记步行工具名称及输入、输出 schema，再用 `walking(token, origin, destination)` 发起请求。返回的 `WalkingReply` 关联原始 `RequestToken`，由调用者核对当前会话与代次后提交候选路线。

`ClientConfig.endpoint` 从本地配置读取。[高德快速接入文档](https://lbs.amap.com/api/mcp-server/gettingstarted) 提供 Streamable HTTP 地址格式。`Discovery` 保存实际服务协议版本、身份和工具 schema，作为服务验收证据。

2025-11-25 使用 initialize、initialized 与服务返回的会话标识；后续请求携带协商版本和会话头。[MCP 2025-11-25 传输规范](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)

2026-07-28 将协议版本、客户端能力及身份放入每次请求的 `params._meta`，HTTP 头镜像版本、方法及工具名称。响应按 `resultType=complete` 解析。[MCP 基础消息](https://modelcontextprotocol.io/specification/2026-07-28/basic)、[Streamable HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)

ESP HTTPS 使用证书包和主机名校验，连接超时 5 s，读取窗口 15 s，最多两次重试，重试退避为 500 ms、1000 ms。请求在独立网络任务执行，交换摘要回调提供服务别名、方法、请求 ID、HTTP 状态、尝试次数、耗时和响应长度。[ESP-IDF 5.5.5 HTTP 客户端](https://docs.espressif.com/projects/esp-idf/en/v5.5.5/esp32s3/api-reference/protocols/esp_http_client.html)

主机夹具使用 synthetic 服务身份，覆盖协议生命周期、工具发现、候选路线和请求代次。真实服务的能力登记由 `discover()` 的设备日志关联验收。
