生成的提示音放在这里，按语言分目录：`audio/cmn/`、`audio/yue/`、`audio/en/`，文件名是提示句 ID（如 `obstacle_front.wav`）。
由 `tools/gen_prompts.py` 生成，不入库。

Pi 摄像头模块的英语提示音使用 `tools/gen_camera_prompts.ps1`，在装有 Microsoft David Desktop 语音的 Windows 电脑上生成，再复制 `audio/en/*.wav` 到 Pi 项目同目录。该脚本和整机三语生成器分别使用。

```powershell
./tools/gen_camera_prompts.ps1
# 只更新 awareness 模式的四条提示：
./tools/gen_camera_prompts.ps1 -AwarenessOnly
```

四条 awareness 提示为 `puddle_ahead`、`damaged_pavement`、`stairs_ahead`、`crowd_ahead`。文本与本次部署 WAV 的校验记录见 [awareness-voice-artifacts.json](../config/awareness-voice-artifacts.json)。校验值记录的是已部署文件；不同 Windows 语音版本重新生成的文件未必逐字节相同，应核对文本并重新记录实际产物，而不能伪称哈希一致。

运行时使用本地 WAV，不进行网络 TTS。蓝牙／USB 输出选择见 [耳机设置](../docs/pi-audio-outputs.md)。本仓库不包含音频二进制；只克隆源码不会自动得到可播放的提示音。
