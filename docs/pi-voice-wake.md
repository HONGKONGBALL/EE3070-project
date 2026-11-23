# 语音唤醒「嘿，阿帕次」（Pi 5 + Grok）

不按键，说一句话，手杖就做 K2 或 K4 的事。

## 怎么用

- 一口气说完：「嘿，阿帕次，我想去最近的地铁站」。听到一声短提示音，表示听到了。
- 或者只说「嘿，阿帕次」。听到两声提示音后再说要求，和按键之后一样。
- 说完唤醒词停一下再说要求也可以，0.8 秒内接着说的话算同一句。

| 说的话 | 手杖做什么 |
| --- | --- |
| 我想去…、带我去…、导航到…、附近的…、第一个、取消导航 | K2 的目的地对话 |
| 前面有什么、这是什么 | K4：Grok 看图回答 |
| 前面可以走吗、能不能过去 | K4 的「可以走吗」回答，永远不会交给目的地对话 |
| 下一步怎么走、还有多远 | 直接从当前路线回答，不经过云端。K4 按键问这两句也一样 |

开始导航仍然要按 K1。语音选好目的地后手杖会说「按K1开始导航」。

手杖正在播报时不监听。按键随时可用：按 K2 或 K4 时手杖会让出麦克风。

## 怎么实现

没有单独的唤醒词模型。两次按键之间，手杖用 Silero VAD 把麦克风附近的每一句话切出来，交给已经在用的 SenseVoice 识别，再看这句话是不是以唤醒词开头。

SenseVoice 会把「嘿阿帕次」写成读音相近的各种字（黑阿帕兹、嘿阿巴斯、阿八兹、K安帕），有时还会吞掉头尾的音节。所以匹配按每个字的读音来，不按字面，「阿帕」两个音必须在。规则和读音表在 `pi/cane/vision/wake_word.py` 的 `spoken_wake()`，来自使用者本人约一百条录音。

问路还是提问：先由本地规则判断（`DestinationDialogue.wants()`）。说法含糊时（「厕所在哪」）交给 Grok，它可以回答「这不是在找地方」（`{"action": "other"}`），或者在看图的那一步回答 `NAVIGATE` 把话交还给目的地对话。每个方向只转交一次。

试过 sherpa-onnx 的关键词模型（`kws-zipformer-zh-en-3M`，拼音关键词）：对「阿帕次」这种自造词只有一半左右的命中率，所以没有用。

## 配置

`config/camera.unified.toml`：

```toml
[assistant]
mode = "psyche_grok"
asr_backend = "sensevoice"   # 语音唤醒只在 SenseVoice 下工作
wake_enabled = true          # 默认 false
wake_silence_s = 0.7         # 停顿多久算一句话结束
```

不需要新的模型文件，用 `asr_model_dir` 里已有的三个。模型缺失或识别出错时语音唤醒自己关闭，按键不受影响。麦克风不可用时不播报，等麦克风回来再继续听。

## 已知限制

- 用使用者本人的 99 条录音回放：唤醒 90 条。3 条平常说话和 81 句日常句子没有误唤醒。换一个人说，命中率要重新测。
- 只认中文读法「嘿，阿帕次」。字母读法 A-P-T 和韩语读法只在本地 Ollama 模式下有（见 `hey-apt.md`）。
- 旁边有人说话时手杖会一直在识别。每句只先识别前 3 秒，Pi 上的 CPU 占用还没有测。
- 真实 Grok 对 `other` 和 `NAVIGATE` 两条提示的反应还没有测，电脑上的预演用的是规则替身。

## 给另一个人调

```
python tools/cane_voice_samples.py --models <SenseVoice 目录>
```

在浏览器打开 `http://localhost:8765`，照着读，每条会显示手杖把它听成了什么、会不会唤醒。录音和结果在 `voice-samples/`（不进仓库）。按结果补 `wake_word.py` 的读音表，再跑 `tests/test_wake_assistant.py`。

电脑上的整套预演（真实 VAD 和 SenseVoice，句子由 macOS `say` 合成）：

```
AMAP_KEY=... python tools/cane_rehearsal.py --speech-models <SenseVoice 目录>
```
