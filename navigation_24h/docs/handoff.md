# 导航模块工程交接

工程提供原生 iPhone 会话 App、ESP-IDF 固件、共享 C++ 导航核心、主机/Pico 接口、独立轨迹夹具和证据工具。跨端字段与 GATT UUID 见 `docs/protocol.md`，构建和设备步骤见 `docs/device_setup.md`。

`progress.json` 保存 T01–T24 的实际执行状态、命令、退出码和下一步；`checkpoint.md` 给出继续入口。`config/test_registry.json` 关联 60 项用例的来源、阈值和执行模式。`evidence/acceptance.json` 与 `evidence/requirement_coverage.json` 汇总当前用例结果与 24 项需求覆盖，审核时逐项打开所引证据。

主机通过 USB CDC JSONL 接收当前导航事件并 ACK。`tools/host_sim.py` 交换单调时钟、核对会话和路线版本、事件 TTL、位置年龄、许可与重复编号。`tools/pico_bridge.py` 将独立 Pico 按钮与当前有效事件关联，生成路口、过街或入口确认。手机和主机恢复连接后交换摘要，再以新位置继续。

`tools/replay.py` 从采集 JSONL 重建观测时间线、分段和资源/时延分位数；`tools/metrics.py` 独立分析设备指标。轨迹断言通过 `regression --suite host` 执行，设备与现场用例依据关联日志、操作者记录和审核确认回写。

```sh
python3 navigation_24h/tools/navctl.py status
python3 navigation_24h/tools/navctl.py regression --suite host
python3 navigation_24h/tools/navctl.py regression --suite all
python3 navigation_24h/tools/navctl.py package
```

`release/navigation_24h.zip` 是生成时刻的工程快照，包含工程源码、配置模板、夹具、实际构建产物、用例记录和证据。`release/manifest.json` 列出文件字节数、SHA-256 和压缩包摘要。验收结论取生成时的实际用例记录；任务继续推进后重新运行 `package` 更新快照。
