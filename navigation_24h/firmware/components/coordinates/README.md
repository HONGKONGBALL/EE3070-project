# Provider 坐标边界

导航核心统一使用 WGS84。Provider 边界按本地确认配置转换起点、目的地与返回折线，每个点记录来源坐标系及转换次数。

| 配置 | Provider 坐标系 | 请求边界 | 路线边界 |
| --- | --- | --- | --- |
| `verified_identity` | WGS84 | 保持坐标，转换次数 0 | 保持坐标，转换次数 0 |
| `verified_gcj02` | GCJ02 | WGS84 → GCJ02，转换次数 1 | GCJ02 → WGS84，转换次数 1 |
| `review_required` | 按 Provider 样例登记 | 等待控制点确认 | 等待控制点确认 |

启用条件为：实际路线样例的坐标标签与 `route_frame` 一致，现场控制点完成叠合核对，并将证据编号登记为 `coord_ref`。NVS 配置由设备确认流程保存。香港测试区域使用三条短路线的控制点、路口、连接点和入口完成核对。

正向算法移植自 [wandergis/coordtransform](https://github.com/wandergis/coordtransform/blob/606c6f3b57b6f1d60458793fea39928d2b11b637/index.js)，保留 MIT 许可证与来源文件。逆向通过同一正向函数执行最多 12 次数值迭代，边界检查回代误差。源码版本、SHA-256、迭代参数和区域边界见 `third_party/provenance.json`。

测试包含上游 README 公布的北京参考向量、独立 JavaScript 实现生成的香港算法参考点、往返精度、坐标标签与逐点转换次数。算法参考点用于验证实现一致性；现场控制点证据决定实际 Provider 配置。

[高德坐标转换文档](https://lbs.amap.com/api/webservice/guide/api/convert) 定义 GPS 等来源坐标至高德坐标的服务接口。真实服务样例与控制点记录共同确定本项目的坐标策略。

设备日志 `coordinate_boundary` 关联会话、请求、控制点证据编号、Provider 标签和转换次数；规范化路线经当前请求代次检查后提交导航核心。
