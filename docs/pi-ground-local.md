# 地面优先模式与本地开发

更新：2026年10月30日。

树莓派的默认视觉任务为地面优先。当前固定使用道路破损、YOLOX坑洞、地面语义和专用积水任务；积水只有存在相关候选时才运行一次上下文复核，没有自动备用算法。地面语义线程新增一次轻量楼梯场景复核，用于补充漏掉的楼梯、排除明显非楼梯背景；不播报上下方向。摄像头预览与推理分线程，各消费者只取最新帧。最新楼梯修改与结果见 [楼梯调试记录](pi-stair-debug-20261030.md)，此前积水、坑洞记录见 [路况校准记录](pi-ground-calibration.md)。

## 运行方式

`config/camera.v2.toml` 的 `[vision] mode = "ground"` 是新的默认值。在该模式下，普通物体检测线程不会启动，YOLO11n 权重不会加载，状态显示 objects=disabled。地面提醒继续不依赖 Pico。

新配置需要相应的 `models/v2/pothole-yolox-research`、`models/v2/puddle-calibrated` 和 `models/v2/stairs-context` 运行文件，不能只更新代码。楼梯复核沿用现有 ONNX Runtime，不增加 Pi 的依赖包。Pi 只需要选定的 NCNN、ONNX、manifest 和英语 WAV，不需要训练环境、文本编码器或原始 Torch 权重。

树莓派连回后，沿用现有服务即可读取该配置；更新文件、核对环境并重启后才算部署。手工运行方式：

```sh
.venv/bin/python -m cane.vision.v2 --config config/camera.v2.toml
```

如需对照之前的组合模式，显式指定 `--mode balanced`。不要同时启动服务与手动进程争用摄像头。

Windows 本地使用现有模型和视频，无需接摄像头：

```powershell
& 'D:\SmartCaneRuntime\.venv\Scripts\python.exe' -X utf8 -m cane.vision.v2 --config config/camera.v2.toml --models-root 'D:\SmartCaneRuntime\models' --replay 'D:\SmartCaneRuntime\simulation\road-lossless-check.avi' --no-audio
```

命令在项目根目录执行。浏览器打开 `http://127.0.0.1:8000/`；视频结束后预览停止。这里是视频回放，不是树莓派摄像头。

## 本地报告

无需启动网页服务即可用实际模型检查一段视频：

```powershell
& 'D:\SmartCaneRuntime\.venv\Scripts\python.exe' -X utf8 tools/check_ground_local.py --video 'D:\SmartCaneRuntime\simulation\road-lossless-check.avi' --timeline 'D:\SmartCaneRuntime\simulation\road-lossless-check.json' --models-root 'D:\SmartCaneRuntime\models' --seconds 45 --out 'D:\SmartCaneRuntime\evidence\ground-local-20261030.json'
```

普通视频可不传 `--timeline`。本地检查强制 ground、distance=none、audio=false；不连接 Pico、不修改 Pi 服务、不下载或训练模型。时长上限600秒，时间不足会记录未完成并返回失败。带时间表时，所有场景都必须被观察，且各场景必须观察到全部地面任务，才记流程完成；不表示准确率通过。

输出包括素材哈希、实际配置、各场景的检测结果与事件、逐任务过滤原因统计、错误及最后一张标注预览。标签只进入报告和场景边界，不用于模型推理或生成提醒。计数单位写明是抽样模型帧，不能当成独立测试样本数量。

## 诊断页面

现有预览页增加折叠的 Ground detection details。只有展开时才更新该表，不增加轮询接口或推理任务。后端每个任务最多返回20个去重候选的原因，复用现有模型和跟踪结果。

主要状态：没有候选、路径之外、上下文否决、等待地面上下文、等待连续观察、重复提醒冷却、已经处理。already_handled 只表示策略已处理，不证明用户听到了声音；实际播放情况仍查看音频日志。过期结果显示不可用，不当作没有风险。

## 本轮不作出的效果声明

没有训练模型权重。已选择既有 YOLOX 权重并调整置信度、上下文规则与积水复核文字原型；默认配置已启用对应任务。通用 water 不再直接触发积水提醒，道路模型也不能绕过专用模型发出坑洞提醒。不能将这些修改自动解释为准确率通过，实际结果和仍失败的场景见校准记录。下行台阶、落差和真实高低不平仍未解决。

本地验证不能证明 Pi 帧率、USB 摄像头、耳机声音、GY-521/TFmini 高差测量或室外前推识别效果。下一轮用诊断结果定位模型/上下文问题，再做相同素材对照和新场景验证。GY-521 硬件驱动不在本次改动内。
