# 坑洞候选替换研究：YOLOX-s

日期：2026-10-27。目标仍为完整 V2，本文不代表其他地面类别完成。

## 已冻结的来源

比较了两个真实可下载的预训练候选，没有训练、微调或扫描置信度。

| 候选 | revision | 实际文件 / 字节 | SHA-256 |
| --- | --- | --- | --- |
| [keremberke YOLOv8-small](https://huggingface.co/keremberke/yolov8s-pothole-segmentation) | `d6d5df4ac1a9e40b0180635b03198ddec88c4875` | `best.pt` / 23,830,146 | `0e508c055ce9b1df031207d6fb0b952073a73812d605d8fd60035dc69ac13630` |
| [AMD-PAVS-AI YOLOX-s](https://huggingface.co/AMD-PAVS-AI/pothole-detection) | `cd5dd7a24988ac3cb8e58825a9ad477323787ed9` | `pothole_yolox_s.onnx` / 35,774,753 | `9d55b8cc134f034b1885bd97aded14dcb17d887b8d5b7fbb3940e820d24c7660` |

YOLOX 发布者模型卡标 Apache-2.0、单类 pothole，声明在 jaygala24 数据集上训练。发布者自身 fork 的 `onnx_utils.py` 尚未找到，不能称其预处理已按该 fork 核实。适配按 [YOLOX 原作者代码](https://github.com/Megvii-BaseDetection/YOLOX/tree/6ddff4824372906469a7fae2dc3206c7aa4bbaee)；原作者源码和发布者 README 已保存在 D 盘研究目录，只作为参考，不执行下载源码。

YOLOv8-small 使用 `weights_only=True` 加载；旧版 Detect.forward 只允许精确的受限属性引用，不使用不受限 pickle。该候选效果更差，没有导出或部署其权重。

## 原图开发诊断

使用先前冻结的 60 张道路照片，保留原始标签；这些照片现在参与模型选择，不能再声称是新模型的独立留出验证。与既有原图诊断保持相同 fit640×480、输入640、conf=0.4、NMS=0.45。输出只是原始框，不是最终提醒。

| 模型 | 坑洞正例有候选 | 坑洞负例有误候选 |
| --- | --- | --- |
| 旧 keremberke nano，已有原图对照 | 5/10 | 8/36 |
| 新 keremberke small | 2/10 | 9/36 |
| 新 YOLOX-s ONNX | 9/10 | 7/36 |

YOLOX 提高了这组原图的候选覆盖，但误候选仍很多；不能直接宣布坑洞能力通过，或取消地面/路径/时序条件。报告：`D:\SmartCaneRuntime\evidence\v2-pothole-candidates.json`。

## 转换与 Pi 速度

- ONNX 输入 `[1,3,640,640]`，输出 `[1,8400,6]`。预处理为 BGR 0..255 浮点、左上放置、114 填充；不翻 RGB、不除255。解码使用三尺度 stride8/16/32，中心加网格，宽高 exp，置信度为 objectness×class，随后 NMS。
- Pi 原 ONNX 每帧 1795–1957 ms，超过1.5秒结果有效期；没有为此延长时效或直接接入默认任务。
- PNNX 对原 Focus 的 step2 Slice 不支持；首次直接转换日志有明确警告，该文件未当可用转换部署。新 `prepare_yolox_research.py` 从冻结原图移出精确 Focus 像素重排，在原 ONNX 前缀上用非平凡随机像素逐元素核对完全一致，再转换未改权重的卷积后缀。不是近似算法、降低分辨率或训练。
- 转换输入是 `[1,12,320,320]`，逻辑图像仍640。NCNN param **19,318字节**、SHA `cafe805b0dc10e94225e62e55445d7726a97ff7d6ca27294769e3ea293beea0c`；bin **35,704,852字节**、SHA `ddaf698e36293afcd047c5b6f504e73c79f252e66a5425db87934e7a3ded5a43`。
- 同60张原图与 ONNX 对照，检测数/类别/IoU匹配 **60/60**，最大置信度差 **0.00000173**、最大坐标差 **1.50px**（含边界裁剪）。只证明转换一致性，不证明准确率。报告 `v2-yolox-ncnn-reference.json`。
- 5张相同照片、原实时服务并行时，旧 nano NCNN约188–366ms；新 YOLOX NCNN约567–719ms。正式组合频率和预览表现要看实际回放，不把单次测速外推成长期稳定性。报告 `v2-yolox-pi-benchmark.json`、`v2-yolox-ncnn-pi-benchmark.json`。

## 显式实验入口

默认配置仍使用旧 NCNN 坑洞模型。实验配置才设置 `pothole.backend="yolox_ncnn_research"` 与 `directory="models/v2/pothole-yolox-research"`。ONNX 参考后端另名 `yolox_onnx_research`，不自动回退。未知后端、坏校验和、错误输出形状均拒绝，不猜类别。

准备转换需在已有导出环境执行，安装本仓库或设置 `PYTHONPATH=pi`，不要在 Pi 安装训练栈：

```sh
python tools/prepare_yolox_research.py --directory MODEL_DIRECTORY --pnnx-binary PNNX_EXECUTABLE
```

原图诊断可复现，输出始终标开发候选，不计声音/室外完成：

```sh
python tools/evaluate_yolox_photos.py --catalog config/road-yolox-development-scenes.json --assets ORIGINAL_ROAD_PHOTOS --model MODEL_DIRECTORY --backend ncnn --out NEW_REPORT.json
```

21图工程回放包含原10个坑洞正例、新模型全部7个原始误检和4个非道路控制；这是预测后选取的压力诊断，**不是新留出集，也不是全60图得分**。冻结原标签保持不变，清单在 `config/road-yolox-development-scenes.json`。FFV1 视频105秒，源图哈希/视频哈希留档。构建与 Pi 运行：

```sh
python tools/build_scene_replay.py --catalog config/road-yolox-development-scenes.json --assets ORIGINAL_ROAD_PHOTOS --output ROAD_YOLOX.avi --seconds 5
.venv/bin/python tools/check_v2_scene_replay.py --video testdata/simulation/road-yolox-development.avi --pothole-yolox --out logs/v2-yolox-development.json
```

检查器临时覆盖运行配置，结束后恢复真实摄像头和原始模型。仍使用唯一语音仲裁器、distance=none 和原地面条件。最终报告必须另核对 `completed/restored_live_camera/base_config_unchanged/runtime_override_removed`。

## 运行中断与测试

Pi 首次完整回归期间重启，旧进程消失，不把该次记通过。新 boot `c0f569fa-7153-4171-aa62-728f9755d70e`，恢复后短暂停实时服务降低并行负载，测试退出自动恢复。新 boot 有低电压历史 `0x50000`，当前低位为0，不能仅据此断言重启原因。

重启后刚传的视频和JSON损坏（JSON为零字节内容），原生校验发现两者SHA不符；模型manifest/NCNN文件与电脑一致。已从电脑重新传入并落盘，视频SHA `33d52d1641431bb49d015c240100562cec84fc515c521f0952bee02fa4184127`、时间表SHA `d55f2d6a78111c941e93303087ba28b55eeced72d052e21d12f7730835853d2c` 两边一致后才启动实际回放。首次JSON解析失败发生在临时服务覆盖前，没有产生有效场景结果。

Windows **218 passed / 24.26s**（`python -Xutf8 -m pytest -q`），Pi **218 passed / 29.32s**；均1个既有 FastAPI/Starlette弃用警告。Windows第一次未带UTF8参数，旧测试用系统GBK读中文配置失败；没有改文件或测试来隐藏该问题。新增测试覆盖像素/Focus顺序、stride网格解码、概率乘积/NMS、不修改输出张量、错误模型输出/权重、显式后端与共享回放链路。

完整 V2 仍未完成：本候选只针对坑洞，积水、可见破损、物理不平整、下行台阶、突降及室外/安装/摆动等要求保持。

## 实际 Pi 工程回放结果

run `86a80f7f74`，105秒素材，含启动恢复 **113.19秒**。四任务、真实仲裁器与播放器、distance=none。完成、恢复真实摄像头、基础配置不变、临时覆盖移除四项均为true，恢复 run `69725e09ef`，不是只启动成功。

- 坑洞最终提醒 **6/10**；所选11个压力负例中 **2/11误报**，为 road-57、road-58。6个命中均在场景起点及首次有效证据后3秒内。漏检为 road-05/07/10/12。未达到80%召回和≤10%误报要求；这些是已参与选择的工程场景，不能据它们称独立验收通过。
- 道路语义 **5/15**、误报0/4、2个含糊；可见破损 **1/9**、误报0/8、4个含糊。样本选择针对坑洞，不拿这些小分母替代之前完整60图结果，更不声称不平整已完成。
- 最低预览 **7.8 FPS**；物体3.96Hz、道路1.50Hz、坑洞1.49Hz、语义1.41Hz；任务错误0。坑洞最大单次推理467.1ms，语义765.2ms。本次短回放频率达标，不是全功能10分钟或户外稳定性。
- 11个风险事件：8个坑洞（6正例+2误报）、2个疑似积水、1个破损。积水在本清单未标真值，不据此评价水潭正确率。9次播放器启动，4次自然结束、5次主动中断、0次audio_error；事件数不等于完整声音次数。
- 全风险有效证据至确认p95 **1522.3ms**；确认至播放器进程p95 **18.0ms**。没有距离样本/抢占计时样本，不捏造这两项；不是声波或浏览器绘制时间。

默认仍保留旧配置；YOLOX保持显式研究选项。先前旧配置60图使用MJPG，本轮21图使用FFV1且更换模型，不能把旧3/10和新6/10写成严格配对提升率。原图同像素候选对照5/10→9/10是不同层面的证据。下一步需要定位语义上下文漏检、减少真实破损/阴影误判，并对确定配置另取未参与选择的照片后验证。

证据在 `D:\SmartCaneRuntime\evidence`：`v2-yolox-development.json`、`v2-yolox-development-summary.json`、`camera-86a80f7f74.jsonl`、`v2-yolox-reproducible-photos.json`、`v2-yolox-tests.txt`。没有提交、推送或新建PR。
