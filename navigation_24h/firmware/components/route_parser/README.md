# MCP 步行路线解析

`RpcStream` 接收任意长度网络分片，按 JSON 或 SSE 包装还原关联的 JSON-RPC 响应。SSE 支持 CR、LF、CRLF、注释、多行 data 和进度通知。输入检查 UTF-8、JSON 深度和消息 ID。

`parse_walking_response()` 解析 structuredContent 或文本内容中的高德 `route.paths[].steps[]`、`paths[].steps[]` 结构，输出 `ParsedRoute`。候选路线完成全部检查后替换输出，路线记录坐标标签、总距离、步骤动作和确认点。

容量为 256 KiB 响应、2048 折线点、256 步骤。Amap 路线使用 `GCJ02` 标签；调用者在 Provider 边界执行已核对的坐标转换，再向导航核心提交 `WGS84` 路线。

主机测试入口：

```sh
navigation_24h/.venv/bin/cmake -S navigation_24h/tests/parser -B navigation_24h/build/parser
navigation_24h/.venv/bin/cmake --build navigation_24h/build/parser
navigation_24h/.venv/bin/ctest --test-dir navigation_24h/build/parser --output-on-failure
```

`third_party/` 保存官方 cJSON 1.7.19 源码及 MIT 许可证，主机测试使用同一解析器。[cJSON 官方源码](https://github.com/DaveGamble/cJSON/tree/v1.7.19)
