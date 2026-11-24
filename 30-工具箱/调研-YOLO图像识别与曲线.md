# YOLO 调研报告：图像对象识别 / 视频中的曲线识别

调研日期：2026-09-23　范围：YOLO 家族现状、曲线类目标适配性、许可与落地路线

---

## 一、结论先行

1. **做「图像对象识别」选 YOLO 是对的**：它是目前工程化最成熟的目标检测库，文档、预训练权重、导出链路（ONNX / TensorRT / OpenVINO / CoreML）、视频跟踪都是一条龙，上手成本远低于其他方案。
2. **用哪个版本：YOLO26**。2026 年 1 月发布，当前主线版本，覆盖 7 类任务 + 开放词表；官方同时把 YOLO11 列为「稳定生产可用」。
3. **做「曲线」不能用默认的检测任务**。YOLO 默认输出轴对齐矩形框，而曲线细、长、弯，一个矩形框里绝大部分是背景，标注和回归都不稳。必须换成下面四种形态之一（见第三节）——**推荐 `-seg` 实例分割**。
4. **最大的风险不是技术而是许可**：Ultralytics 是 AGPL-3.0 + 商业授权双许可，官方明确写了「内部业务工具」也要买企业授权，自训练模型同样受约束。这一条建议在动手前先定。
5. 如果「曲线」指的是**图表/仪表上的折线，最终要读出数据点**，YOLO 不是最优工具，用专用取数工具（WebPlotDigitizer 等）更准更快；YOLO 只负责定位曲线区域。

---

## 二、YOLO 版本格局（截至 2026-09）

| 版本 | 状态 | 说明 |
|---|---|---|
| **YOLO26** | **最新主线**，2026-01 发布，有 arXiv 论文 | 全任务 + 开放词表（YOLOE-26），官方推荐新项目使用 |
| YOLO27 | 预告中 | 无权重、无发布时间，不要等 |
| YOLO11 | 成熟稳定 | 全任务权重齐全，官方列为稳定生产选项 |
| YOLO12 | 社区版 | 注意力机制为主，只有检测权重 |
| YOLOv10 | 社区版（清华） | NMS-free 检测研究 |
| YOLOv9 | 社区版 | PGI 梯度信息方案 |
| YOLOv8 | 旧管线 | 存量项目与第三方集成仍在用 |
| YOLOv7 / v6 / v5 / v4 / v3 | 历史版本 | 仅作参考或存量维护 |

### YOLO26 的技术变化（值得知道的几点）

- **原生端到端、免 NMS**：可选 one-to-one 头，导出时 `nms=False` 直接出结果，部署链路更简单；默认仍用 one-to-many 头 + NMS 拿最高精度，两个头都会训练。
- **DFL-free 回归**：去掉 Distribution Focal Loss，检测头更简单、回归范围不受限。
- **训练配方**：MuSGD（Muon + SGD 混合优化器）、Progressive Loss、STAL（小目标感知标签分配）。
- **公开指标**：检测 COCO 40.9–57.5 mAP，T4 TensorRT 延迟 1.7–11.8 ms；YOLO26n 的 CPU ONNX 推理比 YOLO11n 快最多 43%；实例分割比 YOLO11 提升最多 +2.5 box AP / +3.7 mask AP；姿态 +7.2 AP；OBB 在 DOTA-v1.0 上 +3.4 mAP。

---

## 三、「曲线」怎么用 YOLO 做（关键章节）

YOLO26 提供 7 类任务，对应曲线的四种可行形态：

| 任务 | 权重后缀 | 输出 | 对曲线的适用性 |
|---|---|---|---|
| 目标检测 | `yolo26n.pt` | 轴对齐矩形框 | ❌ 不推荐。细长弯曲目标框内多是背景，标注歧义大 |
| **实例分割** | `yolo26n-seg.pt` | 每个实例的像素掩膜 | ✅ **首选**。直接抠出曲线像素，再骨架化/描点就能拿到折线数据 |
| 语义分割 | `yolo26n-sem.pt` | 整幅 (H,W) 类别图，无实例、无框、无置信度 | ✅ 只需要「哪里有曲线」、不必区分是哪一条时用 |
| 旋转框 OBB | `yolo26n-obb.pt` | 带角度的矩形框（四点归一化标注） | ⚠️ 适合直的/斜的细长目标（线缆、杆件）；一条弯曲线用一个旋转框描述会失真 |
| 姿态关键点 | `yolo26n-pose.pt` | 每个实例的 N 个关键点 | ✅ 若最终要「参数化曲线」（如曲线上 10 个有序点），这是最直接的做法 |

### 细长目标特有的工程坑（务必提前知道）

1. **分辨率**：`imgsz=640` 下细线可能只有 1–2 像素宽。建议 1280 起步，或对大图做切片推理（SAHI 思路）再合并结果。
2. **类别设计**：先定清楚「整条曲线算一个实例」还是「分段算多个实例」，标注规则一次定死，否则返工成本极高。
3. **视频不要逐帧全跑**：抽帧（按业务需要的频率）+ `model.track()` 跟踪，既省算力又能拿到跨帧一致 ID。
4. **跟踪器选择**：Ultralytics 内置 6 个跟踪器——BoT-SORT、ByteTrack、OC-SORT、Deep OC-SORT、FastTracker、TrackTrack（当前默认）。手持/移动镜头用 BoT-SORT，非线性运动用 OC-SORT，最省算力用 ByteTrack。跨帧续接要 `persist=True`，不同视频之间不要复用。ReID 默认关闭，需要时 `with_reid: True`。
5. **后处理**：分割掩膜出来后通常还要做骨架化 + 路径追踪，才能变成「一条有序曲线」；这一步是传统 CV 代码，不是模型能替代的。

---

## 四、许可与合规（最需要提前决策的一项）

Ultralytics 采用**双许可**：AGPL-3.0（免费）或付费企业授权。

- **AGPL-3.0 的义务**：需公开「整个衍生作品的完整对应源码，包括更大的应用、修改、脚本、配置文件，以及适用的**模型权重**」。自己训练的模型默认也归 AGPL。
- **企业授权的适用范围（官方明确列出）**：内部业务工具 / 私有公司应用、任何商业产品或服务、闭源软件、SaaS 平台与 API、嵌入式部署、商用场景下的自训练模型、不开源的研发。**即使从零自训练、不使用预训练权重、只在公司内部使用，也同样适用。**
- 建议：若结果要进公司业务链路（哪怕只是内部工具），**要么采购企业授权，要么改用宽松许可的模型**。这不是律师意见，商务条款建议直接与 Ultralytics 确认。

### 许可更宽松的替代（Apache-2.0 / MIT 系）

| 方案 | 许可 | 任务 | 备注 |
|---|---|---|---|
| **RT-DETR** | Apache-2.0（已核实） | 检测 | DETR 系实时检测，RT-DETRv2 有 S/M/L/X；**无分割任务**；生态易用性不如 Ultralytics |
| **YOLOX** | Apache-2.0（已核实） | 检测 | 无锚框，nano/tiny/s/m/l/x；同样只有检测 |
| PP-YOLOE / PaddleDetection | 宽松许可（以仓库为准） | 检测/分割 | Paddle 生态，国内资料多 |
| MMDetection | Apache-2.0 | 检测/分割全家桶 | 模型最全，但工程链路最重 |
| **YOLOP** | **MIT** | 检测 + 可行驶区域 + **车道线** | 若「曲线」是道路/车道线这类，是现成的 MIT 方案 |

> 权衡：宽松许可的方案在「文档 + 预训练权重 + 一键导出 + 视频跟踪」这条链路上，没有一家能做到 Ultralytics 的顺手程度。省许可费往往要用工程时间换。

---

## 五、落地路线（建议分三阶段，总计约 3–4 周）

**阶段 0：定方向（1–2 天）**
- 明确曲线形态与输出要求（框 / 掩膜 / 参数化曲线 / 数值）
- 决定许可路线（买企业授权，或改 RT-DETR / YOLOX / YOLOP）
- 准备训练环境（Python + PyTorch + Ultralytics；Windows 下训练代码要包 `if __name__ == "__main__":`，否则多进程会报错，且 Windows 不支持多卡训练）

**阶段 1：跑通基线（3–5 天）**
- 从目标视频采样 200–500 帧，用 CVAT / Label Studio / Roboflow 标注（分割掩膜或关键点）
- 用 `yolo26n-seg.pt` 迁移学习微调，先看能否过拟合小样本
- 关键参数：`epochs=100`、`imgsz=1280`（细线）、`batch=16` 或 `-1`（自动）、`device=0`

```python
from ultralytics import YOLO
model = YOLO("yolo26n-seg.pt")
model.train(data="curve.yaml", epochs=100, imgsz=1280, batch=-1, device=0)
```

**阶段 2：达到可用（1–2 周）**
- 扩标注到 1000–3000 张，重点补难例：低对比度、遮挡、多曲线交叉、极端角度
- 建立双指标：模型指标（box AP / mask AP）+ 业务指标（曲线取点误差、漏检率）
- 确定抽帧频率与跟踪器组合，在真实视频上做端到端回归

**阶段 3：部署（约 1 周）**
- 导出：`model.export(format="onnx")` 或 TensorRT / OpenVINO，按目标硬件选
- 端到端推理可开 `nms=False` 简化链路；CPU 场景下 YOLO26n 的 ONNX 表现优于 YOLO11n
- 接视频流：抽帧 → 推理 → `model.track()` → 后处理出曲线

---

## 六、价格调研（2026-09 核实）

### 6.1 Ultralytics 授权：不公开报价，必须询价

- 官网定价页上 Enterprise 一栏是 **"Custom / 联系我们"**，企业授权（Enterprise License）只包含在 Enterprise 方案内，没有任何公开数字。
- GitHub 讨论区相关贴（"Regarding pricing" 2024-01、"Request for Commercial License to Avoid GPL-3.0 Issues" 2024-08、"A question about the license" 2023-03）均无公开报价。
- **结论：这是本次调研中唯一没有明码价、但必须先解决的一项。** 建议把「发出询价」作为独立前置动作，因为报价高低直接决定走哪条路线。

### 6.2 有明码价的部分（可直接用来搭预算）

| 项目 | 价格（已核实） | 备注 |
|---|---|---|
| Ultralytics Platform Free | $0/月，注册送一次性额度（$5；工作邮箱验证 $25） | **仍是 AGPL-3.0** |
| Ultralytics Platform Pro | **$29/席位/月**（年付省 17%），含 $30/席位/月算力额度 | **仍是 AGPL-3.0**，不含企业授权 |
| Ultralytics Platform Enterprise | Custom（询价） | 唯一包含企业授权的档位 |
| 平台云 GPU（官方明码） | **$0.24–$7.39/小时**：RTX 3090 $0.46、RTX 4090 $0.69、A100 PCIe $1.39、H100 PCIe $2.89、B200 $5.89 | 26 种 GPU，按小时计 |
| Roboflow 标注外包 | **拉框 $0.10/个、多边形 $0.20/个、分类/关键点 $0.05/个** | 海外明码价，可作预算上限锚点 |
| Roboflow 平台 | Free（15 credits/月、2 席位）；Core $99/月 或 $79/月（年付） | Enterprise 询价 |
| CVAT 标注平台 | 云版 Free / Solo $33月（年付 $23）/ Team $33每人月（年付 $23）；**自托管企业版起步 $12,000/年** | 社区版开源自建免费 |

### 6.3 算力：基本不是成本项

- **训练**：1000–3000 张的分割任务、n/s 规模模型、100–300 epochs，单卡几小时量级。按 4090 $0.69/小时算，训练总花费通常在**几十美元以内**。
- **国内更便宜**：第三方横评给出的 AutoDL 参考价 4090 ≈ **¥2.68/小时**、A100 ≈ **¥6.68/小时**（来源为 CSDN 博客，价格随时变动，以平台实际为准）。
- **推理**：YOLO26n 的 CPU ONNX 推理比 YOLO11n 快最多 43%，**可以只上 CPU 服务器或边缘盒子**，不必为推理买 GPU。
- 团队若已有 16GB 级显卡（3090/4090/A10），训练阶段零额外支出。

### 6.4 标注：真正的成本大头（示例测算）

以 3000 张图、每张 2 条曲线、按多边形标注为例：

- 按 Roboflow 明码 $0.20/多边形 → 6000 个 × $0.20 ≈ **$1,200（约 ¥8,600）**
- 国内外包一般低于此价，但需实际询价；公开渠道只能查到「专家级标注师月薪 1.5 万–5 万」这类人力描述，没有可靠的单价表。
- 自建标注（CVAT 社区版 + 自己人）= 0 现金支出 + 人工时间。

### 6.5 两条路线的总账

| | 路线 A：采购 Ultralytics 企业授权 | 路线 B：Apache-2.0 / MIT 替代 |
|---|---|---|
| 授权费 | 询价（不透明） | **0** |
| 平台/工具 | 可用免费档，或 Pro $29/席位/月 | 自建 CVAT / Label Studio，0 |
| 工程改造 | 无 | **2–4 人周**（RT-DETR/YOLOX 只有检测任务，需要分割时得换 MMDetection / PaddleDetection 生态） |
| 标注 | 同 | 同 |
| 算力 | 同（很小） | 同（很小） |

**判断**：授权费与工程时间是两个可以互换的成本项，标注和算力在两条路线上几乎相同。**在拿到 Ultralytics 报价之前，不要按「AGPL 能白嫖」做预算**——官方口径明确内部业务工具也需要授权，等集成进公司系统后再补授权，议价空间只会更小。

---

## 七、待确认的输入（决定选型走向）

1. 曲线是**画面里的曲线状物体**，还是**图表/仪表曲线要读数**？（后者建议换取数工具路线）
2. 最终交付物是**框 / 掩膜 / 参数化曲线 / 具体数值**？
3. 成果是否商用、能否接受 AGPL 或采购企业授权？
4. 训练 GPU 条件与可投入的标注人力各是多少？

---

## 附：参考来源

- Ultralytics 模型总览 https://docs.ultralytics.com/models/
- YOLO26 文档 https://docs.ultralytics.com/models/yolo26/
- 语义分割任务 https://docs.ultralytics.com/tasks/semantic/
- 旋转框任务 https://docs.ultralytics.com/tasks/obb/
- 跟踪模式 https://docs.ultralytics.com/modes/track/
- 训练模式 https://docs.ultralytics.com/modes/train/
- 许可说明 https://www.ultralytics.com/license
- 定价（含云 GPU 单价） https://www.ultralytics.com/pricing
- Roboflow 定价与标注服务价 https://roboflow.com/pricing
- CVAT 定价 https://www.cvat.ai/pricing
- RT-DETR https://github.com/lyuwenyu/RT-DETR
- YOLOX https://github.com/Megvii-BaseDetection/YOLOX
- YOLOP https://github.com/hustvl/YOLOP
