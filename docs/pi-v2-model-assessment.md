# Pi V2 现成算法评估

核查日期：2026-10-26。前几节记录 PRD 制作时的模型调查，当时没有下载/执行新模型。后续实施与选型变更见末节及 [V2 验证记录](pi-v2-validation.md)，不要把历史候选列表当当前已启用能力。

## 1. 推荐的最小组合

保留 V1 的 YOLO11n COCO；优先新增一个 RDD2022 YOLOv8n 覆盖坑洞与三类裂缝，再验证一个共享语义分割模型用于地面/水体。用户已删去路缘/路面边缘需求，不增加专用模型；箱子/杆子仅为模型支持时的障碍示例。下行台阶、突降与真实高低不平仍有未解决的模型证据缺口，不能由模型名称补齐。

### 物体：既有 YOLO11n NCNN

V1 已在 Pi 验证。COCO 的实际类别包含 person/chair/bicycle/backpack 等，不含独立 box/pole、puddle、pothole 或 stairs；新增类别名字不会增加模型能力。[官方类别表](https://github.com/ultralytics/ultralytics/blob/main/ultralytics/cfg/datasets/coco.yaml)

### 坑洞与可见破损：RDD2022 YOLOv8n

- 来源：[dronefreak/rdd2022-yolov8n](https://huggingface.co/dronefreak/rdd2022-yolov8n)。
- 本次 API 核查 revision：`acf216a43d796f7910fc854f662c1e9cfa03fc01`；实际 `best.pt` 为 **6,251,050 字节**，不是仅通用 COCO 权重。[文件列表](https://huggingface.co/dronefreak/rdd2022-yolov8n/tree/acf216a43d796f7910fc854f662c1e9cfa03fc01)
- 作者类表：longitudinal_crack、transverse_crack、alligator_crack、pothole。一个模型可覆盖坑洞和这些可见破损，不能据此识别所有凸起、松动路砖或物理不平整。
- 模型卡标 AGPL-3.0；数据来源与许可另记录。作者评测属于道路损坏数据，不能当拐杖视角性能。
- 部署候选：兼容 Ultralytics 工具加载后导出 NCNN；当前模型转换与 Pi Python 环境均未测。先在 D 盘隔离导出，记录前后差异；不能照抄 V1 固定 80 类输出解码。

备选仅在上述失败时评估：[keremberke/yolov8n-pothole-segmentation](https://huggingface.co/keremberke/yolov8n-pothole-segmentation)，单类 pothole，旧依赖示例需兼容调查；不能替代全部破损需求。不同时装多个坑洞模型做无依据置信度融合。

### 地面与水体：SegFormer-B0 ADE20K

- [模型卡](https://huggingface.co/nvidia/segformer-b0-finetuned-ade-512-512)；本次 revision `489d5cd81a0b59fab9b7ea758d3548ebe99677da`，`model.safetensors` **15,036,944 字节**。
- [实际配置](https://huggingface.co/nvidia/segformer-b0-finetuned-ade-512-512/raw/489d5cd81a0b59fab9b7ea758d3548ebe99677da/config.json)包含 road/sidewalk/path、water、stairs/stairway/step、box、pole 等语义类；按该配置映射，不混用 ADE20K 的索引偏移。
- 可用一个模型探索多种语义；water 不是专用浅水潭检测，stairs 没有上下方向，pole/box 也须单独测当前视角。
- 优先评估固定形状 ONNX CPU 导出或经验证的 NCNN 转换，最终只保留实际选定路径；没有测得 Pi 耗时，不承诺实时率。
- [上游许可证](https://github.com/NVlabs/SegFormer/blob/master/LICENSE)有限制，模型卡标 other；保留原文并按使用方式核实，不写成 MIT。

## 2. 下行台阶/突降：有文件不等于有方向能力

补查了 [Yesh492/StairVision 训练仓库](https://github.com/Yesh492/stair-detection-training)和[Android 仓库](https://github.com/Yesh492/cs663-stair-detection-android)。

- Android revision `619edf426279035bff3fbba3fc81fe6719eae43c` 确有 `app/src/main/assets/stair_yolo_best_float32.tflite`，**12,280,703 字节**。
- 根目录另一个 `assets/stair_model.tflite` 是 **0 字节占位**，不可当模型。
- 训练仓库树中本次没有查到可直接复用的 `.pt/.onnx` 主体；README 的路径和训练命令不等于已发布该格式权重。
- 更关键的是，作者 [YoloDetector.kt](https://github.com/Yesh492/cs663-stair-detection-android/blob/619edf426279035bff3fbba3fc81fe6719eae43c/app/src/main/java/com/example/stairvision/YoloDetector.kt#L192) 的 `analyzeStairType` 用框的 y 位置、宽高比等启发式猜上行/下行；这不是已经证明可用的方向识别模型。

结论：可将其作为楼梯检测研究线索，许可、TFLite ARM/Python 兼容与实际输出均待测；**不得把它列为已解决“下行/突降”的算法**。不复制它的按框形状估米数或引入 Android/Gemini 流程。

[StairNet-DepthIn](https://github.com/MrChenWang/StairNet-DepthIn)明确需要 RGB+Depth 双分支；现有 C270 不提供深度，不能直接用来满足单相机约束。一般单目相对深度也不直接证明路缘高度、落差或米数。

## 3. 验证与选择门槛

每个候选依次通过：真实文件与标签 → 来源/许可记录 → 兼容加载 → 导出一致性 → 独立正负例 → Pi 单模型 → 组合配置 → 室外当前摄像头。直到最后一步前，不标室外完成。

对积水、裂缝和台阶特别检查阴影、反光、纹理和当前安装角；按场地/片段划分测试，不把邻帧当独立样本。无合适免训练路线时记录必要条件和失败证据，维持必做未完成，不把训练作为默认补救。

这份调研支持“可以开始哪些验证”，不支持“所有目标已可直接安装实现”的承诺。最终能力以 PRD 的逐项验收结果为准。

## 4. 实施阶段追加

首选 RDD nano 的实际测试结果不足，已停止作为当前运行模型。当前用独立坑洞 nano（640）与道路破损 small（416），两者同用 NCNN；语义分割继续复用一个 SegFormer-B0 ONNX。拆成两个检测权重是实际验证后的必要调整，不是同时堆入多套候选备用。

- [实际道路破损权重](https://huggingface.co/vinothvikas1987/pothole-detection-yolov8)：revision `b001687443175e43442f63bef4691c3546629def`。原始类为三类裂缝、Pothole、Other；不把 Other 解释成任意地面风险。
- 坑洞采用上文 keremberke 权重；实际 revision `132feb1b360d9b3b5b3eed9b96add0ad42d2a026`，原始 SHA-256 `375c8a7c2603a3fffe22c546d24aeab9ae9ffeb71550363354fc414473b16eba`。
- [语义 ONNX 导出来源](https://huggingface.co/Xenova/segformer-b0-finetuned-ade-512-512)：revision `d3e5499fa8701ff0453ca940a8dfeae39b2f1504`，源模型仍是 NVIDIA ADE20K；许可与原始类别限制不因 ONNX 而改变。
- [AMFNet](https://github.com/lab-sun/AMFNet)虽提供负障碍研究和权重入口，但实际 demo 使用 RGB+Depth 数据，不是现有单 RGB 摄像头的直接方案。
- [StairNet 作者论文](https://link.springer.com/article/10.1186/s12938-024-01216-0)的主类围绕 level-ground 与 incline-stair 转换，不能由名称直接推断具有下行/突降识别。
- 补查到的 omree uneven-B0 仓库本次没有实际模型文件；若其他页面只有训练代码或空占位，也不计为可部署权重。

以上尚未解决物理不平整、下行台阶与突然落差三个必做项；继续研究与验证，不能写已完成。

### 公开模拟后的追加核查

- [bossenti/Real-Time-Detection-of-Stairs](https://github.com/bossenti/Real-Time-Detection-of-Stairs)：初查 README/API 没核实到最终权重；后续 Git 树确认实际存在，已加载并在 Pi 研究回放。新公开照片测试只有 1/10 下行正例进入确认候选，未通过，默认关闭；标签、哈希、测试和许可缺口见 [下行研究记录](pi-v2-stair-research.md)。没有把通用预训练初始化权重当方向模型。
- [NoonGil-BarrierFree-AI](https://github.com/ezhye0n/NoonGil-BarrierFree-AI)有 step/pavement_damage 类及模型发布入口，但没有 down/drop 类，推荐运行环境包含 CUDA。它的 step 标签不能补齐方向或物理高差，不为替代同义标签增加常驻模型。
- [Offroad-Nav 作者项目](https://lariad.github.io/Offroad-Nav/)使用 Depth Anything V2 加 VINS-Mono 的稀疏 SLAM 测量做尺度恢复，再建高程图；这是完整机器人管线，不是现有未标定 C270 上可直接启用的下行/突降分类器。单独拿到深度权重也不能据此宣称高差功能完成。

19 张公开照片的实测与失败记录已保存，见 [场景模拟](pi-v2-simulation.md)。素材缺口可由实施 agent 自行补充，模型能力缺口仍需实际适用权重与验证，不能通过“模拟一个正确答案”解决。

2026-10-27 追加：已实际转换并部署专用 puddle 预训练检测器，配合冻结 MobileCLIP2 上下文复核，在 Pi 上完成 51 张照片回放；积水提醒 17/19 正例、0/29 负例，另 3 张含糊。没有训练；这些照片参与过工程选择，仍是开发诊断。来源缺少明确检测器许可，默认摄像头未启用此研究任务；固定版本、算法、反例与复现见 [积水研究记录](pi-v2-puddle-research.md)。

2026-10-27 后续：全帧复核的新 27 图仅 6/13 积水提醒；又用 34 张新图验证湿路兼容规则，只有 8/14、误报 1/13。该放宽没有带来额外新正例，已撤回，不能沿用旧开发结果声称泛化通过。实际型号/决策哈希与三轮独立边界见积水记录。

[SBP-YOLO](https://github.com/chuanqi1997/SBP-YOLO) revision `37d6edaa3df0ded55842d94b513c926ab6dd1660` 确实发布了训练后基线权重，实际类表 Pothole/SpeedBump。标准基线已安全加载并导出 NCNN，但初步凸起正例 0/2，未部署；减速带子类也不等于所有物理不平整。完整来源、文件哈希与失败记录见 [凸起研究](pi-v2-raised-bump-research.md)。

后续实际比较 YOLOv8-small 与发布者提供的 YOLOX-s ONNX。后者在60张开发原图有9/10坑洞候选，但7/36负例误候选；经源图Focus等价核对和NCNN转换，在Pi四任务21图回放最终6/10提醒、误报2/11，仍不达标。只保留显式研究选项，不替换默认或声明通过。来源revision、文件哈希、转换一致性、性能与重启恢复记录见 [YOLOX研究](pi-v2-yolox-research.md)。
