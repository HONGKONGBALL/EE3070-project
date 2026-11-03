# Pi V2：运行与交接

当前入口：`.venv/bin/python -m cane.vision.v2 --config config/camera.v2.toml`。
这是一套在原仓库上扩展的单摄像头程序；能力是否通过验收见 [V2 验证记录](pi-v2-validation.md)。

用户不提供场景时，可直接使用已准备的 89 张公开路面照片与回放视频（62 开发、27 新留出）。专用积水实验通过 `tools/check_v2_scene_replay.py --puddle` 显式运行，见 [积水研究与复现](pi-v2-puddle-research.md)。该实验结束会自动恢复原摄像头及 distance=none，不需要重刷 SD 卡。

## 当前设备

Pi 用户目录：`~/EE3070-project/`。C270 与 HyperX USB 接收器均接 Pi。
电脑只看画面；不用把耳机接电脑。Pi 使用系统 OpenCV 4.10.0 / Python 3.13.5。

本机服务：`smart-cane-camera`，当前已切到 V2 用户服务；登录用户会话后启动。

```sh
systemctl --user status smart-cane-camera --no-pager
systemctl --user restart smart-cane-camera
systemctl --user stop smart-cane-camera
```

停止服务后才可用前台命令运行，避免两个程序争抢摄像头。
Pi 浏览器用 `http://127.0.0.1:8000/`。Windows 建立转发后用 `http://127.0.0.1:8010/`：

```sh
ssh -N -L 127.0.0.1:8010:127.0.0.1:8000 gp7_3070@192.168.1.99
```

换网后核对 IP；如果 VPN 导致本地地址走错接口，可用 SSH 的 `-b <电脑Wi-Fi地址>` 选择本地来源，不必关掉整台电脑的 VPN 或修改全局路由。
服务仅绑定 Pi localhost；未公开给整个局域网。页面仍是视频、一行状态和截图，没有增加复杂控制台。

## 距离模式

默认 `distance.mode=none`：普通物体继续显示框，但**不发普通物体语音**；有效地面风险可以独立触发。

显式模拟（先停止后台服务）：

```sh
.venv/bin/python -m cane.vision.v2 --distance mock
```

页面和视频标 `MOCK DISTANCE`。固定脚本在 `config/scenarios/approach.json`，250→180→120→90→50 cm。默认没有校验过的对象关联，近距离应说 **Obstacle ahead**，不会任意叫成 Chair。

脚本单位 cm，程序内部统一 m。按 100 cm 进入、120 cm 退出、两个不同样本确认，带冷却。演示阈值不是安全距离。

真实 Pico 模式只预留并适配已有 USB JSONL：

```sh
.venv/bin/python -m cane.vision.v2 --distance pico
```

运行前必须将配置中的串口占位地址改为实际设备，并与队友核对方向、协议和安装关系。
本版本只读已有 front 距离通道，不自行融合 TFmini/超声波，也不驱动震动。仅按旧协议发送心跳；硬件联调未完成。
没有已验证映射时始终用通用提醒；真实连接失败不会自动换成模拟。

## 模型与依赖

Pi 仅增加固定的 ONNX Runtime CPU；不安装 PyTorch、Ultralytics 或训练代码。

```sh
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -e '.[camera-v2]'
.venv/bin/python tools/prepare_camera_model.py
```

系统 OpenCV/venv 应先具备，沿用现有 Pi 环境。V1 COCO 模型继续按原工具校验。

新增地面权重在 Windows 的独立 CPU 转换环境生成，下载/缓存均放 D 盘：

```powershell
$env:YOLO_CONFIG_DIR = 'D:\SmartCaneRuntime\ultralytics-settings'
$env:PIP_CACHE_DIR = 'D:\SmartCaneRuntime\pip-cache'
python -m venv D:\SmartCaneRuntime\export-venv
D:\SmartCaneRuntime\export-venv\Scripts\python.exe -m pip install torch==2.14.1+cpu torchvision==0.29.1+cpu --index-url https://download.pytorch.org/whl/cpu
D:\SmartCaneRuntime\export-venv\Scripts\python.exe -m pip install -r tools/requirements-camera-export.txt
D:\SmartCaneRuntime\export-venv\Scripts\python.exe tools/prepare_v2_models.py --export
```

它只取得现成权重并导出 NCNN，没有训练。下载工具核对固定 revision 与 SHA-256，失败即停止。
将生成目录中的以下文件复制到 Pi `models/v2/` 对应目录：

- `road-distress/manifest.json` 与 `road-distress/best_ncnn_model/model.ncnn.param`、`model.ncnn.bin`。
- `pothole/manifest.json` 与 `pothole/best_ncnn_model/model.ncnn.param`、`model.ncnn.bin`。
- `segformer/model.onnx`、`config.json`、`preprocessor_config.json`、`manifest.json`。

不要复制 `.pt`、PNNX 中间产物或整个训练环境到 Pi。ONNX YOLO 适配器仅用于本轮转换对照，生产 YOLO 固定 NCNN，不自动切后端。

## 英语语音与回放

在 Windows 项目根目录生成固定 WAV：

```powershell
Get-Content tools/gen_camera_prompts.ps1 -Raw | powershell.exe -NoProfile -Command -
```

复制 `audio/en/*.wav` 到 Pi。同一 PipeWire 输出由唯一仲裁器调用，支持停止/抢占；不录麦克风、不做云端 TTS。

无音响测试用 `--no-audio`，仍记录事件，明确 audio disabled。
离线视频用 `--replay /absolute/path/video.mp4`，明确标 replay；结束即停止提供旧图像，不伪装实时相机。

```sh
.venv/bin/python -m cane.vision.v2 --replay /path/to/test.mp4 --no-audio
```

### 公开场景模拟

用户无需提供路面素材。初版 19 张照片对应 `config/simulation-scenes.json`、114 秒视频；扩展的 51 张对应 `config/puddle-simulation-scenes.json`、306 秒视频。两者均保留来源、许可、SHA-256 与开发标签，详见 [场景模拟](pi-v2-simulation.md)。

在 Windows 构建（下载、图片和视频均留在 D 盘，不进 Git）：

```powershell
D:\SmartCaneRuntime\.venv\Scripts\python.exe -X utf8 tools/build_scene_replay.py
```

扩展版（已具备合并素材目录时，直接使用缓存，不重新下载）：

```powershell
D:\SmartCaneRuntime\.venv\Scripts\python.exe -X utf8 tools/build_scene_replay.py --catalog config/puddle-simulation-scenes.json --assets D:/SmartCaneRuntime/testdata/combined-simulation --output D:/SmartCaneRuntime/simulation/puddle-scenes.avi
```

将 `D:\SmartCaneRuntime\simulation\outdoor-scenes.avi` 和同名 `.json` 复制到 Pi 的 `testdata/simulation/`。在 Pi 项目目录执行：

```sh
systemd-run --user --collect --unit=smart-cane-v2-scene-replay --property=RuntimeMaxSec=200 --property=TimeoutStopSec=30 --working-directory="$PWD" "$PWD/.venv/bin/python" tools/check_v2_scene_replay.py
```

程序临时切换同一服务到 replay，仍用原 8000/电脑转发 8010 端口，结束后恢复 live_camera/none。它不另开一个相机/常驻模型进程；跨照片切换会清除旧检测、跟踪与待播语音。真实 Pico 不允许和录制视频混用。报告 `logs/v2-scene-replay.json` 保存每张图的实际检测/提醒与恢复结果。

回放配置与用户服务覆盖现在只写 `/run/user/<uid>/` 的临时文件，**不改写 `config/camera.v2.toml` 或永久服务文件**。正常结束移除覆盖、重新加载服务；断电重启时这些内存文件消失，仍从基础配置启动。报告核对基础配置前后哈希、临时覆盖移除和摄像头恢复结果。

显式命名距离夹具只允许 replay + mock，不是真实传感器标定：

```sh
.venv/bin/python tools/check_v2_scene_replay.py --fixture config/scenarios/named-person-fixture.toml --video testdata/simulation/named-person.avi --out logs/v2-named-person-replay.json
```

该夹具使用固定照片中的中央行人与明确声明的模拟波束区域。照片中的其他人不会因“最大框/唯一框”被任意点名；90 cm 抖动阶段不重复，退出后重新接近才允许新事件。视频和距离都持续标为模拟，不能把夹具投影复制成真实 Pico 安装标定。

## 状态与验证

`/status` 包含每个模型的 FPS、推理耗时、结果帧龄、真实/模拟来源及未验证能力。
`ground_surface_observation` 绑定语义分析帧、采集/观察/失效时间和模型版本。它只表示图像关注区的道路类别，不能从未检出风险推断路面正常或安全。
`/frame.jpg`、`/snapshot.jpg` 和 `POST /capture` 沿用原接口；没有新帧返回 503。
`logs/camera-<run_id>.jsonl` 记录模型 revision、事件和播放过程。
看到 `normal_pavement_detected` 只代表语义标签，不代表路径安全。

```sh
.venv/bin/python -m pytest -q
.venv/bin/python tools/check_v2_mock_runtime.py
.venv/bin/python tools/check_camera_runtime.py --duration 600 --out logs/v2-stability.json
```

Mock 实机检查约 18 秒，会重启服务临时启用模拟，结束后恢复 none；勿在稳定性监测期间同时执行。
持续运行工具拒绝超过 600 秒，不默认安排 20 分钟测试。它记录各任务频率、帧龄、CPU/RSS、温度及欠压，不能代替户外准确率或真实听音验收。

仅有标定且朝向/来源/calibration_id 均一致时，近距离门控会留最多 100 ms 的逻辑窗口等正在完成的视觉结果；仍无可靠匹配则通用提示，不会扩大关联时效。实际耗时包含渲染调度，由日志记录。

五任务、5 分钟真实摄像头背景观察（无需模拟距离，不保存连续视频）：

```sh
.venv/bin/python tools/check_v2_scene_replay.py --background 300 --puddle --out logs/v2-background-puddle.json
```

时间分析按同一次 run 的 event_id/playback_id 连接，旧日志缺字段则报告不可用：

```sh
.venv/bin/python tools/summarize_v2_timing.py logs/camera-RUN_ID.jsonl --out logs/timing-RUN_ID.json
```

这里测到的是软件进程时间，不是耳机声波或浏览器绘制。完整最新测试及未完成项见 [恢复实施记录](pi-v2-progress-20261027.md)。

图片回放新默认 FFV1，保留原始像素并验证解码；需要复现旧有损条件时，构建视频必须显式 `--codec MJPG` 并使用不同输出名。道路三类最新未通过结果、标签勘误和计分命令见 [道路验证](pi-v2-road-validation.md)。

另有显式 `--pothole-yolox` 的NCNN候选工程回放，默认实时模型不切换。实际转换、准备命令、105秒压力样本和仍未通过的6/10提醒、2/11误报见 [YOLOX研究](pi-v2-yolox-research.md)。不要把原图候选9/10当最终播报正确率；检查器结束后恢复原配置。

普通声音 3 秒间隔由唯一仲裁器控制，风险确认与距离决策不等待间隔。队列仍至多 2 个请求，未播放/过期/中断不计完整播放；紧急事件可越过普通间隔抢占。`recent_events` 是事件历史，完整播放结果要按同一 run 的 event_id/playback_id 查日志，不能仅凭画面短句认定声音播完。

## 画面受限状态

`/status.visual_quality` 与视频底栏显示 `usable` / `limited` / `unavailable`、原因及像素指标。每个任务也记录 `prediction_skipped` 与分析帧的质量。极暗、近乎全白、几乎无细节或极低锐度时，停止对应帧的视觉分析；清除跟踪、待播视觉提醒，停止正在播放但已无视觉依据的提醒。有效距离的通用 Obstacle ahead 不依赖画质，仍可独立触发。预览继续显示当前画面，模型没有崩溃时不会冒充模型故障。

恢复后必须等待在最后受限帧之后采集、且质量可用的新模型结果；旧框和旧地面语义不会重新出现。质量不足时分析 FPS 为 0、inference_ms 为 null；不能将跳过推理计入模型 FPS。

固定像素门槛是实验工程过滤，不是经过室外标定的遮挡/运动检测算法。清晰但低纹理的墙面可能受限，部分遮挡、运动模糊或逆光仍可能漏过。89 张回放原图未被拦截，5 种模拟退化均被限制，只证明这组诊断；现场照明和拐杖摆动质量验收仍未完成。证据 `D:\SmartCaneRuntime\evidence\v2-quality-scenes.json`。

## 回退 V1

```sh
cp deploy/camera-user.service ~/.config/systemd/user/smart-cane-camera.service
systemctl --user daemon-reload
systemctl --user restart smart-cane-camera
```

恢复 V2 用同样步骤复制 `deploy/camera-v2-user.service`。V1 是显式回归入口；V2 不因距离缺失而自动退回 V1 播报规则。
Pi 首次改动前还保留了 `~/smart-cane-backups/before-v2-20261026-192155.tar.gz` 源码备份和 `camera-v1.service`。
