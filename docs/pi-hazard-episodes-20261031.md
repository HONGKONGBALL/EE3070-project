# Pi 危险片段优化：本地验证记录（2026-10-31）

当前是本地候选版本，分支 `feature/pi-hazard-episodes`。基线为 `de1d3a38db4e95ebbc2074f021b2c5d6275f008a`，保留在 `D:/SmartCaneRuntime/episode-upgrade-20261031/baseline`。没有 SSH、部署、Git 推送、训练或改权重。所有二进制证据留在 D 盘；`sources/` 参考资料未修改。

## 结论与查看方法

本地对比页：<http://127.0.0.1:8855/>。文件：`D:/SmartCaneRuntime/episode-upgrade-20261031/review/index.html`。可选择八段视频、同时播放修改前后画面、查看每条事件和声音队列结果。试听按钮使用本地英语 WAV，不连接 Pi。

已解决的重点是短暂漏帧后的证据累计、框变化造成的重复提醒、排队失败后无法重新判断，以及“创建提醒”和“真的开始播放”混为一谈。尚未解决的是底层模型的全部识别错误，以及真实行走的提前量。

最终验收数值见下方自动生成的数据摘要。未取得逐帧物理危险 ID、接近距离或正常步行积水正例，不能将这些回放称为真实拐杖安全验收。

## 根因与实现

原地面策略依靠 IoU 目标 ID、连续两次命中、每类八秒冷却。楼梯在语义框和大观察区域之间变化会换 ID；正常缺帧又会清空跟踪。事件创建时就标记已提醒，随后音频即使排队过期也没有可靠重试。新策略使用独立于框 ID 的三个前方图像区域危险片段：`wet_surface`、`damaged_pavement`、`stairs`。

- 积水、破损、楼梯默认 3.2 秒窗内 2/3 次有效独立观察、至少相隔 0.3 秒。重复渲染同一模型帧不增加证据；缺帧、低质量、过时结果不计作负例；最新结果仍须在 1.5 秒内。真实有效的负观测与缺失分开。
- **坑洞保留 2/2 个有效观测**，不是 2/3。较宽松的中间负例容忍会在猫喝水近景增加坑洞误报，因此最终保留更严格保护；真正缺失的捕获仍不会清空证据。不是所有类别使用同一套宽松规则。
- 同一前方片段不因框大小变化重新播报。楼梯重新进入需要至少四次可靠负观测跨三秒；湿滑需要六次跨五秒；破损需要三次跨 1.5 秒。楼梯负观测还须有可用地面／室内地板背景。缺失十秒（湿滑）或六秒（其他）才做不确定重置；此重置明确记录“physical exit unknown”，不是证明已离开。
- 裂纹和坑洞共用破损提醒族，但原始类别、模型、框和拒绝理由全部保留。至少两次强坑洞观察（置信度 ≥0.75、图像框面积 ≥1%）才能把普通破损升级为坑洞；这些阈值不代表物理深度或距离。楼梯／强坑洞优先于普通湿滑或破损。同一片段的升级会使旧请求失效。
- 提示缩短为 `Possible wet pavement ahead.`、`Possible damaged pavement ahead.`、`Possible pothole ahead.`、`Possible stairs ahead.`。`warning.phrases` 可配置文字和 WAV ID；改变实际语音必须同时生成／提供对应 WAV，单独改事件文字不会改变录音内容。音频清单和校验值在 `config/hazard-episode-artifacts.json`。

这是前方观察区域的时间分组，不能证明两次检测是同一个物理物体。多个彼此分离但持续出现在同一区域的水坑可能合并为一段湿滑提醒；长期离开视野后的同一楼梯也可能重新提醒。这是明确保留的取舍。

## 声音、恢复与时序

片段分开记录候选、确认、提交、排队、开始、完成、取消、失败、打断。队列最多两项，普通提醒最小间隔默认 1.5 秒，可用 `audio.ordinary_interval_s` 配置；高优先级危险按现有仲裁器抢占。排队时检查身份、最新证据与 TTL，过期／无关请求丢弃。未成功送达的提醒必须等待新有效画面和重试间隔（默认 0.8 秒），不能直接重新播放旧事件。

实际 PipeWire 路径在日志里记录 `pw-play` 进程启动与结束，关联稳定的事件／播放 ID。进程启动仍不是实测扬声器出声时刻。声音关闭会明确显示 `disabled`，消费一次预览片段而不会不断重试，不能据此说用户听到了提示。图像失效会取消无效排队及当前视觉声音；独立的通用距离提示不被视觉质量切断。

本次视频使用真实仲裁器配合 WAV 时长模拟器，不打开声音设备。`audio_simulated_start/done` 只说明模拟队列行为；试听是浏览器回放。移动道路视频中，积水首个请求因新画面不再支持而被丢弃；随后约 9.4 秒才有新证据并模拟播出。**这不能当作已经验证“足够早”**，尤其该素材是行车视频，不能换算人的接近距离。

捕获、候选输出、场景复核、确认、提交、开始分别保留时间戳；未做场景编码的模型以最终检测输出作为候选时刻。楼梯由全图复核恢复的候选，以复核结束作为首次可知候选时刻。首候选延迟使用被当前片段复核接受的首次候选；更早的拒绝候选留在诊断中，不伪装成已确认危险。

## 回归、取舍与剩余问题

1. 重用 125 张冻结照片（119 实拍、6 AI 干扰图），各图四秒独立曝光；这只能验证每图是否产生提醒，不验证持续移动的片段去重。原始模型存在性、原类别提醒和三种播报族分别统计，合并类别不算识别精度增加。
2. 三段正常速度第一人称步行视频保留步态、转头和曝光变化；另五段是既有实拍、固定镜头／加速／行车回归，来源性质分别注明。没有用真值重置视频内的策略。视频画面是实际运行 JPEG 按源时钟重采样，模型未处理每一原始帧。
3. 林间楼梯提醒从五次变为一次，柏林楼梯从两次变为一次。天桥仍有两次：第二次发生在超过缺失时限后，并非八秒定时复读；其未标注区间不能擅自算作新楼梯真阳性。林间落叶仍误报积水。猫喝水案例最初出现新增坑洞误报和湿滑重复，最终用更严格坑洞观察与较长湿滑退出窗消除该回归；中间报告也保留在 D 盘。
4. 照片裂纹提醒仍只有 2/12。权重没有变化，不能声称本次把裂纹模型调准了。原本的积水、坑洞漏报和误报名单继续提供。部分素材可能与原模型训练数据重合，不能当成独立测试集。
5. 0.5/1/1.5 倍媒体速率只是相同 180 帧行车画面的时间压力测试，不是 0.5/1/1.5 m/s，不推断米数。Windows 视频回放的实际捕获频率略低于媒体名义频率；回看时间和实际运行时钟分别保留。
6. 三客户端 HTTP、模拟断流／黑帧／恢复、持续楼梯、离开重进、多危险和 balanced 人／椅子／包距离门控均做本地测试。模拟断流不是拔 USB、掉电、真实 Wi-Fi 或 Pi 恢复测试。

## 主要文件与函数

| 文件 | 修改 |
|---|---|
| `pi/cane/vision/episodes.py` | `observe/step/valid/delivery/suspend`，有界时间证据、三族片段、升级、退出、重试与统计 |
| `pi/cane/vision/v2_policy.py` | `observe_ground/step/request_valid/clear_visual` 接入片段；普通物体／距离关联逻辑保留 |
| `pi/cane/vision/speech.py` | `Speech`、`PipeWireAudio.play` 关联队列与播放 ID、回调送达、时序；原离线声音设备路径保留 |
| `pi/cane/arbiter.py` | 取消队列时记录取消事件，不改变 Pico 震动请求与距离读取 |
| `pi/cane/vision/v2.py` | 片段、场景和质量恢复、过时推理标记 limited、不额外推理的状态统计、声音回调 |
| `pi/cane/vision/{ground,puddle,diagnostics}.py` | 候选／复核时间和拒绝原因，原权重、验证器、室内与路径门槛不变 |
| `pi/cane/vision/demo.py` | 原折叠诊断中显示片段／送达状态；沿用单个预览流和原 700ms 状态刷新 |
| `config/camera.v2.toml` | 分类别证据、片段退出、重试、短语及音频间隔；不改模型采样和置信阈值 |
| `tools/check_real_videos.py`、`timed_review_audio.py` | 真实视频＋有时长的模拟音频队列、完整日志 |
| `tools/check_episode_media_rates.py`、`build_episode_comparison.py` | 媒体速率压力和本地对比页面 |
| `tests/test_hazard_episodes.py`、`test_episode_audio.py` | 缺帧、单次弱误报、框变化、重进、升级、队列过期／重试等契约 |

没有增加模型调用、图像帧积压、地图、麦克风、训练或额外算法备选分支。仅三族状态、每标签最多八条历史、队列两项、最近送达十六项；原 latest-frame 相机和独立模型线程沿用。温度、功耗与实际物理帧丢失未测，`unsampled_capture_frames` 是主动跳过旧捕获帧，不等于 USB 丢帧。

## 本地复现

在本地仓库根目录执行（PowerShell）：

```powershell
$EpisodePython = 'D:\SmartCaneRuntime\.venv\Scripts\python.exe'
$EpisodeRoot = 'D:\SmartCaneRuntime\episode-upgrade-20261031'
& $EpisodePython -X utf8 -m pytest -q
& $EpisodePython -X utf8 tools/check_ground_local.py --video D:\SmartCaneRuntime\stress-20261031\mixed-replay.avi --timeline D:\SmartCaneRuntime\stress-20261031\mixed-replay.json --models-root D:\SmartCaneRuntime\models --seconds 600 --out "$EpisodeRoot\photos-final\report.json"
& $EpisodePython -X utf8 tools/check_real_videos.py --catalog "$EpisodeRoot\final-catalog.json" --models-root D:\SmartCaneRuntime\models --out "$EpisodeRoot\repeat-videos" --timed-audio-dir "$EpisodeRoot\audio\en"
& $EpisodePython -X utf8 tools/check_episode_media_rates.py --catalog "$EpisodeRoot\final-catalog.json" --models-root D:\SmartCaneRuntime\models --out "$EpisodeRoot\media-rates-final" --audio-dir "$EpisodeRoot\audio\en"
& $EpisodePython -X utf8 tools/build_episode_comparison.py --root $EpisodeRoot
& $EpisodePython -m http.server 8855 --bind 127.0.0.1 --directory "$EpisodeRoot\review"
```

最后一行只在端口服务未运行时执行。当前预览服务限本机。冻结目录保存原媒体和来源／许可，未把原视频上传到 Git。英语 WAV 用 `tools/gen_camera_prompts.ps1 -OutputDirectory ...` 生成，现有模型、环境和权重不必重装。

## 后续 Pi 部署命令（本次未执行）

在确认本地效果后才使用。历史项目位置为 `/home/gp7_3070/EE3070-project`；地址以重新联网后的实际地址替换。首先读取服务使用的配置路径，不猜测旧网络地址或覆盖 Pi 线程／采样设置。

```powershell
$EpisodePi = 'gp7_3070@3070gp7.local'
scp D:\SmartCaneRuntime\episode-upgrade-20261031\smart-cane-hazard-episodes-local.zip "${EpisodePi}:~/smart-cane-hazard-episodes-local.zip"
ssh $EpisodePi
```

Pi SSH 会话中的命令：

```bash
systemctl --user cat smart-cane-camera
cd ~/EE3070-project
# 以下默认现有服务用 config/camera.v2.toml；如上一行显示另一文件，替换这个变量。
EPISODE_CONFIG=config/camera.v2.toml
EPISODE_STAMP=$(date +%Y%m%d-%H%M%S)
systemctl --user stop smart-cane-camera
tar -czf "$HOME/smart-cane-before-episodes-$EPISODE_STAMP.tgz" pi/cane "$EPISODE_CONFIG" audio/en
mkdir -p "$HOME/smart-cane-episode-candidate"
python3 -m zipfile -e "$HOME/smart-cane-hazard-episodes-local.zip" "$HOME/smart-cane-episode-candidate"
cp -a "$HOME/smart-cane-episode-candidate/pi/cane/." pi/cane/
cp "$HOME/smart-cane-episode-candidate/audio/en/"*.wav audio/en/
export EPISODE_CONFIG
python3 - <<'PY'
import os, pathlib, tomllib
target=pathlib.Path(os.environ['EPISODE_CONFIG'])
text=target.read_text()
old=tomllib.loads(text)
if any(k in old['warning'] for k in ['confirmation','episodes','pothole_urgency','phrases']):
    raise SystemExit('Episode settings already exist; compare them instead of appending duplicate TOML tables')
candidate=pathlib.Path.home()/'smart-cane-episode-candidate/config/camera.v2.toml'
source=candidate.read_text()
addition=source[source.index('# Independent admitted observations'):]
updated=text+'\n'+addition
tomllib.loads(updated)  # validate before writing; camera/audio/Pico settings stay as deployed
target.write_text(updated)
PY
systemctl --user restart smart-cane-camera
systemctl --user status smart-cane-camera --no-pager
curl -f http://127.0.0.1:8000/status
```

若配置已含这些表，脚本会退出；不要继续重启，先对比当前配置。源程序默认普通声音间隔为 1.5 秒；如部署配置已有该选项则以配置为准。保留 `[audio] enabled` 的既有值，本次包不会自动开启无设备的声音输出。发生问题可停止服务、在原目录解压前述 `.tgz`、重启。模型和 Pico 固件不在更新包中。

## 实机仍须完成

- Pi 5 稳定电源、USB C270、真实耳机／音响，实际声音启停、抢占与故障恢复；确认不再是 Dummy Output。
- ground 与 balanced 各至少十分钟，测任务频率、捕获／预览、过时帧、CPU、RSS、温度／限频；本机 Ryzen 数据不能换算 Pi 性能。
- 摄像头固定到实际拐杖，正常向前推动，实测不同步速与接近距离；用地面标记或外部量测记录提醒发生位置和物理出声时刻。
- 正常步行积水正例、多段连续水坑、室内外上／下楼梯、光照／雨后反光。当前默认泛称 stairs，不承诺视觉判断上下行或真实高低差。
- Pico 测距与震动保持独立；后续验证时间戳、方向和物体关联，再决定是否使用互补信息。当前没有虚构距离，也没有启用现场未验证的关联。

computer-use 在浏览器视觉检查时因无法可靠确认当前 URL 而自动停止；未绕过或继续操控浏览器。页面结构、链接和媒体通过本地文件／HTTP 检查，视频帧已直接解码查看；未声称浏览器全交互验收完成。

<!-- GENERATED_RESULTS -->

## 最终数据摘要

照片结果为每图提醒事件存在性；不是框 mAP，也不是室外实测可靠度。未标注类别不计分。

| 原始类别 | 基线正例触发 | 最终正例触发 | 基线／最终负例误报 |
|---|---:|---:|---:|
| possible_puddle | 12/23 | 12/23 | 1/42 → 1/42 |
| pothole | 17/23 | 17/23 | 5/66 → 5/66 |
| damaged_pavement | 2/12 | 2/12 | 0/10 → 0/10 |
| stairs | 26/29 | 26/29 | 0/14 → 0/14 |

最终 125/125 完成，无模型错误；逐图的命中、漏报、误报名单全部与基线相同。仍有 6 张发生误报、29 张至少漏一类；不隐去失败。

原始模型输出存在性的计数（语义参考类别也保留，不等于播报）：{"before": {"possible_puddle": 16, "pothole": 28, "damaged_pavement": 13, "stairs": 31}, "after": {"possible_puddle": 16, "pothole": 28, "damaged_pavement": 13, "stairs": 31}}

播报族与原始标签分开统计，破损族真值为裂纹或坑洞任一明确为真；两者都明确为假才算负例，其余未知。完整族计数见 `photo-comparison.json`。鞋子／黑物／圆图案 10 张均无危险事件，商场／反光室内集 18 张无已标注类别误报。

| 视频 | 基线原始事件数 | 最终请求数 | 模拟开始数 | 说明 |
|---|---:|---:|---:|---|
| forest-stairs | 6 | 2 | 2 | 楼梯 5→1；仍有一次落叶积水误报 |
| rain-walk | 2 | 1 | 1 | 楼梯 2→1 |
| city-footbridge | 2 | 2 | 2 | 楼梯仍两次；长时间缺失后重开 |
| puddle-cat | 1 | 1 | 1 | 最终只有一次积水，无坑洞误报 |
| road-potholes | 0 | 1 | 1 | 完整回放，无模型错误 |
| stairs-down | 4 | 1 | 1 | 完整回放，无模型错误 |
| metro-timewarp | 2 | 2 | 2 | 完整回放，无模型错误 |
| dashcam-potholes | 2 | 4 | 3 | 积水第一请求被取消，新证据重试后才模拟播出 |

八视频采样：本帧捕获→事件 P50 328.0ms / P95 437.4ms；首次被接受候选→模拟开始 P50 1110.0ms / P95 5909.4ms；提交→模拟开始 P95 0.0ms。不是 Pi 或扬声器测量。
视频仲裁器丢弃／取消记录：`{"dropped_invalid": 1}`。请求数与开始数不同，重试不算重复成功播放。

| 模式 | 基线／最终等效忙核 | 基线／最终状态响应 P95 | 基线／最终 JPEG 响应 P95 | 最终 RSS 峰值 |
|---|---:|---:|---:|---:|
| ground | 1.82 → 1.83 | 20.35 → 18.58ms | 19.78 → 17.39ms | 968.61MB |
| balanced | 2.58 → 2.12 | 20.03 → 18.45ms | 18.4 → 17.42ms | 992.89MB |

CPU 是进程 CPU 秒／墙钟秒，1.0 表示一个忙核；单次短测的差异不归因于优化带来的确定加速。机器为 Ryzen 7 8845H，数据不能替代 Pi。模拟断流期间的 503 是预期结果；无 HTTP 异常，成功响应格式均正确。balanced 在无有效距离时没有绕过门控播报物体。
- ground: 86.97 秒、2008 次请求；捕获中位 12.6fps，预览 10.4fps；任务 Hz `{"road": 1.48, "pothole": 1.47, "semantic": 1.47, "puddle": 1.47}`；15 秒后 RSS 变化 6.56MB。
- balanced: 44.81 秒、1024 次请求；捕获中位 12.6fps，预览 10.5fps；任务 Hz `{"objects": 5.66, "road": 1.48, "pothole": 1.47, "semantic": 1.47, "puddle": 1.48}`；15 秒后 RSS 变化 33.22MB。

保存的八段基线视频本帧→事件 P50 359.0ms / P95 931.4ms；基线音频关闭，没有可比较的真实出声时间。基线与新视频不是受控重复试验，少量时延差异不作为确定加速结论。
提交→模拟开始出现 0ms 表示同一时钟刻度内同步派发，不是零物理延迟；最早候选可能经过多次等待与新证据重试才被播报。

| 媒体速率 | 完成 | 请求／模拟开始 | 模型错误 |
|---|---|---:|---|
| 0.5× | 是 | 3 / 2 | 无 |
| 1× | 是 | 3 / 2 | 无 |
| 1.5× | 是 | 4 / 3 | 无 |

最终完整自动测试：**279 passed**（73.15 秒）；一条既有 Starlette/httpx 弃用提示，无测试失败。`git diff --check` 和源码编译检查通过。28 项既有模型文件校验全部匹配。

时间顺序与可审计性：先保留基线，再试验片段策略，发现猫近景新增坑洞误报和水面重复后收紧，单独重测该段与移动道路。最终配置又完整重跑 125 张照片及 ground/balanced 压力测试、三种媒体速率和自动测试。早期报告、视频及各自源码哈希未删除；`preliminary-*`、`walking/`、`photos/` 是中间证据，`photos-final/`、`media-rates-final/`、`performance/after-*` 是最终回归。八视频各自原始记录保留运行源码哈希，后续仅声音参数配置化／诊断时间字段的改动不改变其模型或确认行为。

A–G 本地范围已核对：时间证据、片段／重进、三族原标签、送达重试、时间／负载、既有照片／视频／故障／距离门控、基线／权重与回退均有上述证据。Pi 对比、真实步速／距离、真实声音和完整浏览器交互验收仍按前述列表保留，遵守本次只在本地展示的限制。
