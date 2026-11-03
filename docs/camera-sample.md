# 单摄像头样品

这个入口只运行：Logitech 摄像头 → 已训练 YOLO11n → 检测框/英语提醒 → 可选耳机。
不需要 Pico、IMU、双目、地图、麦克风或云端服务。没有训练步骤。

本轮实际连接信息：Pi `gp7_3070@192.168.1.99`，64-bit Debian 13 / Python 3.13.5，
相机 `/dev/v4l/by-id/usb-046d_0825_5A2EE4D0-video-index0`。
USB 声音设备实际枚举名称是 **HyperX Cloud II Core Wireless**。
这些是本次查到的值，换网络后应重新确认 IP。

## Pi 上安装

在项目根目录执行；当前 Pi 已有系统 OpenCV、NumPy 和 venv 支持。

```sh
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -e '.[camera]'
.venv/bin/python tools/prepare_camera_model.py
```

系统没有 OpenCV/venv 时，先安装系统的 `python3-opencv python3-venv`。
不使用 sudo pip，不需要在 Pi 安装 PyTorch。

模型约 5 MB，下载后核查 SHA-256；校验失败立即停止，不静默改用其他模型。
依赖和模型就绪后，运行不依赖互联网。

## 启动与预览

在 Pi 的项目根目录：

```sh
.venv/bin/python -m cane.vision.demo --config config/camera.example.toml
```

在 **Pi 浏览器**打开 `http://127.0.0.1:8000/`。

在 Windows 查看时，在电脑上单独建立转发：

```sh
ssh -N -L 127.0.0.1:8010:127.0.0.1:8000 gp7_3070@192.168.1.99
```

保持转发连接，再在电脑浏览器打开 `http://127.0.0.1:8010/`。
页面只有实时画面、识别框、一行状态和截图链接；没有额外控制面板。
服务仅监听 Pi 本机，不默认公开给局域网。

`Ctrl+C` 停止前台程序并释放相机；再运行同一命令即可重启。
原项目 `python -m cane --sim` 会跳过真实视觉，不能代替上述命令。

## 识别与提醒

- 画面可标出 COCO 的现成类别。绿色框是检测；橙色框是前方区域中的受支持提醒目标。
- 自动提醒为人、椅子、背包；手提包和行李箱沿用英语 Bag 提示，但保留模型原始类别。区域用灰色梯形显示，只是图像关注区域，不是物理安全走廊。
- 同类连续命中 3 帧且在 1 秒窗口内才触发；持续存在只提示一次，离开约 2 秒再进入才可重新触发，同时受同类 8 秒冷却和全局 3 秒间隔限制。
- 图像出现英语提醒；声音使用同一事件和固定短语，没有实时 TTS 开销。
- 摄像头或推理没有新结果时隐藏旧画面、清空当前目标，不用旧帧继续提示。
- 采集线程只发布最新帧；模型不处理积压的视频队列。音频线程独立，队列最多一条且有有效期。
- 当前配置用 1 个推理线程、每秒最多识别 8 帧，以降低持续 CPU 负载；摄像头仍采集 15 帧，只处理最新帧。正常供电下已观察到约 15 FPS 的处理能力，但设备重复失联后先以稳定性为优先。
- 无米级距离，没有“安全通过”或绕行指令；水潭/坑洞/楼梯不在当前模型能力范围内。

## 英语耳机提示

`config/camera.example.toml` 的 `[audio] enabled = true` 启用耳机播放。
在 Pi 声音输出中选 HyperX，或用 `wpctl status` 确认默认输出。
默认用 Pi 上的 PipeWire `pw-play`。USB 接收器直接接 Pi；AirPods 等蓝牙耳机须先与 Pi 配对。V2 预览页可保存和切换两种输出，见 [耳机输出切换](pi-audio-outputs.md)。

固定 WAV 保存在 `audio/en/`，不提交 Git。在本机 Windows 项目根目录已实际生成过；
如需重新生成，可使用已安装 Microsoft David Desktop 声音：

```powershell
Get-Content tools/gen_camera_prompts.ps1 -Raw | powershell.exe -NoProfile -Command -
```

然后将 `audio/en/*.wav` 放到 Pi 对应目录。生成阶段在 Windows，播放阶段在 Pi，
不会采集麦克风或上传语音。

程序启动后可用以下命令测试实际输出；这只是声音设备测试，不是模型事件：

```sh
curl -X POST http://127.0.0.1:8000/test-audio
```

返回状态说明已调用播放队列；是否播放成功继续看 `/status` 的
`audio.last_played` 和 `audio.error`。`process_started_at` 是播放器进程启动时间，
不冒充实际声波起始时间。真实听感需由佩戴耳机的人验证。

## 状态、截图与事件

- `GET /status`：相机、新帧、当前检测、FPS、推理时间、采集到 JPEG 的 p95、音频状态和最近事件。
- `GET /frame.jpg`：最新标注帧；没有新帧返回 503。
- `POST /capture`：保存当前真实标注帧到 `captures/` 并返回实际路径。
- `GET /snapshot.jpg`：在浏览器打开最新标注帧。
- `logs/camera-<run>.jsonl`：每次运行的启动、提醒、错误和停止事件；默认不持续保存图像。
- `models/yolo11n/manifest.json`：实际模型来源、大小和 SHA-256。

当前端看到 `FPS` 时，这是处理完成的识别帧率，不是相机请求的 15 FPS。
网页仅使用一个 MJPEG 流和约每 700ms 一次的小状态请求。

本次已安装并启用用户级服务，当前用户会话启动后会启动摄像头程序：

```sh
systemctl --user status smart-cane-camera --no-pager
systemctl --user restart smart-cane-camera
systemctl --user stop smart-cane-camera
```

停止服务后才可按前面的命令前台启动，避免争用相机。
在新环境安装同一服务时，在项目根目录执行：

```sh
mkdir -p ~/.config/systemd/user
cp deploy/camera-user.service ~/.config/systemd/user/smart-cane-camera.service
systemctl --user daemon-reload
systemctl --user enable --now smart-cane-camera
```

服务假定项目在 `~/EE3070-project/`。这是用户会话服务，未配置无人登录时启动，
不保证尚未登录桌面或 SSH 时已运行。查看输出用
`journalctl --user -u smart-cane-camera -n 50 --no-pager`。

持续运行验证（在 Pi 上）：

```sh
.venv/bin/python tools/check_camera_runtime.py --duration 1200
```

结果写入 `logs/camera-stability.json`。这是健康、时效和资源记录，不能代替人/椅子/包的实物分类试次。

## 模型扩展

`Detector.predict(image)` 返回 `label/confidence/bbox` 列表，框为原始画面坐标。
后续模型应适配到同一观察/事件链路，保留语义掩码时可以扩展字段，不更换整个服务。
来源、实际标签、许可与 Pi 测试通过后才能启用新场景；不会自动进入训练。

## 验证

```sh
.venv/bin/python -m pytest tests/test_camera_demo.py -q
.venv/bin/python -m pytest -q
```

前者验证坐标映射、类别、去重和区域规则；后者是已有项目回归。
两者都不代替真实相机/耳机、Pi 帧率或实验室类别验收。
当前实测情况见 `camera-validation.md`。
现场演示步骤和待填结果见 `demo-checklist.md`。
