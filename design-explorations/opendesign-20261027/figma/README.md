# 可编辑 Figma 设计

[打开完整文件](https://www.figma.com/design/gRakiEjVhFartJGVOWtVsU)

| 方案 | 桌面画板 | 手机画板 |
| --- | --- | --- |
| A · 精密仪器 | [1440 × 1002](https://www.figma.com/design/gRakiEjVhFartJGVOWtVsU?node-id=4-101) | [390 × 1724](https://www.figma.com/design/gRakiEjVhFartJGVOWtVsU?node-id=4-299) |
| B · 测量控制台 | [1440 × 1005](https://www.figma.com/design/gRakiEjVhFartJGVOWtVsU?node-id=4-1162) | [390 × 1511](https://www.figma.com/design/gRakiEjVhFartJGVOWtVsU?node-id=4-1446) |
| C · 方向可视化 | [1440 × 1002](https://www.figma.com/design/gRakiEjVhFartJGVOWtVsU?node-id=4-650) | [390 × 1497](https://www.figma.com/design/gRakiEjVhFartJGVOWtVsU?node-id=4-856) |

三套方案位于独立页面。界面使用原生文本、可编辑矢量和本地按钮组件实例，并按方案组织主题变量与文本样式；整页没有位图填充。所有读数均为设计演示，实际采集见 [统一测距程序](../../../distance_mac_viewer/README.md)。

## 源文件

- 六份 `*-dom.json`：最终 HTML 的几何、文字与样式数据。
- [build-from-layout.js](build-from-layout.js)：原生 Figma 图层构建代码。
- [prepare-payload.cjs](prepare-payload.cjs)：生成可交给 Figma Plugin API 执行的构建脚本，例如 `node figma/prepare-payload.cjs A`。
- [capture-layout.cjs](capture-layout.cjs)：从本地 HTML 预览重新采集几何数据；需要 Node.js 和 Playwright。
- [state.json](state.json) 与 [links.json](links.json)：文件、页面和画板索引。

启动 HTML 预览的方法见 [设计说明](../README.md)。最终效果：[A 桌面](../previews/figma-A-desktop-browser.jpg) / [A 手机](../previews/figma-A-mobile-browser.jpg)、[B 桌面](../previews/figma-B-desktop-browser.jpg) / [B 手机](../previews/figma-B-mobile-browser.jpg)、[C 桌面](../previews/figma-C-desktop-browser.jpg) / [C 手机](../previews/figma-C-mobile-browser.jpg)。[检查记录](../previews/QA.md)同时说明 HTML 控件的演示范围。
