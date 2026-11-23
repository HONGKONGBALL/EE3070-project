# NavigationCompanion

工程：`NavigationCompanion.xcodeproj`，共享 Scheme：`NavigationCompanion`。Bundle ID：`hk.ee3070.NavigationCompanion`，部署目标 iOS 17。

## 安装与导航

在 Xcode 的 Signing & Capabilities 中选择自己的开发 Team，或在设备构建命令中设置 `DEVELOPMENT_TEAM`。iPhone 开启开发者模式并信任开发签名后安装 App。通过 USB 在电脑运行 `python3 navigation_24h/tools/navctl.py pair-device --interactive`，再在 App 中搜索并选择导航设备。核对 iPhone 和电脑显示的六位数字，在两端确认。

App 显示「设备绑定完成」后，按 K2 开始录音，再按 K2 提交。按 K3 切换播报的地点候选，按 K1 确认已播报的地点。App 使用 iPhone 的本机语音识别和地点关键词解析。附近搜索取得手机位置后按直线距离排序。也可以在 App 中输入地点名称，或手动输入 WGS84 坐标确认目的地。

## 可选 Grok 关键词解析

项目默认在 iPhone 上提取地点关键词。要使用自有的 OpenAI 兼容服务，在个人私有配置目录创建 `GrokDestinationCredentials.json`，用实际服务地址、模型名和 API Key 替换以下示例值。

```json
{
  "endpoint": "https://provider.example/v1/chat/completions",
  "model": "MODEL_NAME",
  "apiKey": "YOUR_API_KEY"
}
```

`endpoint` 使用可信 HTTPS 服务的完整请求地址。API Key 放在 `apiKey` 字段，App 使用 Bearer 请求头认证，并从系统 TLS 获取服务器身份验证。首次打开已安装的 App 后，将配置导入它的数据容器。

```sh
xcrun devicectl device copy to --device DEVICE_ID \
  --domain-type appDataContainer --domain-identifier hk.ee3070.NavigationCompanion \
  --source /path/to/GrokDestinationCredentials.json \
  --destination Library/Caches/GrokDestinationCredentials.json
```

重新打开 App 后，有效配置进入系统钥匙串，导入文件随即移除。启用此配置时，服务接收语音转写文本并返回关键词。语音识别和地点距离计算在手机上运行，服务遇到问题时 App 使用本机关键词解析。

## 位置与蓝牙

Core Location 由前台操作启动，配置 `location` 后台模式、精确位置授权与系统导航指示；Core Bluetooth 配置 `bluetooth-central` 后台模式及 Central 状态恢复。绑定身份、手机会话、导航代次、最新测量序号和路线版本用于恢复核对。恢复后以新位置继续导航。

定位年龄从测量源计算，再由手机单调时钟累计；首片发送前重新计算年龄。时间同步请求携带 `phone_send_monotonic_ms`，ACK 回传该值及 `device_monotonic_ms`。手机以设备 ACK 时刻减手机 ACK 接收时刻计算时钟偏差下界；首片发送时将其映射为 `device_sent_lower_bound_ms`，由 ESP32 在重组完成时补入传输耗时上界。位置发送使用最近 30 秒内、RTT ≤1000 ms 的同步估计。独立心跳通过 `get_status` 更新摘要和时钟。

事件年龄上界采用手机当前单调时刻减设备事件时刻，再加设备与手机时钟偏差上界；该上界等于 RTT 中点估计再加 RTT/2。人工确认核对当前会话、代次、路线、事件 ID 及有效期。

会话页可导出 `navigation-session.jsonl`，记录测量、命令、ACK、状态、前后台与心跳；单文件达到 8 MiB 时轮转保存上一份。

## 构建与检查

运行主机协议测试和断言。

```sh
swift test --package-path navigation_24h/ios
```

```sh
CLANG_MODULE_CACHE_PATH="$PWD/navigation_24h/ios/.build/cache" \
SWIFTPM_MODULECACHE_OVERRIDE="$PWD/navigation_24h/ios/.build/cache" \
swift run --package-path navigation_24h/ios --scratch-path navigation_24h/ios/.build \
  --cache-path navigation_24h/ios/.build/cache --disable-sandbox ProtocolChecks
```

编译 iPhone App 供安装。

```sh
xcodebuild -project navigation_24h/ios/NavigationCompanion.xcodeproj \
  -scheme NavigationCompanion -configuration Debug -sdk iphoneos \
  -destination 'generic/platform=iOS' -derivedDataPath navigation_24h/ios/DerivedData \
  DEVELOPMENT_TEAM=YOUR_TEAM build
```

实现依据。[Apple 后台定位](https://developer.apple.com/documentation/corelocation/cllocationmanager/allowsbackgroundlocationupdates)、[Core Location 授权](https://developer.apple.com/documentation/corelocation/requesting-authorization-to-use-location-services)、[Apple Core Bluetooth 后台与恢复](https://developer.apple.com/library/archive/documentation/NetworkingInternetWeb/Conceptual/CoreBluetooth_concepts/CoreBluetoothBackgroundProcessingForIOSApps/PerformingTasksWhileYourAppIsInTheBackground.html)。
