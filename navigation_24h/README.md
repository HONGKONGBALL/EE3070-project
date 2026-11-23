# 便携步行导航

iPhone 负责语音识别、候选搜索、定位与播报。Pico 通过 UART 发出按键事件，XIAO ESP32S3 通过 BLE 转发给手机，并通过 Wi-Fi 请求高德步行路线。

1. 短按 K2，听到提示后说出目的地，说完再短按 K2。
2. 按 K3 切换候选。附近候选按 iPhone 当前位置的直线距离排序，并播报距离。
3. 核对地点后按 K1 规划步行路线。

高德 MCP 提供路线步骤。步骤摘要缺少折线时，设备通过高德步行接口补取，并核对起终点、分段和距离。深圳公开控制点见 [坐标样例](fixtures/coordinates/shenzhen-public-controls.json)。

- [接线、刷写与配对](docs/device_setup.md)
- [iPhone 工程](ios/README.md)
- [通信协议](docs/protocol.md)

## 本地回归

~~~sh
cd navigation_24h
uv sync --locked
uv run cmake -S . -B build/host -G Ninja
uv run cmake --build build/host
uv run ctest --test-dir build/host --output-on-failure
uv run pytest
swift test --package-path ios
~~~
