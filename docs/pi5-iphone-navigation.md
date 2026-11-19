# Pi 5 语音目的地联调

iPhone 在 Safari 向 Pi 5 持续共享位置。Pico 通过 USB 上报 K2，Pi 5 用 USB 麦克风录音并运行本地 Vosk。Pi 5 搜索附近地点，按当前搜索出发点的直线距离排列最多五个候选，在 iPhone 上供你选择。

## 准备 Pi 5

在 64 位 Raspberry Pi OS 上进入仓库目录。把运行项目 MicroPython 固件的 Pico 和 USB 麦克风接上 Pi，准备音箱或耳机。

```bash
sudo apt install python3-venv alsa-utils openssl unzip curl espeak-ng qrencode
python3 -m venv --system-site-packages .venv
. .venv/bin/activate
python -m pip install -e '.[pi,iphone]'
cp -n config/config.example.toml config/config.toml
arecord -l
aplay -l
ls /dev/serial/by-id/
```

根据上面的设备列表配置 `config/config.toml` 中的 `pico.port`、`voice.device` 和 `audio.device`。普通话用 `general.language = "cmn"`。在 Pi 的运行环境设置项目已有的 `AMAP_KEY`，供普通地点搜索、手动出发点查询和步行规划使用。24/7 FITNESS 门店由[品牌目录](https://247.fitness/en/contact_us/stores)提供，Pi 用 iPhone 精确位置在本地计算这些门店的距离。

```bash
mkdir -p models
curl -fL https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip -o /tmp/ee3070-vosk-cmn.zip
unzip -q /tmp/ee3070-vosk-cmn.zip -d models
mv models/vosk-model-small-cn-0.22 models/vosk-cmn
python tools/gen_prompts.py --backend espeak --lang cmn --config config/config.toml
```

## 配对并联调

1. 将 Pi 和 iPhone 接入同一局域网，或将 iPhone 连到 Pi 的 Wi-Fi 热点。Pi 通过联网的接口获取地点与步行数据。
2. 在 Pi 执行 `python tools/setup_pi_phone.py --profile`。多网卡时用 `--address` 指定 iPhone 可以访问的 Pi IPv4 地址。
3. iPhone 用 Safari 扫描第一个二维码下载配置描述文件。在「设置 → 通用 → VPN 与设备管理」安装 **EE3070 Pi 5 Location**，再到「设置 → 通用 → 关于本机 → 证书信任设置」启用该证书的完全信任。此步骤每套配对证书执行一次。[Apple 证书信任说明](https://support.apple.com/en-us/102390)
4. 在 Pi 执行以下命令。iPhone 打开终端给出的第二个二维码，点「开始共享定位」并允许精确定位。联调时让 Safari 页面保持在前台。

   ```bash
   python -m cane --config config/config.toml --phone-pair certs/pi-phone
   ```

5. 短按 K2，听到「请说目的地」后说「附近最近的二四七健身房」，说完再短按 K2。页面显示候选和距离。也可以在输入框修正识别的搜索词。
6. 在 iPhone 选中一个地点，然后短按 K1 请求步行规划。Pi 会播报路线提示。想换搜索出发点时，点「修改出发点」并选择地点，或点「用 iPhone 当前位置」恢复按手机位置排序。

再次联调时执行 `python tools/setup_pi_phone.py` 取对应配对链接。Pi 更换 IP 或证书到期后，可用 `--directory certs/pi-phone-new` 生成新配对，并把启动命令中的目录改为同一个值。
