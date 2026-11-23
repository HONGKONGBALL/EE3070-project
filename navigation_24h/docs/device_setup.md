# 设备部署

准备 Xcode、带 MicroPython 的 Pico、带 8 MB PSRAM 的 XIAO ESP32S3、ESP-IDF 5.5、Python 3.13 和 uv。命令在仓库根目录执行。

| 用途 | Pico 端 | 对端 |
| --- | --- | --- |
| K1 确认 | GP10 | 按钮另一端接 GND |
| K2 录音 | GP11 | 按钮另一端接 GND |
| K3 切换 | GP12 | 按钮另一端接 GND |
| UART TX | GP0 | XIAO D7（GPIO44，UART RX） |
| 共地 | GND | XIAO GND |

将 [buttons.py](../../pico/buttons.py) 保存到 Pico，再将 [portable_keys.py](../pico/portable_keys.py) 保存为 `main.py`。按钮使用 Pico 内部上拉，板卡使用 3.3 V 逻辑电平。

在已加载 ESP-IDF 环境的终端构建和刷写，替换示例串口。

~~~sh
idf.py -C navigation_24h/firmware build
idf.py -C navigation_24h/firmware -p /dev/cu.usbmodemXXXX flash
uv sync --project navigation_24h --locked
cp navigation_24h/config/local.example.json navigation_24h/config/local.json
cp navigation_24h/config/secrets.example.env navigation_24h/config/secrets.env
~~~

在 `config/local.json` 填写 XIAO 串口 `esp32.port` 和网络名称 `wifi.ssid`，在 `config/secrets.env` 填写 `NAV_WIFI_PASSWORD` 与高德 Web 服务 `AMAP_MAPS_API_KEY`。使用目标区域的公开控制点和实际路线核对坐标转换，随后登记 `mcp.coordinate_control_reference`，将 `mcp.coordinate_policy` 设为 `verified_gcj02`、`mcp.coordinate_frame` 设为 `GCJ02`、`mcp.coordinate_verified` 设为 `true`。

确认设备处于 `IDLE` 后运行配置命令。收到 `CONFIGURED_RESTART_REQUIRED` 后重启设备，检查 Wi-Fi 和 MCP 状态。

~~~sh
uv run --project navigation_24h python navigation_24h/tools/navctl.py provision
~~~

打开 [iPhone 工程](../ios/README.md)，选择团队的签名 Team，在已开启开发者模式的 iPhone 上运行「步行导航」。首次使用时授权蓝牙、精确定位、麦克风和语音识别。

保持 XIAO 的 USB 连接，启动本机确认页。

~~~sh
uv run --project navigation_24h python navigation_24h/tools/pairing_web.py
~~~

在网页点击「开始配对」，随后在 App 中选择「连接并绑定 EE3070 Navigation」。核对两处六位数字，一致时先在 iPhone 确认配对，再在网页确认。看到 App 显示「设备绑定完成」后，保持 App 在前台，按 [按键操作说明](../README.md)开始导航。
