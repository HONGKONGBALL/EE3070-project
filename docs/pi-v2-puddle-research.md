# 专用积水识别与公开照片模拟

日期：2026-10-27。实验功能；不等于户外通过，也不代表整个 V2 完成。

## 实际使用的预训练模型

检测器来自 [Ayush0000002/Puddle-detection-model](https://huggingface.co/Ayush0000002/Puddle-detection-model/tree/7a8ef53d4b0fee120c513ddb9889ce8cb2a999aa)，实际 checkpoint 的唯一类别是 `puddle`。固定 SHA-256 为 `e4f0843a4d6f81d16639c402380e91f24eb3e350182f13fed5f6b56a4223a526`。来源没有明确许可，当前只作本地研究，不把权重随项目发布。

上下文复核使用 [RuteNL/MobileCLIP2-S0-OpenCLIP-ONNX](https://huggingface.co/RuteNL/MobileCLIP2-S0-OpenCLIP-ONNX/tree/2a4361b87cd76f8493625cc31f36031ee9d52110)，源模型卡声明 apple-amlr。固定模型文件及生成文本原型均记录于运行目录 `manifest.json`；没有训练或微调。

Windows 下载、转换及文本编码由 `tools/prepare_puddle_pipeline.py` 完成。Pi 只部署 NCNN 检测器、浮点视觉编码器和 5×512 文本原型；不安装 Torch、Ultralytics、tokenizer 或文本编码器。五类提示词在 `config/puddle-verifier-prompts.json`。

## 固定推理方式

1. 按地面关注多边形的外接矩形裁剪同一帧，640 输入的 NCNN 模型输出积水候选，置信度门槛 0.25。
2. 将框还原至原图，确认其至少 10% 面积与地面关注区相交。细长地面风险采用框面积相交，不沿用站立物体的底边中心锚点。
3. 对同一完整帧进行一次上下文编码，而不是将候选框紧裁剪后拉伸。采用作者配置：RGB、双线性缩放短边至 256、中心裁剪 256×256、除以 255，mean=0、std=1。仅有路径相关检测器候选时执行，每帧最多一次。
4. 视觉向量与固定文本原型作余弦排序，仅第一名为 puddle 才进入连续帧与声音规则。湿路、干路、阴影/地画、其他均不通过。

余弦相似度不是概率，画框置信度仍保留原始检测器值。拒绝的原始候选和复核理由也写入 `/status` 与回放报告，没有根据人工标签生成检测结果。

修复了一项实际 NCNN 输入缺陷：NumPy 裁剪常为不连续视图，原 native pixel 调用会读取错误布局。现统一转成连续内存；回归测试比较相同裁剪的视图与复制像素。

## 已完成的开发检查

- 原始 Torch 与 NCNN 在早期 19 张照片上的框/类别对照通过，保存为 D 盘 `evidence/v2-puddle-ncnn-check.json`。
- 一度尝试通用水分割确认，但扩展照片中漏检明显，已移除这条路线；没有堆叠成备用判断。
- 旧紧裁剪适配器对全部 51 张开发照片的实际推理：19 张可见积水中 17 张给出候选，29 张非积水中 0 张给出候选，另 3 张人工判为含糊而不计正负。历史报告 `D:\SmartCaneRuntime\evidence\v2-puddle-exact-adapter-dev.json` 不覆盖。
- 51 张已参与工程选择，部分照片同地点或同系列；这些是开发诊断，不能把 17/19 或 0/29 当独立准确率。
- Pi 先完成 5 场景、30 秒短回放，含启停恢复 38.30 秒，五个任务均运行；已恢复 live/none。记录 `logs/v2-puddle-short.json`，run `9317155871`。启动阶段预览为 0 的采样不用于稳定帧率结论。

完整 51 场景 Pi 回放结果追加于 [验证记录](pi-v2-validation.md)。完整照片时间为 306 秒，包含模型/英语播放器，测试后恢复真实摄像头。实验模型只经显式 `--puddle` 开启；默认真实摄像头配置没有增加此实验任务。

## 可复现命令

Windows（准备过的权重无需重新下载或导出）：

```powershell
D:\SmartCaneRuntime\.venv\Scripts\python.exe -X utf8 tools/evaluate_puddle_photos.py --catalog config/puddle-simulation-scenes.json --assets D:/SmartCaneRuntime/testdata/combined-simulation --model D:/SmartCaneRuntime/models/v2/puddle-research --out D:/SmartCaneRuntime/evidence/v2-puddle-exact-adapter-dev.json
```

Pi（确认回放视频及时间表已在 testdata/simulation，唯一服务未被其他测试占用）：

```sh
.venv/bin/python tools/check_v2_scene_replay.py --video testdata/simulation/puddle-scenes.avi --puddle --out logs/v2-puddle-scenes.json
```

这里的回放以公开真实照片为输入。没有模拟水的物理深度、相机运动或太阳曝光变化；下行、突然落差、真实高低不平仍是另行未完成的能力。

## 全帧上下文改进（2026-10-27）

追加照片暴露旧紧裁剪复核会将雨纹/反光水潭判为 wet_pavement/other。现改为上述固定的全帧预处理，检测器、文字原型、排序与阈值均不变，也没有增加第二个上下文模型或推理分支。

预处理来源是 `timm/MobileCLIP2-S0-OpenCLIP` revision `095906d28bf54d7584dc411e8ffe448f34289e05` 的 `open_clip_config.json`，实际 SHA-256 `43e7162c531e845155dbf47e8fef8fe5718b0eab3d6beefb8d615a2c55b4c1e5`。副本和校验在运行模型目录及 manifest。使用固定 Pillow 11.1.0；Pi 已有该版本，无需增加训练环境。适配器拒绝预处理不匹配的旧 manifest。

精确运行适配器的电脑开发诊断：旧 51 张为 16/19、误报 0/29；追加旧 11 张为 8/8、另 3 张含糊。相比紧裁剪的 17/19 + 4/8，明确积水总候选从 21/27 增为 24/27。这 62 张均属于开发数据，不是独立准确率。证据 `v2-puddle-global-exact-dev.json`、`v2-puddle-global-expanded-dev.json`。

新清单 `config/puddle-global-context-scenes.json` 共 89 张，明确标记 62 张 development 与 27 张 held_out_after_global_recipe。新增 27 张在冻结此预处理后才收集、看图并记录标签，没有用于选择上述改动。新组含 13 张积水照片、10 张非积水、4 张含糊；13 张正例只有 11 个元数据场景组，同组不重复计为独立试次。夜间照片单独标记，训练集重合未知。完整 Pi 时序/语音结果见验证记录，不把单张候选当作提醒通过。

## 湿路兼容规则被新素材否决（2026-10-27）

旧图显示整图 wet_pavement 排名会否决真实水潭，因此试验过：top1=puddle 仍接受；top1=wet_pavement 且积水检测器 confidence≥0.5 也接受；dry/shadow/other 仍拒绝。没有训练、改变文字原型、加权不同模型的分数或增加编码次数。

旧 89 张全部重新定义为该规则的开发素材，清单 `config/puddle-compatible-dev-scenes.json` 保留原 split 仅供历史报告复现。精确适配器实际输出为 36/40 正例候选、1/39 负例候选、10 张含糊。它不是独立验收，候选也不是语音。

随后另收集 34 张新照片（作者/标题与旧 89 张不同），看图后冻结为 14 正例、13 负例、7 含糊。冰雪、薄水膜、画作、场外积水与路径关系不明确的图保留但不计正负；2 张负例为夜间，白天负例 11 张。训练素材重合仍未知。新清单 `config/puddle-compatible-validation-scenes.json`，每张来源/许可/SHA 都保留。另加 8 张预先固定的旧回归图，共 42 张实际 Pi 回放 210 秒，含恢复 219.43 秒，run `b107593bce`。

新组只产生 **8/14 积水提醒，1/13 负例误报**，仍未通过。逐事件证据表明这 8 张正例全部本来就是 puddle top1；新增的 wet 条件仅在 `compatible-17.jpg` 湿路上制造误报，没有带来额外新正例。旧回归组也出现已预期的 `holdout-22.jpg` 误报。

因此已撤回兼容规则，当前源码/manifest 再次要求 puddle top1，且显式校验 `verifier.decision`，防止运行行为与准备的 metadata 不一致。保留失败报告及当时的规则/哈希，不让效果较好的开发数掩盖新图失败。专用积水仍默认关闭，不能把这次试验写成泛化提升。

证据：`D:\SmartCaneRuntime\evidence\v2-puddle-compatible-exact-dev.json`、`v2-puddle-compatible.json`、`v2-puddle-compatible-summary.json`、`camera-b107593bce.jsonl`。当前仍有两类缺口：无原始候选的漏检，以及上下文复核否决的漏检；下一步应分别研究，不再通过单纯放宽 wet 标签解决。
