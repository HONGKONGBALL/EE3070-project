# Pi 最新视觉基线部署 — 2026-10-31

已部署并启用 `smart-cane-camera` 用户服务。项目位于 Pi 的 `/home/gp7_3070/EE3070-project`，设备 `3070gp7`，本次局域网地址 `192.168.1.99`。

Windows 预览：[http://127.0.0.1:8010/](http://127.0.0.1:8010/)。隐藏 SSH 转发由本次部署建立，转到 Pi 的 `127.0.0.1:8000`；需要保持电脑转发进程及 Pi 网络连接。Pi 自己可直接访问 `http://127.0.0.1:8000/`。

本次使用 `smart-cane-stairs-update-20261030.zip`。其 53 个运行时代码／配置相关条目与本地最新版比较一致；212 个载荷文件全部校验。服务重启后的运行 ID 为 `1786ac93e9`，以 ground 模式、真实 C270 相机运行，不启用 Pico 或模拟距离。保留旧环境与未引用模型，没有安装训练依赖。

## Pi 运行配置

没有修改模型权重、类别、置信阈值或判断规则。根据 Pi 的实际 CPU 耗时，只调整采样和线程数：

- 摄像头目标 15 FPS、网页预览目标 12 FPS。
- road、pothole、puddle 采样上限各 1 FPS。
- semantic／stairs-context 使用两线程，采样上限保留 1.5 FPS。
- 用户选择“现在先只看画面”，因此 `[audio] enabled=false`。

Pi 当前没有检测到 HyperX USB 接收器，只有虚拟 Dummy Output。以后使用声音时，将接收器接 Pi 并打开耳机，确认真实输出设备后再开启音频，不能把当前无声状态当作听音通过。

## 实机检查

真实摄像头检查持续 35.16 秒，60 个启动后稳定样本全部就绪，无 HTTP 异常或模型错误，服务自动重启次数为零。

| 指标 | 实测 |
|---|---:|
| 相机采集 | 14.9 FPS |
| 电脑预览中位数 | 11.8 FPS |
| 状态接口 p95（含 Wi‑Fi／SSH） | 87.26 ms |
| 图片接口 p95（含 Wi‑Fi／SSH） | 152.60 ms |
| 裂损／坑洞／积水任务 | 各约 1 FPS |
| 语义／楼梯任务 | 约 1.34 FPS |
| 语义／楼梯推理 p95 | 816.8 ms |
| 结束温度 | 65.9°C |
| 欠压／降频状态 | `0x0` |

最初单线程语义／楼梯推理约 1.3 秒，调整采样调度后约 0.8 秒。实时预览已在 Windows 通过 SSH 转发返回有效 JPEG，帧号持续前进。

当前镜头朝门框、墙面，并没有对准行走地面。此检查证明实际相机与模型链路正常运行，不代表本次已验收积水、坑洞、楼梯的真实路面识别或声音。下一步把镜头向下固定对准地面，在受控条件下复测相关场景。

## 备份与记录

Pi 原覆盖文件备份：`/home/gp7_3070/.smart-cane-deploy/20261031/before-update-20261031T060024Z.tar.gz`。

Pi 部署目录：`/home/gp7_3070/.smart-cane-deploy/20261031/`，含校验、配置覆盖和部署结果。调度修改前的配置为 `camera.before-pi-runtime-tuning.toml`。

Windows 证据目录：`D:/SmartCaneRuntime/deploy/pi-20261031/`，含：

- `final-deployment-check.json`：212 个文件无校验差异（配置以已记录 Pi 覆盖为准）、服务及模型状态、温度和欠压记录。
- `live-validation.json`、`live-validation-samples.json`：本次实际 Pi 与相机短时检查。
- `camera.pi.toml`：Pi 当前实际配置；与开发电脑默认配置分别保存。
- `deployment-report.json`、`pi-runtime-tuning.json`：覆盖文件列表、备份和调度调整。

服务需要重启时，在 Pi 的 SSH 会话执行 `systemctl --user restart smart-cane-camera`。该命令正常执行通常没有文字输出，是否成功应看服务状态与预览页面。

本次没有操作用户现有 SSH 终端、重刷 SD 卡、修改 Wi‑Fi、发布 Git 或联调 Pico。
