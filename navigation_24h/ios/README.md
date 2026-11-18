# NavigationCompanion

工程：`NavigationCompanion.xcodeproj`，共享 Scheme：`NavigationCompanion`。Bundle ID：`hk.ee3070.NavigationCompanion`，部署目标 iOS 17。

签名通过 Xcode 的 Signing & Capabilities 选择开发 Team，或在构建命令中设置 `DEVELOPMENT_TEAM`。手机打开开发者模式并信任开发签名后安装 App。在电脑通过 USB 运行 `python3 navigation_24h/tools/navctl.py pair-device --interactive`，再在 App 中搜索并选择导航设备。核对 iPhone 系统与电脑显示的六位数字，在两端确认；App 显示“设备绑定完成”后，确认目的地名称及 WGS84 坐标并开始导航。

Core Location 由前台操作启动，配置 `location` 后台模式、精确位置授权与系统导航指示；Core Bluetooth 配置 `bluetooth-central` 后台模式及 Central 状态恢复。绑定身份、手机会话、导航代次、最新测量序号和路线版本用于恢复核对。恢复后以新位置继续导航。

定位年龄从测量源计算，再由手机单调时钟累计；首片发送前重新计算年龄。时间同步请求携带 `phone_send_monotonic_ms`，ACK 回传该值及 `device_monotonic_ms`。手机以设备 ACK 时刻减手机 ACK 接收时刻计算时钟偏差下界；首片发送时将其映射为 `device_sent_lower_bound_ms`，由 ESP32 在重组完成时补入传输耗时上界。位置发送使用最近 30 秒内、RTT ≤1000 ms 的同步估计。独立心跳通过 `get_status` 更新摘要和时钟。

事件年龄上界采用手机当前单调时刻减设备事件时刻，再加设备与手机时钟偏差上界；该上界等于 RTT 中点估计再加 RTT/2。人工确认核对当前会话、代次、路线、事件 ID 及有效期。

会话页可导出 `navigation-session.jsonl`，记录测量、命令、ACK、状态、前后台与心跳；单文件达到 8 MiB 时轮转保存上一份。

主机协议断言：

```sh
CLANG_MODULE_CACHE_PATH="$PWD/navigation_24h/ios/.build/cache" \
SWIFTPM_MODULECACHE_OVERRIDE="$PWD/navigation_24h/ios/.build/cache" \
swift run --package-path navigation_24h/ios --scratch-path navigation_24h/ios/.build \
  --cache-path navigation_24h/ios/.build/cache --disable-sandbox ProtocolChecks
```

设备构建：

```sh
xcodebuild -project navigation_24h/ios/NavigationCompanion.xcodeproj \
  -scheme NavigationCompanion -configuration Debug -sdk iphoneos \
  -destination 'generic/platform=iOS' -derivedDataPath navigation_24h/ios/DerivedData \
  DEVELOPMENT_TEAM=YOUR_TEAM build
```

实现依据：[Apple 后台定位](https://developer.apple.com/documentation/corelocation/cllocationmanager/allowsbackgroundlocationupdates)、[Core Location 授权](https://developer.apple.com/documentation/corelocation/requesting-authorization-to-use-location-services)、[Apple Core Bluetooth 后台与恢复](https://developer.apple.com/library/archive/documentation/NetworkingInternetWeb/Conceptual/CoreBluetooth_concepts/CoreBluetoothBackgroundProcessingForIOSApps/PerformingTasksWhileYourAppIsInTheBackground.html)。
