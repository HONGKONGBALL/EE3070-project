# 路线摘要与几何验收记录

真实高德 MCP 响应已形成一条香港步行路线摘要：起点 `114.177692,22.332921`，终点 `114.179502,22.333517`，总长 235 m，共 7 步；分段距离合计 235 m。摘要保留指令、朝向、道路、距离和时长，坐标标签为 GCJ02。

用户授权的一次官方步行几何查询于 2026-11-17 23:44:21（UTC+8）返回 HTTP 200，服务体为 `status=0`、`infocode=10045`、`info=USER_ABROAD_DAILY_QUERY_OVER_LIMIT`。Provider 边界将该结果分类为 `ServiceState::Quota`，完整保留服务码与说明。[高德错误码文档](https://lbs.amap.com/api/webservice/guide/tools/info)

| 证据 | 路径 | SHA-256 |
| --- | --- | --- |
| MCP 路线摘要 | `fixtures/routes/candidate_route01.json` | `6fad80010b0021354ce646513c544af90ccee76b30d969a158d4c51079f065f9` |
| 官方几何查询响应 | `evidence/T03/rest_geometry_authorized_once/response.json` | `ae38f18f527cb6a26453b39769ab325e27c5cf0af961b02c5f3a7dcab1ddb030` |
| HTTP 交换记录 | `evidence/T03/rest_geometry_authorized_once/exchange.json` | 由工程证据 manifest 登记 |

`parse_walking_summary()` 解析起终点、总距离、7 步元数据和几何准备状态。导航候选通过 `parse_walking_response()` 检查完整折线；当前摘要得到 `ROUTE_GEOMETRY_REQUIRED`，原有候选保持其身份和内容。服务配额响应得到 `SERVICE_QUOTA`，MCP 客户端映射为 `MCP_QUOTA`。

离线断言分别覆盖真实摘要、真实 10045 响应、synthetic 成功服务体、synthetic 几何路线及原子候选提交。真实证据与 synthetic 夹具按各自来源判定。

Provider 请求坐标以最多六位小数序列化，符合[官方步行接口参数约定](https://lbs.amap.com/api/webservice/guide/api/direction)。几何候选后续按相同起终点、路线距离、分段和端点叠合完成核对，再通过已登记的坐标控制点策略提交导航核心。
