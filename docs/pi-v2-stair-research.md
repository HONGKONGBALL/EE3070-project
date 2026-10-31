# 下行台阶研究与失败边界

日期：2026-10-26。**没有通过下行台阶验收，不在默认摄像头中启用方向模型。** 正常服务继续用四个已有任务；本节的实验不会把普通 stairs 改名成 down。

## 已找到并实际运行的方向权重

作者：[bossenti/Real-Time-Detection-of-Stairs](https://github.com/bossenti/Real-Time-Detection-of-Stairs)，revision `b383cfd1c78d1821347b4de76ed643b2f8951e48`。先前只看 README 没核实权重文件，后续 Git 树检查已确认实际文件，不再按“只有训练代码”处理：

- 配置：`Modeling/YOLOModels/YOLO_final_model_up_down.cfg`，416×416、3 类。
- 权重：同目录 `YOLO_final_model_up_down.weights`，23,539,796 字节，SHA-256 `1eebeae807ae9a285baded72037f733ddc9f2d37ead8ea1fe91f6072387d48b1`。
- 标签来自作者 `Modeling/TinyYOLOv4.ipynb` 保存的评估输出：0=None、1=up、2=down；不是根据文件名或框形状猜方向。
- 作者仓库没有明确许可证文件；只保留本地研究，不将其权重发布到 Git 或默认为交付模型。

新适配器 `vision/stairs.py` 使用 Pi 已有 OpenCV 4.10 DNN，不在 Pi 安装新训练库。Windows 当前开发环境 OpenCV 5 不含 Darknet 加载器，研究使用 D 盘独立 OpenCV 4.10 目录，不降级工作中的开发环境。

## 实际测试

1. 作者下行示例可实际输出 down；这张图不是独立测试。Pi 单线程 3 次推理为 781/352/319 ms，首轮含热身；不是完整组合的 p95。
2. 另取 `mateoguaman/iphone_stairs_ramps` validation 中 20 张按行号均匀抽取的照片，先人工看图，均未见下行。仅方向模型在其中 6 张误报 down，包含平路/坡道。这些图来自同一校园、分组边界未知，不当作 20 个独立场地。
3. 用现有 SegFormer 的高置信 stairs/step 像素确认方向框：框内至少 10% 为楼梯，帧时间差不超过 750 ms。该开发集的 6 次误报被排除，作者下行图仍保留；这只是开发结果。
4. Pi 五任务真实回放：作者下行图 + 20 张校园负例，视频 126 秒，含恢复共 133.72 秒，run `6c2e5132b3`。作者图产生并实际播完 `downstairs_ahead`；校园负例没有下行提醒，所有模型无记录错误，短时预览约 8 FPS。
5. 首轮发现同一楼梯随后又报泛化 stairs，以及场景切换主动停播被记成音频错误。已按空间重合抑制重复普通提示，并区分主动取消；6 秒视频短复核总用时 12.65 秒，run `1551d7ebe7`，只有一次下行事件，切换记录为 `audio_interrupted`，无 `audio_error`，恢复真实摄像头。

## 新素材扩测未通过

自行搜集 24 张有署名/许可的 Wikimedia Commons 楼梯照片，人工审阅后再推理：10 张下行正例、12 张上行/非下行负例、2 张方向证据模糊不计分。布达佩斯两张同地点图属于同组；包括旋转/俯视等困难视角，不能将正例数写成 10 个独立正常安装场景。

素材清单、SHA-256 和审阅结果在 `config/stair-research-scenes.json`。原图保存在电脑 D 盘 `SmartCaneRuntime/testdata/stairs-public`。阈值保持此前 0.5/0.55/10%，没有按这批结果调高分数或删图。

实际离线结果：**10 张正例只有 1 张进入完整空间/语义提醒候选，12 张负例也有 1 张错误进入候选。** 还没执行连续帧/播报，不把候选数写成语音成功率。原始方向、语义确认、区域过滤结果全部保存在 D 盘 `evidence/v2-stair-public-photos.json`。

因此不再为这套权重追加完整户外部署或重复长测。它未达到 PRD 的召回要求，默认关闭；测试通过的软件接口不等于模型能力通过。

## 冻结零样本模型追加核查

另外检查了 [MobileCLIP-S0 ONNX](https://huggingface.co/Xenova/mobileclip_s0)（revision `757d59c9c6870a76a4b0306f05f5061bca15c39f`）与 [MobileCLIP2-S0 ONNX](https://huggingface.co/RuteNL/MobileCLIP2-S0-OpenCLIP-ONNX)（revision `2a4361b87cd76f8493625cc31f36031ee9d52110`）。只有固定文字描述、冻结图像/文字编码器与余弦排名，没有训练。

先核对模型卡的猫/狗/鸟示例。本轮 S0 的 uint8 视觉文件在当前运行时把已看图确认的猫判成鸟；换成浮点视觉文件后正确，交叉更换文字模型结果仍一致。因此早期量化视觉评估文件 `v2-mobileclip-stair-research.json` **不能用于评价方向能力**，不能把加载/量化偏差算成模型不懂楼梯。浮点结果另存，不覆盖早期证据。

| 固定提示词、浮点视觉模型 | Commons 下行图 Top-1 命中 | Commons 非下行图 Top-1 误报 | 校园非下行图误报 |
| --- | --- | --- | --- |
| MobileCLIP-S0 | 6/10 | 2/12 | 1/20 |
| MobileCLIP2-S0 | 8/10 | 4/12 | 0/20 |

以上是整图类别排名，尚未做路径区域、连续帧或声音验收，不和上一节的确认候选数混用。余弦分数也不是经过校准的正确率。新版提高了这批下行图的命中，但误报仍超过 PRD 要求，所以没有部署到 Pi，也没有以“已存在通用模型”冒充通过。

模型、分离的研究依赖、固定文字描述和完整逐图结果留在 D 盘 `SmartCaneRuntime`：`evidence/v2-mobileclip-float-quant-sanity.json`、`v2-mobileclip-stair-research-float.json`、`v2-mobileclip2-sanity.json`、`v2-mobileclip2-stair-research.json`。原始来源许可文件和模型卡一并保留，未把转换仓库的许可字段当作整个项目的统一许可。

## 复现研究

权重的原文件、manifest 与完整哈希在 D 盘/Pi `models/v2/stairs-direction-research`；保留上述原来源与未明确许可状态，不随源码分发。Pi 可做短单图检查：

```sh
.venv/bin/python tools/check_stair_candidate.py
```

当前有研究视频时可显式临时运行，结束恢复正常摄像头，不永久启用方向模型：

```sh
.venv/bin/python tools/check_v2_scene_replay.py --stairs --video testdata/simulation/stairs-research.avi --out logs/v2-stairs-replay.json
```

源码中的临时距离/回放规则、地面时序确认、唯一语音仲裁都继续生效；不将方向模型输出解释成米数、台阶高度或突然落差。

## 同作者 SSD MobileNetV2 追加核查

实际取得同一 revision 的 `Modeling/SSDMobilenetV2_trained_models/SSD_300px_16k_steps_up&down/frozen_inference_graph.pb`，19,240,040 字节，SHA-256 `6a14bd16ddb7a1f8f9cedf7b275c5a4554a99abca069d69f610c66a87aa79b21`。作者 `Preprocessing/PreprocessingUtilities.py` 390–403 行证实 TF 标签为 background=0、up=1、down=2。

使用官方 OpenCV 4.10.0 的 `tf_text_graph_ssd.py` 和实际 `pipeline.config` 生成可读图，无 TensorFlow 安装，没有替换为不相干的 COCO pbtxt。原始 RGB 300×300 输入，保留图内 /127.5−1 预处理。

44 张诊断图（上述 Commons 24 + 校园负例 20）：阈值 0.5 时，下行原始候选仅 2/10，非下行 0/12、校园 0/20。再经固定楼梯语义/路径规则，正例为 0/10。作者图可检测不代表泛化通过，因此没有部署此候选到 Pi，也没有新增常驻模型。完整逐图证据 `D:\SmartCaneRuntime\evidence\v2-stair-ssd-research.json`，保留失败结果。
