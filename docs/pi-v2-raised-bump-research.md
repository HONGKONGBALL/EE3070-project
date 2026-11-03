# 凸起路面研究：SBP-YOLO 基线

日期：2026-10-27。目标仍是 PRD 的真实物理不平整；本次仅核查减速带这一子类，没有把它改名为全部不平整或宣布完整能力通过。

## 取得的实际文件

[作者仓库](https://github.com/chuanqi1997/SBP-YOLO)，revision `37d6edaa3df0ded55842d94b513c926ab6dd1660`，公布了训练后的模型下载。[作者权重目录](https://drive.google.com/drive/folders/1Z8WspHFXUirS-V3UZ-aibALTGr-3YfhE)中的 `pre_trained_weights` 子目录包含真实文件。选择标准 YOLO11 基线，不把自定义 LEDH 等模型一起加入运行程序。

- 文件 `yolo11.pt`，5,465,512 字节，SHA-256 `0e7f2f2faf22fee544c8b5045636b96112bbb0efcd36a34f9e4582011e3ccd81`。
- 实际类表：0=`Pothole`，1=`SpeedBump`；2,590,230 参数。没有把 COCO 初始化模型当成专用模型。
- 使用 `weights_only=True` 和已检查的 Torch/Ultralytics 类加载。检查点还引用 dill 的类型辅助函数；本地替代函数只允许静态检查到的 set/NoneType，不执行下载的任意 Python 或关闭受限加载。
- 模型来源仓库 GPL-3.0；研究文件不随本项目源码分发。没有训练或微调。

电脑研究目录 `D:\SmartCaneRuntime\research\sbp-yolo`，包括来源 README、公开文件清单、原始权重、`baseline-inspection.json` 和导出结果。Pi 未安装 Torch 或 dill，也未部署此候选。

## 转换与初步结果

标准 NCNN 416×416 浮点导出，输出 `[1,6,3549]`。运行文件已复制到电脑 `D:\SmartCaneRuntime\models\v2\raised-bump-research`：

| 文件 | 字节数 | SHA-256 |
| --- | --- | --- |
| model.ncnn.param | 22,701 | 7d70056c2cadee37cc193ff711930154d1928522c0f9fbd2f7138ef6ab43fe35 |
| model.ncnn.bin | 10,373,108 | eb1aaa93c9431018403469d8da657cc79f2c1e9d9be464aae3f37eb83bc0c381 |

解码器只将真正的 `SpeedBump` 原始类映射为 `raised_bump`，不将裂缝、普通 step 或 Other 当作物理高差。默认配置没有选择此权重，策略也没有启用对应语音。

先人工审阅 8 张 Commons 照片，再运行原始 Torch 和 NCNN，统一 640×480 补边、416 输入、confidence=0.4、NMS=0.45：

- 2 张有可见已安装凸起轮廓的照片均未检出 SpeedBump（0/2）。
- 4 张未见凸起的过街条纹照片无 SpeedBump 输出（0/4）。
- 另 2 张含糊/诊断图保留但不计分：只有标线且轮廓不明确、拆下堆叠的橡胶模块。后者被两个后端一致判为 Pothole 0.407，不把它算作正确坑洞。
- 两个后端的类别、框/分数在这些输出中一致，尚不能证明在充分正例上转换没有损失。没有在 Pi 做时序、声音、性能或室外验证。

清单 `config/raised-bump-research-scenes.json`，原图 `D:\SmartCaneRuntime\testdata\bump-research`，报告 `D:\SmartCaneRuntime\evidence\v2-raised-bump-conversion.json`。当前样本不足且正例失败，不能启用为可用功能；后续需补明确凸起正例、平面标线负例，并检验原始训练输入分辨率/域差异，不能靠降低阈值展示任意检测框。

## 其他候选的边界

[PedWalk-Yolo](https://huggingface.co/EddyPhyanqz/PedWalk-Yolo) revision `48c0982ffc25275aac3e31cc3a1448cdf0cc50da` 发布了实际 6,555,881 字节 best.pt（SHA-256 `6d12552ee22c0dab2c400d91e0b67994dc3de2f5813fdc70b782f718678bba25`），但检查点需要来源文件中没有给出的 `cbam.CBAM/ChannelAttention/SpatialAttention/C2f_CBAM` 定义。未猜测架构、执行不明加载器或部署到 Pi；作者四类仍是裂缝/坑洞，不覆盖物理不平整。

[AGSENet](https://github.com/Lyu-Dakang/AGSENet)当前 README 提供 Puddle-1000 数据下载与训练/验证代码，未核实到可直接使用的发布训练权重。本轮没有为它开始训练，也不把数据集链接当可用模型。
