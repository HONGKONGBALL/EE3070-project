# K2 语音对话选目的地（Grok）

盲人用户全程不用看屏幕：按 K2 用普通话说想去哪里，Grok 听懂意思后，Pi 在高德上搜索 iPhone 附近的地点，并把最近的几个读出来。用户再按 K2 说「第二个」，最后按 K1 开始导航。

## 流程

```
K2 短按 → 提示音 → 说话 → K2 短按
  → Vosk（普通话，离线）转文字
  → Grok 只返回一个 JSON 动作：search / choose / ask / cancel / confirm
      search  → Pi 用高德搜索手机定位附近的地点，按直线距离取最近 3 个，用 Piper 读出
      choose  → 只能选刚才读出的编号，Pi 会校验，然后暂存目的地
      ask     → 读出 Grok 的追问
      cancel  → 取消已选的目的地，或停止正在进行的导航
      confirm → 提示「请按K1开始导航」
  → K1 短按 → 现有的高德步行路线和逐段提示
```

例子：

> 用户（K2）：我想去附近的麦当劳
> 手杖：找到3个地点。第1个，麦当劳（太子店），约310米。第2个……请按K2说要去第几个。
> 用户（K2）：第一个
> 手杖：目的地，麦当劳（太子店），约310米。按K1开始导航。
> 用户：按 K1 → 开始导航

### 安全边界

- Grok 只负责理解用户说的话，**不规划路线，不给方向，也不判断安全**。路线和转向提示全部来自高德。
- Grok 只能从读出来的候选里选。编号不在列表里时不会暂存，会重新报一遍可选的编号。
- 开始导航必须按物理键 K1。用户口头说「好的、出发」时，只会提示按 K1。
- 「第一个/第二个/第三个」和「取消/算了」由本地直接处理，不走云端，更快，断网也能用。
- 云端出错（超时、额度用完、返回格式不对）时，退回本地处理：从这句话里提取地点名，直接去高德搜索，播报时先说一句「云端助手暂时不可用」。
- 超过 2 分钟没有对话、K2 长按，或 K1 开始导航以后，对话状态都会清空。

## Pi 上的准备

在 `speech_models_dir`（默认是 `voice/models`）里放好普通话模型：

```bash
cd voice/models
curl -fLO https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip && unzip -q vosk-model-small-cn-0.22.zip
curl -fLO https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx
curl -fLO https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx.json
```

如果 Pi 跑 medium 太慢，可以换成 `zh_CN-huayan-x_low`，然后在配置里设 `piper_voice`。

生成普通话的导航转向提示音（逐段提示用的是预先录好的音频）：

```bash
python tools/gen_prompts.py --backend espeak --lang cmn --config config/config.toml --out audio/navigation
```

没有生成 `audio/navigation/cmn/` 时，转向提示会自动回退到英文，日志里会有一条警告。

## 配置

`config/camera.unified.toml`：

```toml
[assistant]
enabled = true
mode = "psyche_grok"
question_language = "zh"
answer_language = "zh"
# destination_dialogue 在 answer_language = "zh" 时默认开启

[navigation]
enabled = true
language = "cmn"
```

Grok 模型默认用 `PSYCHE_VISION_MODEL`。如果想让文字对话用另一个模型，在 `psyche.env` 里设 `PSYCHE_CHAT_MODEL`。

注意：`answer_language = "zh"` 时，K4 的视觉回答和云端失败提示也会改成普通话。避障警告仍然用原来预录的音频，不受影响。

## 用英文说目的地

`[assistant] destination_language` 只管 K2：

| 值 | 效果 |
| --- | --- |
| 不写（默认） | K2 用 `question_language` / `answer_language`，和以前一样 |
| `"auto"` | 目的地可以用普通话或英语说，手杖用听到的那种语言回答 |
| `"en"` / `"zh"` | K2 固定用这一种语言，其余按键不受影响 |

写了这一项就会开启目的地对话，不必再写 `destination_dialogue = true`。

- 识别：SenseVoice 不管设成哪种语言，听到英语都写英文、听到普通话都写中文，所以 `"auto"` 不需要再加载一份模型。只有固定成与 `question_language` 不同的语言时才会多加载一份。
- 回答：按这句话里有没有汉字判断语言。英文回答用英文的 Piper 声音；如果 `vision_language = "en"`，就是 K4 已经加载的那一个。
- 给 Grok 的提示词、候选地点的读法（"I found 3 places. 1: Kowloon Tong, about 320 metres..."）、本地能直接处理的说法（"the second one"、"cancel"）都有英文版。
- 地图服务是 openrouteservice 时，地名按这句话的语言返回（Kowloon Tong / 九龙塘），见 [pi-map-provider.md](pi-map-provider.md)。高德只有中文地名。
- 走路时的转弯提示是预先录好的，语言由 `[navigation] language` 决定，不随 K2 这句话变化。

没有验证的：真实 Grok 对英文提示词的反应，以及 Pi 上真人说英语的识别效果。电脑上用合成语音试过，"MTR" 会被写成 "EMR"、"nearest" 会被写成 "nearst"，地点类别的匹配已经容纳这两种写法。

## 台架检查

1. iPhone 共享定位，最好在窗边或室外。
2. K2 说「我想去最近的地铁站」，应该听到 2–3 个候选和距离。
3. K2 说「第二个」，应该马上听到确认句（这一步不走云端）。
4. 按 K1，应该开始播报路线。
5. 拔掉网络后再按 K2 说一个地名，应该听到「云端助手暂时不可用，直接为你搜索」。高德也需要网络，所以接着会听到「地图搜索失败」。
6. 在 dashboard 的 `/status` 里，`assistant.destination_dialogue` 会显示当前的候选、已选目的地和上一次的动作。
