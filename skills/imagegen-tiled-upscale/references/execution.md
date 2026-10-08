# 可选参考代码：本地分块处理

仓库 `reference-code/auto_photo/tiled.py` 提供准备、配准融合和 FSRCNN 2× 超分的参考实现；技能的 `scripts/tiled_upscale.py` 是可选包装入口。两者都不调用 imagegen。使用分块技能本身不要求运行这些脚本，也不要求先安装 Python 或 RAW 引擎。

如需借用代码，以下示例从仓库的 `reference-code/` 目录运行，相对输入输出路径也以此为基准。脚本用到 Pillow、NumPy、OpenCV 和超分权重，环境由使用者按需准备，这里不提供安装流程。`--cv-runtime` 指含 `cv2` 的目录，`--model` 指模型文件；当前环境已有合适 OpenCV 时可省略 `--cv-runtime`。

```sh
cd /path/to/open-photo-workflow/reference-code
```

包装入口会查找仓库内的 `reference-code/auto_photo`，也兼容已可导入的包。`--project`、`AUTO_PHOTO_ROOT` 或 `AUTO_PHOTO_PROJECT` 可以指向仓库根或 `reference-code`。以下 `SKILL/SOURCE/RUN` 均替换为实际路径；这些代码约定不是技能的必需步骤。

## 升级判断与准备

先记录目标查看尺度（屏幕默认长边 2560，可配置且不缩输出）、`tolerance:minor|strict`、`purpose:screen|print`，说明在这个尺度上四块比整图预期改善什么；至少留出四次生成预算。熟悉小批首张兼样片，新场景先试一张。

```sh
python SKILL/scripts/tiled_upscale.py prepare SOURCE --output RUN/prepared
```

输出 `prepared/prepare.json` 和 `prepared/inputs/TL.png`、`TR.png`、`BL.png`、`BR.png`。RAW 必须先显影；输入归正并转 sRGB，无 ICC 的 RGB 按 sRGB 解释。记录实际边界、尺寸、路径、文件大小及修改时间，不生成哈希。

纳入 workflow T 时，每次发出生成前用其 generation-begin 登记尝试；独立脚本没有预算拦截，Agent 必须另记相同调用计数。默认每目标一次，明确 max_attempts 为 2 才能有原因地再试一次。Agent 查看准确图块后调用内置 imagegen 或用户明确选择的图片编辑工具，默认单输入；外部工具接入见 [说明](https://github.com/hangxu/open-photo-workflow/blob/main/docs/image-generation-tools.md)。将原生返回保存在 `RUN/native`，记录块 ID、输入版本与张数、完整提示词、尝试号、结果尺寸、状态及实际披露用量。失败同样计数，未知型号/token 为 `null`。脚本不会生成、等待其他代理或接收迟到结果。

## 合并

```sh
python SKILL/scripts/tiled_upscale.py merge \
  --prepared RUN/prepared/prepare.json \
  --tl RUN/native/TL.png --tr RUN/native/TR.png \
  --bl RUN/native/BL.png --br RUN/native/BR.png \
  --output RUN/merged --cv-runtime /path/to/opencv-contrib
```

默认输出 `merged/native-merged.png` 和 `merge.json`；`--diagnostics` 才保留额外配准/未校色图。四块齐全才执行；以 SIFT 检查返回范围，再按受限 ECC 仿射与重叠色光匹配结果决定是否采用改进。默认角点门槛为参考对角线的 2.5%，仅为保守启发式，不为放行错误全景而调宽。

共同采样倍率取各块返回宽高相对源块的最小比例，按原图比例合并，允许整数舍入。极小边缘缺口可用同坐标输入像素补齐并记录比例；不使用反射扩展画布。大局部非线性变形不能靠现有配准保证修复，明显接缝转人工判断或停止。

## 按需超分

```sh
python SKILL/scripts/tiled_upscale.py upscale RUN/merged/native-merged.png \
  --output RUN/upscaled --cv-runtime /path/to/opencv-contrib \
  --model /path/to/FSRCNN_x2.pb
```

输出 `final.png`、`final.jpg` 和 `upscale.json`；PNG 为恢复/后处理所需资产，交付通常只取 JPG。此子命令执行原生 2×，例如 2400×1600 输入得到 4800×3200；不先插值到固定 4K。内部有上下文的小块计算仅控制本地内存，不再次调用 imagegen。需要轻锐化才加 `--sharpen`，一次 USM（1.2/80/2，最终像素尺度）；额外诊断仅 `--diagnostics` 开启。

## 复用、验收、交付

schema 2 完成记录以直接配方参数及文件元数据复用；同路径未完成的 schema 2 阶段可再次执行。已完成阶段更换参数，或目录只有历史格式记录时，使用新目录，不覆盖历史结果。元数据不证明字节相同；来源被替换或版本不明时重新准备。

每张最终图至少在目标查看尺度批量视觉对照一次，显著异常才补原尺寸必要局部。minor 模式中自然好看且只有轻微纹理/非关键背景差异，可以标 minor、passed:true 并正常交付；critical 主体、身份或交付重要文字错误标 major 拒绝，strict 则不接受 minor。非主体小字依用途与正常观看影响判断，不一票否决。数值报告不能认证身份、细字或纹理，程序硬失败也不能手改记录绕过。保留昂贵原生块和当前合并结果，其他廉价临时物在不影响恢复/相册时清理。不要把失焦图上的生成字句当真实恢复。

此脚本不内置相册或打包。纳入 `photo-workflow` 批次时使用其显式 `report/package`；独立运行则只对当前明确通过的最终 JPG 生成静态相册或压缩包，不检查其他任务、不等待未完图、不混入整个过程目录。比较方案时可读 [方法限制](lessons.md)，不能替代本批复核。
