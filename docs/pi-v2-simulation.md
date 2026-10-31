# V2 公开路面场景模拟

用户不能提供现场场景，由实施 agent 准备素材与回放。最初材料是 Wikimedia Commons 的 19 张真实照片；目前已扩展到 51 张路面开发照片，另有独立目录的楼梯研究照片。不是生成图片，也没有训练模型。原始图在电脑 `D:\SmartCaneRuntime\testdata\outdoor-simulation` 和 `puddle-holdout`（历史目录名，不代表当前独立留出集）；可重建视频和时间表在 `D:\SmartCaneRuntime\simulation`。

以上为最早开发组。后续积水/楼梯及新60张道路评测分别留档，不能把重复出现在多个清单的照片累加成独立场景。最新道路材料及失败结果见 [道路验证](pi-v2-road-validation.md)。

## 输入与复现

逐文件来源页、作者、许可和 SHA-256 位于 [素材目录](../config/simulation-scenes.json)。`Muddy road in Akamas Peninsula, Cyprus 02` 的 API 作者字段曾缺失，已按[原来源页](https://commons.wikimedia.org/wiki/File:Muddy_road_in_Akamas_Peninsula,_Cyprus_02.jpg)补记 Podzemnik（CC BY-SA 4.0）。照片按原比例缩放并补黑边到 640×480，每张 6 秒、8 FPS，共 114 秒。标签与期望结果不会印进模型输入。对外再分发时保留素材署名、许可及缩放/视频转换说明。

`tools/build_scene_replay.py` 校验原始文件，生成 `outdoor-scenes.avi` 及同名 JSON 时间表。`tools/check_v2_scene_replay.py` 在 Pi 上运行全部现有模型，关闭距离输入，以真实模型输出驱动英语提示。按时间表区分照片、清除上一场景证据；最后恢复真实摄像头。运行命令见 [运行说明](pi-v2-run.md)。

人工看图标签在模型评测前记录：6 张可见积水、13 张未见积水；8 张可见铺装破损。部分是同地点/相似系列，不能当成 19 次独立户外试验。泥路凹槽、水与坑洞也可能同时存在，所以没有坑深真值的图不强行作“无坑洞”负例。

## 当前开发发现

- 单张图片原始 water 语义输出在 6 张可见积水中命中 5 张，但也在两张无水图片误报；这只是开发诊断。
- 大面积水会占据原先的路面区域，全局路面比例门槛会漏掉水潭。已改成检查水体边界周围的室外地面语义；室内 floor 不提供水潭上下文。
- 同一开发集调整后，4/6 可见积水具有有效边界上下文，13 张未见水照片无满足条件的 water 区域。这个数字不含路径区域、连续帧与语音规则，且参数已看过这组图，不能当独立准确率。
- 坑洞模型会把泥路凹槽或破损纹理判成 pothole；完整回放保留这些原始输出。语义过滤、确认与提示规则的效果以 Pi 报告为准。

## 不可混淆的验证层次

Pi 首轮已实际完成 19 个场景：积水提醒 3/6，未见积水照片上的积水提醒 0/13；破损提醒 4/8。共 8 次英语播放正常结束。包含恢复总用时 121.2 秒，已恢复真实摄像头；逐项结果见 [验证记录](pi-v2-validation.md)。这些是完整过滤后的提醒数，与上节的单张原始模型/上下文诊断分开。

1. 规则测试用合成检测夹具，只证明迟滞、去重和队列行为。
2. 当前照片回放由实际模型推理，可发现漏检/误报并验证完整声音链路。
3. 运动、曝光、相机摆动和距离随运动变化需要真实视频或相机；重复照片不能代替。
4. 物理高低不平、下行方向、突然落差尚没有通过的识别实现。这组照片既没有测量高度真值，也没有这些类别的充分正负例，不能据此宣称完成。
5. 真实 Pico 方向/安装关联及户外实机验收仍未完成。模拟距离与视觉回放分别标明来源，真实距离不会关联录制画面。

后续素材继续由实施 agent 搜集，按场地与片段隔离开发/验证集；不要求用户造水潭、靠近危险落差或补训练数据。

## 51 场景扩展回放

完整逐图来源、许可、人工标签和 SHA-256 位于 `config/puddle-simulation-scenes.json`。合并目录为 `D:\SmartCaneRuntime\testdata\combined-simulation`，回放文件为 `D:\SmartCaneRuntime\simulation\puddle-scenes.avi` 和同名 JSON。每张 6 秒，共 306 秒，未把期望标签印进图像。3 张含糊照片保留展示但不计入积水正负指标。

专用积水检测器配合固定上下文复核的实现、电脑检查和限制见 [积水研究记录](pi-v2-puddle-research.md)。当前数据已用于工程选择，所有结果按开发回放报告，不能声称完成独立户外验收。

## 89 场景全帧上下文回放

新清单 `config/puddle-global-context-scenes.json` 保留每张照片的来源、作者、许可、SHA-256、预先人工审阅标签、场景组和 evaluation_split。62 张属于工程开发数据；冻结全帧复核后新增 27 张，含 13 正例照片（11 组）、10 负例、4 含糊。液态水/冻结、池塘与路径关系不明确的图在看模型输出前标为含糊，不按输出修改标签。负例包含湿路、反光、阴影和深色铺装，不能只用晴天干路。

素材目录 `D:\SmartCaneRuntime\testdata\global-context-evaluation`；视频及时间表 `D:\SmartCaneRuntime\simulation\puddle-global-context.avi` / `.json`，每张 5 秒、总计 445 秒。构建器校验照片 SHA 并保留 split。标签只供报告使用，不影响推理。来源模型训练集重合未知，照片回放也不能代替移动相机和真实室外测试。

```sh
.venv/bin/python tools/check_v2_scene_replay.py --video testdata/simulation/puddle-global-context.avi --puddle --out logs/v2-puddle-global-context.json
```

预处理选择后才收集的新组只用于这一固定版本的检查。若根据它继续改动算法，该组随后也必须标为开发数据，不可反复当作独立留出集。

## 新的 34 图与 42 场景试验

新增素材目录 `D:\SmartCaneRuntime\testdata\puddle-compatible-validation`，清单 `config/puddle-compatible-validation-scenes.json`：14 明确积水、13 未见可辨水潭、7 不确定。负例含湿路和条纹/树影；其中两张夜间单独记录。标签在运行模型前冻结。候选 34/35 号未下载，不计入清单或验收，下载器保留当时 HTTP429/Retry-After，不以缺失文件凑数。

旧 89 张因参与新规则选择，在 `config/puddle-compatible-dev-scenes.json` 中全部是 development；历史全帧 v1 清单和报告原样保留。42 图混合回放额外含 8 张旧回归图，每张 5 秒，视频 `D:\SmartCaneRuntime\simulation\puddle-compatible.avi`、同名 JSON。总计 210 秒，开发/新素材分别评分。

实际新组 8/14 提醒、1/13 误报，不达标，试验规则已撤回。这份数据以后如用于继续设计规则，也须当作开发数据；不能重新称为未见素材。

## 道路三类评测与编码条件

`config/road-validation-scenes.json` 是预测前冻结的60张道路/草地/室内地板等真实照片，14张不相关图表/机械/纹理候选在推理前排除。原图 `D:\SmartCaneRuntime\testdata\road-validation`，历史视频 `D:\SmartCaneRuntime\simulation\road-validation.avi`，每图5秒、共300秒、原编码MJPG。原冻结清单不修改；road-33标注遗漏单列带哈希勘误，详见道路验证。

从当前 builder 起，默认编码改为 FFV1，并验证首帧像素一致；JSON含原图SHA和codec，报告含视频/时间表SHA。`--codec MJPG` 仅用于明确的有损输入条件。重建历史实验须显式指定旧codec并换输出文件名，不能覆盖历史输入后沿用旧报告。没有新增Pi训练或视频运行时依赖，使用已有OpenCV/FFmpeg支持；编码不可用会报错而非静默切换。

新无损短对照是 `road-lossless-check.avi/.json`，5图25秒；不把它写成60图完整复测或正式户外验收。
