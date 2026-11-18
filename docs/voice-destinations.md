# 语音目的地

## 用 Mac 和 Pico 联调

用 USB 数据线连接 Pico。整机固件把 K2 的短按发给 Mac，K2 接 Pico 的 GP11 和 GND。电脑也可直接通过麦克风输入。

在 `smart_cane_mac` 目录编译 Apple 地图搜索程序，再启动 Mac 联调。附近搜索随当前出发点调整，输入地名或场所也可直接搜索。

```zsh
xcrun swiftc -O tools/mac_place_search.swift -o .venv/bin/cane-map-search
.venv/bin/python -m cane.mac.host --voice-bench --apple-places .venv/bin/cane-map-search
```

打开 [语音目的地](http://127.0.0.1:8765/)。短按 K2 或点「说出目的地」开始录音，也可以开启语音唤醒后说「你好导航」。听到「请说目的地」后说完整地名。说完再短按 K2 或点「结束录音」，识别后按名称和地址选择候选。首次录音时按 macOS 的提示授权麦克风。

在同一局域网使用 iPhone 定位时，将 Mac 联调以 `--phone` 启动，通过主机配置指定监听地址、TLS 证书和密钥文件，并从本机环境提供配对口令 `CANE_PHONE_TOKEN`。在 iPhone「步行导航」打开专用配对链接，点「开始共享」，按 iOS 提示授权定位和本地网络。地点候选会按出发点的直线距离排列。可以在网页点「修改出发点」或说「把定位改到深圳湾口岸」，选中地点后重新排序。Apple 地图接收关键词和出发点附近的粗略网格，精确坐标留在 Mac 计算距离。

## 用 Pi 5 运行整机

将麦克风通过 USB 声卡接入 Pi 5，将 Pico 通过 USB 数据线接入 Pi 5。整机的 K2「目的地」键接在 J11，使用 Pico 的 GP11（J11.1）和 GND（J11.2）。用 `arecord -l` 核对麦克风，再把所选输入设备填入主机配置的 `voice.device`。

在 Pi 主机的 `config/config.toml` 中合并以下设置。普通话模型目录为 `models/vosk-cmn`，语音运行依赖为 Vosk 与 `arecord`。

```toml
[voice]
enabled = true
backend = "vosk"
device = "default"
record_s = 4.0
vosk_model_dir = "models"
wake_enabled = true
wake_phrase = "你好导航"
candidate_selection = true
```

从整机工程目录运行 `python -m cane --config config/config.toml`，打开 Pi 本机的诊断页 `http://127.0.0.1:8080/`。短按 K2 或说「你好导航」，听到录音提示后，说出目的地，例如「我想去香港城市大学」。在「候选地点」中按名称和地址选择一项。选中后，用 K1 或网页的「确认 / 继续」确认开始。

使用预设地点时，在 `nav.destinations` 添加地点、别名和已核验的 WGS84 坐标，地址可填入 `address`。要搜索高德地点，在同一 `[voice]` 段设置 `places_python` 和 `places_script`，分别指向导航模块的 Python 3.13 环境和 `navigation_24h/tools/poi_search.py`。从运行环境提供 `AMAP_MAPS_API_KEY`，并用 `[nav] city` 设置搜索城市。高德候选先显示名称和地址，用户点选后再查询坐标。

测试正式路线前，先核对当地控制点、入口和高德坐标。通过实地核验后，用 `[nav] provider_coordinates_verified` 登记核验结果；用户确认后，主机按现有位置和传感器条件启动步行规划。
