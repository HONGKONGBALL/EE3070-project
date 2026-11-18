# 设备联调入口

将 `config/local.example.json` 复制为 `config/local.json`，登记 iPhone UDID、签名 Team、XIAO 与 Pico 的独立串口、Pico 按钮引脚、网络名称和设备操作者。Wi-Fi 密码与高德 Web 服务 Key 从模板指定的环境变量读取，服务能力由 `probe-mcp` 保存。

通过 USB 连接 ESP32，并确认串口状态为 IDLE。运行 `configure-wifi` 保存本地网络参数，再运行 `provision` 将参数写入设备。主机会保存与命令 ID 对应的 `CONFIGURED_RESTART_REQUIRED` ACK；随后重启设备，核对 Wi-Fi 状态与 MCP 能力发现记录。

在 Xcode 中打开 `ios/NavigationCompanion.xcodeproj`，选择开发 Team，连接已开启开发者模式的 iPhone。手机首次启动后授予蓝牙和精确定位权限。首次绑定时，保持 ESP32 的 USB 连接，在工作区运行本地确认服务：

```sh
navigation_24h/.venv/bin/python navigation_24h/tools/pairing_web.py
```

打开终端显示的本机页面，点击“开始配对”，再在 iPhone App 中点击“连接并绑定 EE3070 Navigation”。逐位核对手机与页面的六位数字；一致时，先在手机点击“配对”，再点击网页的“确认数字一致”。页面实时显示本轮剩余时间与设备确认状态，记录保存在 `evidence/T10/pairing_web_*`。看到 App 显示“设备绑定完成”后，确认目的地名称与坐标。结束本地服务时，在启动服务的终端按 `Ctrl+C`。

按顺序执行构建与安装：

```sh
python3 navigation_24h/tools/navctl.py env
python3 navigation_24h/tools/navctl.py check --task T02
python3 navigation_24h/tools/navctl.py build --target esp32
python3 navigation_24h/tools/navctl.py build --target ios-device
python3 navigation_24h/tools/navctl.py flash-esp32
python3 navigation_24h/tools/navctl.py install-ios
python3 navigation_24h/tools/navctl.py probe-mcp --routes config/field_routes.json
```

Pico 两侧排针与金色焊盘逐个焊牢后，插入面包板。焊接过程可参考[树莓派官方图示](https://magazine.raspberrypi.com/articles/how-to-solder-gpio-pin-headers-to-raspberry-pi-pico)。Pico 按钮接登记 GPIO 与 GND，固件使用内部上拉和 40 ms 稳定去抖。将 `pico/confirm_button.py` 的 `BUTTON_PIN` 与本地配置对齐，再通过 MicroPython 编辑器保存到 Pico 的 `main.py`。电脑桥接器读取 Pico 的独立 USB 按钮序号，与当前会话、路线、路口事件及有效期关联。

```sh
uv run --project navigation_24h navigation_24h/tools/pico_bridge.py
```

H18–H20 设备采集使用 `config/soak_schedule.json`：移动锁屏 30 分钟、切换应用移动 30 分钟、省电锁屏移动 30 分钟、静止锁屏 15 分钟、恢复移动 15 分钟。操作者按分段切换场景，串口记录保留主机时刻、设备原文、分段标签和输入摘要。

```sh
python3 navigation_24h/tools/navctl.py soak --minutes 120 --schedule config/soak_schedule.json
```

H20–H22 使用同一区域三条 150–300 m 短路线。在 `config/field_routes.json` 登记 `id`、`origin`、`destination`、`coord_frame` 和 `operator_confirmed`，核对路口、平行道路、高程连接点与入口。每条路线各执行两轮：

```sh
python3 navigation_24h/tools/navctl.py field --routes config/field_routes.json --rounds 2
uv run --project navigation_24h navigation_24h/tools/capture.py route01 --round-number 1 --minutes 8
```

每轮将手机导出的导航记录与 ESP32 采集目录放在对应路线记录中，补充操作者关键点确认、App/固件摘要与配置版本，然后按用例阈值复核。
