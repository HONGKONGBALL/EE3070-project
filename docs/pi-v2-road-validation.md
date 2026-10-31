# 道路、坑洞与可见破损验证

日期：2026-10-27。以下是实际未通过结果，不是 V2 完成报告。

## 冻结素材与计分含义

自行收集 74 张公开有来源/许可的候选图。人工看图后、运行模型前，排除 14 张图表、机械疲劳图、图纸和纹理/PBR 素材，保留 60 张真实场景。各图作者/标题与此前工程素材不同；模型训练重合未知，元数据分组不等于已测量的独立地理场地。

清单 `config/road-validation-scenes.json` 包含原始文件 SHA、来源页、署名、许可、冻结标签与不确定原因。原图在 `D:\SmartCaneRuntime\testdata\road-validation`。没有训练、微调或用预设答案画框/触发声音。

- 坑洞：10 张明确有可见凹坑边缘的正例，36 张负例，14 张因水、遮挡或宽裂隙等形态不明确而不强行分类。没有估坑深。
- 可见破损：原冻结清单为 22 张裂缝/表面碎裂正例，29 张负例，9 张不确定；规则并不等于物理高低不平。负例包括铺砖接缝、封缝、路面标线和室内地板。
- 道路语义：43 张可见 road/sidewalk/path，15 张非道路/有效地面缺失，2 张不确定。可以和破损共存，不能解释成“地面完好”“可以安全前进”。

## Pi 完整回放

run `1997f3ef5f`，四任务、distance=none，每图 5 秒，视频 300 秒，含启动恢复 **308.09 秒**。模型正常工作，没有任务错误。

| 分类 | 正例命中/提醒 | 负例分类误报 | 结果 |
| --- | --- | --- | --- |
| 坑洞 | 3/10 | 3/36 | 召回未通过 |
| 可见破损，原冻结标签 | 5/22 | 2/29 | 召回未通过 |
| 可见破损，显式勘误后 | 6/23 | 1/28 | 仍未通过；含预测后标签复核 |
| road/sidewalk/path 语义 | 21/43 | 1/15 | 召回未通过；没有正常路面声音 |

所有命中都在首次有效证据后 3 秒内，也在渲染线程首次观察到该场景之后 3 秒内。后者是解码帧的软件时间，既不是相机曝光也不是声波。没有命中的正例仍算漏检，不能靠只统计已播事件的低时延抵消。

### 标注勘误

road-33.jpg 的正例列表漏录，错误地将有清晰纵向裂缝的前景路面记为破损负例。预测后复核全分辨率图确认遗漏。原素材清单、原回放报告和原摘要全部保留；`config/road-validation-label-review.json` 单列 before/after、原因、复核时间、原清单/报告哈希。修正版摘要明确 `post_prediction_label_review=true`，不得伪装为完全未经复核的冻结标签结果。这一修正并未使任何类别通过。

### 性能和声音

- 预览最低 7.9 FPS；物体最低 3.93 Hz，道路/坑洞/语义分别最低 1.49/1.48/1.47 Hz。
- 24 个风险事件：坑洞 13、可见破损 7、疑似积水 3、普通楼梯 1。后两类在此清单中没有充分真值，不强称正确或纳入这三类准确率。
- 23 次播放器启动，16 次自然结束，7 次主动中断，0 个 audio_error。事件数不等于完整播音数。
- 全部风险首次有效证据至确认 p95 1478.9 ms；确认至播放器进程 p95 22.2 ms；1 次软件抢占停止 9.2 ms。无距离样本，不提供距离决策指标。
- 基础配置前后相同、临时覆盖已移除并恢复真实摄像头。不是户外、最终安装或全功能 10 分钟验证。

## 失败定位

原冻结正例中，原始检测输出覆盖坑洞 4/10、破损 8/22，而最终提醒为 3/10、5/22。只有一部分失败来自地面上下文；大多数在检测器原始输出阶段就没有候选。不能通过取消地面条件解决整体低召回。

后续对相同原始照片检查原始 Torch 权重和 NCNN：两模型各 60 图的检测数和类别/IoU 全部匹配；道路模型最大置信度差 0.00130、坑洞模型约 0.00000113，最大坐标差 1 像素。该比较仅证明这些照片上的转换一致性，不证明模型准确率。

读取到两模型作者训练输入均为 640。固定 conf=0.4、NMS=0.45、不训练不扫阈值，原道路权重的裂缝候选在 416 为 8/22，640 为 7/22；负例候选从 2/29 增为 4/29。因此未增大运行尺寸。原始坑洞权重在 640 为 5/10 候选、负例 8/36；这些是原图单帧候选，不能和最终声音数混用。

## 回放像素修复与短对照

此前 builder 使用 MJPG，会对已经来自照片的像素再次有损编码。现默认 FFV1，并检查首帧解码与输入逐像素一致，单元测试另对完整随机彩色帧核对；不静默切换到有损编码。JSON 记录 codec、原图 SHA，回放报告记录视频/时间表 SHA。历史 MJPG 文件和报告保留，不覆盖成“已修复结果”。

另用 5 张固定回归图做 25 秒 FFV1 真实 Pi 检查，含恢复 32.26 秒，run `2250f3b49b`。road-05 原本在 MJPG 下缺失的原始坑洞候选重新出现，但仍无有效地面上下文，最终无提醒；road-67 木地板也有原始坑洞误检，正确因地面条件而静默。road-01 提醒坑洞；road-17 铺砖仍有坑洞/破损误报；road-33 的真实裂缝产生提醒，同时还有未评分的坑洞输出。

短对照不重新计算 60 张整体验收准确率，也没有通过降低门槛制造“正常”。恢复真实摄像头 run `f399f774cc`，页面约 8 FPS。现有所有地面能力仍按未通过完整验收处理。

## 证据与复现

电脑 `D:\SmartCaneRuntime\evidence`：

- `v2-road-validation.json`、`v2-road-validation-summary.json`：原冻结回放/计分。
- `v2-road-validation-reviewed-summary.json`：显式勘误后的计分。
- `camera-1997f3ef5f.jsonl`：实际事件和播放身份。
- `v2-road-reference-resolution.json`、`v2-road-ncnn-reference.json`：原权重/分辨率/同像素转换诊断。
- `v2-road-lossless-check.json`：FFV1 短对照。

以下是重建历史有损条件的命令，勿覆盖原始证据文件；新回放默认 FFV1：

```sh
python tools/build_scene_replay.py --catalog config/road-validation-scenes.json --assets PATH_TO_ORIGINALS --output PATH_TO_NEW_VIDEO.avi --seconds 5 --codec MJPG
```

计分工具必须配对应原清单和报告；显式勘误通过哈希校验，不修改原报告：

```sh
.venv/bin/python tools/summarize_road_replay.py --report logs/v2-road-validation.json --catalog config/road-validation-scenes.json --out logs/v2-road-validation-summary.json
.venv/bin/python tools/summarize_road_replay.py --report logs/v2-road-validation.json --catalog config/road-validation-scenes.json --label-amendments config/road-validation-label-review.json --out logs/v2-road-validation-reviewed-summary.json
```

后续若据这些结果调整模型/门槛，60 张都应视作开发数据，另取未参与选择的场景验证。物理不平整、下行台阶和突降不得借用本报告的裂缝或原始框数声明完成。

后续已有实际YOLOX候选转换及21图FFV1工程回放，见 [YOLOX研究记录](pi-v2-yolox-research.md)。最终6/10坑洞提醒、误报2/11仍不通过；21图是预测后选择的压力样本，编码也不同，不能覆盖或直接冒充本页60图独立验证的更新分数。
