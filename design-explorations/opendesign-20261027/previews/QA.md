# 设计与交互检查

2026-10-27：三套 HTML 预览与六张可编辑 Figma 画板均已通过检查。

| 方案 | 桌面与手机布局 | 页面横向溢出 | 手机操作目标 |
| --- | --- | --- | --- |
| A · Precision Lab | 通过 | 无 | 至少 44 × 44 px |
| B · Control Room | 通过 | 无 | 至少 44 × 44 px |
| C · Spatial Compass | 通过 | 无 | 至少 44 × 44 px |

## 检查内容

三套界面均包含 TFmini-S、前方和上方 HC-SR04，显示明确的演示标记、读数、超时、更新时间、TFmini 强度与原始单位。三路按各自时间记录，TFmini 解析间隔不作为真实采样间隔。C 的方向图只表达传感器朝向。

HTML 检查覆盖页面加载、通道隐藏与恢复、单位切换不影响超声波、诊断展开与收起，以及连接和 CSV 控件的预览反馈。B 另检查连接模式与 CSV 预览对话框。页面无 JavaScript 错误；检查没有访问 USB 或串口。

Figma 检查覆盖完整单行主读数、字体、标题、手机曲线标签、异步时间说明、记录表对齐，以及原生文本、矢量和按钮组件。最终六张画板均无整页图像填充。

这些结果验证设计和演示交互。真实采集流程与硬件验证记录见 [统一测距程序](../../../distance_mac_viewer/README.md)。

## 预览与记录

- HTML：[桌面对比](comparison-desktop.png)、[手机对比](comparison-mobile.png)。
- Figma：[A 桌面](figma-A-desktop-browser.jpg) / [A 手机](figma-A-mobile-browser.jpg)、[B 桌面](figma-B-desktop-browser.jpg) / [B 手机](figma-B-mobile-browser.jpg)、[C 桌面](figma-C-desktop-browser.jpg) / [C 手机](figma-C-mobile-browser.jpg)。
- B 手机：[读数细节](figma-B-mobile-detail-browser.jpg)、[时间说明与记录细节](figma-B-mobile-timing-browser.jpg)。
- 自动检查数据：[页面加载与布局](browser-checks.json)、[交互检查](interaction-checks.json)。可选复核脚本 [check-interactions.cjs](check-interactions.cjs) 需要 Node.js、Playwright 和正在运行的本地预览服务。
- 最终几何：[Figma 源数据](../figma/README.md)。

对比 PNG 来自 HTML 预览；Figma JPG 来自实际画板。启动本地预览与方案说明见 [设计 README](../README.md)。
