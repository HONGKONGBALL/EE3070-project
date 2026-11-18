# EE3070 智能盲杖 · Mac 主机版

这份代码把 Mac 作为整机主机，包含主循环、传感器判断、输出仲裁、导航、语音、网页和诊断。软件模拟使用合成传感器与合成坐标，明确标记为模拟；真实模式不会自动回退到模拟。

## 启动

双击 `start_mac_sim.command`，打开 <http://127.0.0.1:8765/>。终端窗口保持开启，Ctrl-C 停止。

命令行等价入口：

```sh
cd /Users/psyche/Documents/ChatGPT/EE3070/smart_cane_mac
.venv/bin/python -m cane.mac.host --sim
```

`--quiet` 只在控制台记录提示，不播放声音。默认使用 Mac 的 `afplay` 播放本地 WAV。已生成普通话、粤语、英语共 189 条提示音。

## 演练流程

1. 选择“前方近距离障碍”等场景，核对距离、风险和实际输出记录。
2. 障碍存在时暂停并尝试恢复，界面应显示恢复受限。
3. 选择“恢复正常观测”，等安全保持期结束，再恢复。
4. 选择“离线导航路线”，核对“演示终点”，点击“确认 / 继续”启动。
5. 选择“路口人工判断”，等待人工确认。未确认时直接运行“到达”会被拒绝。
6. 自行确认过街后点击“确认 / 继续”，再运行“到达目的地”。

演示定位持续发送合成位置，不代表实际手机位置。演示也不会自动越过人工确认。上/下坡与横坡场景使用合成点云运行真实地面拟合代码；不是摄像头测量结果。

## 语音和视觉

- “主机离线语音输入”使用 Mac 麦克风和本地 Vosk；首次录音可能需要系统权限。只有用户主动点击后才录音。普通话/英语模型已安装。
- 粤语本地 ASR 当前不可用，界面明确禁用；粤语语音输出和文字目的地输入仍可用。
- 浏览器麦克风按钮可能使用浏览器联网识别服务，页面有说明。
- 文字识别结果或语音目的地都先读回，再等待确认。取消、确认、重试采用同一语义入口。
- 可选摄像头识别：`.venv/bin/python -m cane.mac.host --sim --camera --camera-index 0`。摄像头默认不打开；本轮没有请求相机或麦克风采集权限。
- 单目 YOLO 提示人、椅子、包；不输出米级距离或可通行结论。视觉提示也进入唯一仲裁器，旧观测会撤销；低压策略可暂停推理。

## 后续连接 Pico

停止原测距网页或其他串口程序，然后双击 `start_mac_hardware.command`，或执行：

```sh
.venv/bin/python -m cane.mac.host --port auto
```

多个 USB 串口时，必须用 `--port /dev/cu.usbmodem...` 指定设备。

- 整机 JSON 协议：匹配 `pico/` 中固件。收到有效 JSON 标识后，才允许心跳、振动、蜂鸣器和灯光命令。
- 旧 `TF_DATA` / `US_DATA` 测距协议：兼容接收，TFmini 默认按厘米解释，每路独立超时；此模式不发送控制命令。若 TFmini 实际配置为毫米，需先统一配置或扩展接收单位配置，不能直接使用默认厘米换算。
- 未识别协议时不发命令，不自动安装固件、不自动模拟。板内文件本轮未修改。
- Mac 没有 Pi 的 I2C 总线；没有接入真实运动源和标定双目时，相关朝向、恢复和坡向能力保持受限。
- 原组件测试工具仍在上级目录 `distance_mac_viewer/`，与整机串口互斥使用。

## 真实手机定位与高德

真实导航需要 `AMAP_KEY`，定位页需要 `CANE_PHONE_TOKEN`；二者只从环境读取，页面与日志不显示值。本轮未使用线上高德密钥或发送真实定位。

手机定位是可选服务；启用前准备 `certs/cane.crt` / `certs/cane.key`，再用 `--phone`。例如给手机热点中的 Mac IP 生成证书：

```sh
bash tools/make_cert.sh MAC_IP_ON_HOTSPOT
.venv/bin/python -m cane.mac.host --phone
```

`--phone` 监听配置的 HTTPS 8443 端口；诊断页默认仍限制本机。手机连接、证书信任、定位授权和线上高德覆盖需后续实测。不要把令牌或密钥提交到仓库。

## 重新安装与验证

已有 `.venv` 可直接运行。重新创建环境时使用项目内的 uv（也可使用已安装的 uv）：

```sh
.tools/uv venv --python 3.12 .venv
.tools/uv pip install --python .venv/bin/python -r requirements-mac.lock
.tools/uv pip install --python .venv/bin/python --no-deps -e .
.venv/bin/python tools/gen_mac_prompts.py --out audio
.venv/bin/python -m pytest -q
node --test tests/dashboard_client.test.mjs
```

推理模型、语音模型和音频已放在当前项目目录，Git 默认忽略它们。迁移机器时需单独复制 `models/`、`audio/`，或按原项目工具与 Vosk 官方来源重新准备。测试中的本地 HTTP/PTY 需要系统允许本机端口和伪终端。

逐项范围与证据见 `output/mac-verification/RESULTS.md`。完整人体运动跟踪、真实连续局部绕行、实际测距准确性、掉电告警和续航尚未完成硬件验收。
