# K4 视觉提问（Pi 5 + Grok）

按一下键，问一句话，手杖用摄像头当前的画面回答。

## 怎么用

1. 短按第 4 个键（GP13，固件里叫 `mode`）。
2. 听到两声提示音后说问题。提示音之前说的话不会被录到。
3. 说完停 0.8 秒，录音自动结束。也可以再按一次键结束。
4. 等回答。Grok 看图要 8–13 秒。

第 4 键长按仍然是测试模式。短按不再开关灯。

Pico 固件没有专门的视觉键（`VISION_BUTTON_PIN = None`），所以 Pi 把 `mode` 的短按当作 K4。以后固件发 `vision` 键时两者都有效。

## 两类问题

| 问法 | 回答 |
| --- | --- |
| 前面是什么、我左边有什么 | Grok 描述画面，只播第一句 |
| 前面可以走吗、能不能过去、有没有障碍 | 以「可以走」或「不可以走」开头，后面跟原因 |

「可以走吗」这一类要在配置里打开 `walkable_answers`。不打开时回答固定是「只凭一个画面，我无法判断能不能安全通过。」

打开后的规则：

- Grok 同时拿到画面和手杖的测距读数（前方超声和激光取近的那个，再加头部高度的超声）。
- 前方读数在警告距离内（约 1 米），或头部高度读数在 1.2 米内，回答一定是「不可以走」。画面看起来空旷也不行。
- Grok 超时或画面不可用时，只要有近距离读数，仍然回答「不可以走，手杖测到前方大约 45 厘米有障碍。」
- 测距没有回波时告诉 Grok「无读数」，不当作前方没有东西。
- 前面是马路或路口时回答「不可以走」，因为一张图判断不了车流。

导航相关的话（「导航去…」）仍然回答「导航请用K2按键。」

## 配置

`config/camera.unified.toml`：

```toml
[assistant]
enabled = true
mode = "psyche_grok"
question_language = "zh"
answer_language = "zh"
asr_backend = "sensevoice"      # 默认 "vosk"
asr_model_dir = "voice/models/sense-voice"
endpoint_silence_s = 0.8        # 说完多久算结束
walkable_answers = true         # 默认 false
request_timeout_s = 25          # Grok 看图 8–13 秒，15 秒会经常超时
max_scene_age_s = 30            # 要比 request_timeout_s 大，否则慢回答会被丢掉
```

SenseVoice 需要 `pip install -e '.[sensevoice]'`（装 sherpa-onnx），`asr_model_dir` 里要有三个文件：`model.int8.onnx`、`tokens.txt`、`silero_vad.onnx`。

## 和避障警告的关系

避障警告的优先级仍然高于 K2/K4。有一处例外：一轮 K2 或 K4 对话期间，同一个障碍的测距警告不重复播。否则障碍在 0.6 米内时每秒一声「停」，提示音、录音和回答都会被打断。新出现的警告，或者同一个警告中断半秒以上再出现，照常打断。

台架上把 `config/config.toml` 的 `[hazard] max_valid_m` 设成 0.01 会让所有测距读数无效，测距避障全部关闭。走路前要改回 4.0。

## 实测

2026-11-23，Pi 5，iPhone 热点，AirPods Pro，实体第 4 键加真人说话，一轮：

| 环节 | 耗时 |
| --- | --- |
| 按键到提示音播完 | 0.9 秒 |
| 录音（说完自动结束） | 2.5 秒 |
| SenseVoice 识别 | 0.6 秒 |
| Grok 看图（3790 输入 token） | 10.5 秒 |
| Piper 合成 | 3.2 秒 |
| 播放回答 | 4.7 秒 |
| 按键到开始播回答 | 18 秒 |

同一晚直接调 Grok 接口 7 次，耗时 6.4–13.0 秒。网关上其他带 `fast` 的模型没有更合适的：`grok-4.7-build-fast` 也要 8 秒，`grok-chat-fast` 和 `grok-chat-auto` 看不到图，`grok-composer-2.5-fast` 把一面墙说成了斑马线。

## 已知问题

- 麦克风会把旁边人说的话一起录进去。
- 回答之前有十几秒没有声音。
- 开机后 AirPods 不会自己连上，要在 Pi 上执行 `bluetoothctl connect <MAC>`。
