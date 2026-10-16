# 来源与许可 / Sources and licenses

本项目分享工作流和参考实现，项目自身代码采用 MIT。以下工具、模型和参考资料有各自的许可；本仓库附有下述公开照片演示，不分发模型权重、ICC 文件或这些工具的可执行程序。

This project shares a workflow and reference implementation under MIT. The tools, models, and references below retain their own terms. Public photo demonstrations are listed below. Model weights, ICC files, and external executables are not bundled.

- [RawTherapee](https://github.com/RawTherapee/RawTherapee)：RAW 显影引擎，单独安装。/ RAW engine, installed separately.
- [rawpy](https://github.com/letmaik/rawpy)、[Pillow](https://github.com/python-pillow/Pillow)、[NumPy](https://github.com/numpy/numpy)、[OpenCV](https://github.com/opencv/opencv)：通过包管理器安装的运行依赖，各自许可见上游。/ Runtime dependencies installed through the package manager; see their upstream licenses.
- [Saafke/FSRCNN_Tensorflow](https://github.com/Saafke/FSRCNN_Tensorflow)：可选 FSRCNN 权重来源，上游仓库采用 [Apache-2.0](https://github.com/Saafke/FSRCNN_Tensorflow/blob/master/LICENSE)。本项目不附带权重。/ Source for optional FSRCNN weights; upstream uses Apache-2.0. We do not bundle the weights.
- [xuanyidesign/codex-image-optimize-4k-upscale](https://github.com/xuanyidesign/codex-image-optimize-4k-upscale)：四块生成、配准与频段融合研究的参考项目。当前流程按摄影需求作了调整，并不保证实现与上游一致。保留上游 MIT 许可如下。/ Reference for four-tile generation, alignment, and frequency-based blending. This workflow adapts the approach for photographic use; behavior is not claimed to match upstream. Its MIT notice is preserved below.
- [op7418/Humanizer-zh](https://github.com/op7418/Humanizer-zh)：中文文档表达的参考，未捆绑其 SKILL 或代码。/ A writing reference for the Chinese documentation; its skill and code are not bundled.

## codex-image-optimize-4k-upscale — MIT notice

MIT License

Copyright (c) 2026 xuanyidesign

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## 公开演示照片 / Public photo demonstrations

风景原片由 Bonnie Moreland 拍摄，人像原片由 Wilfredor 拍摄，两者均采用 CC0 1.0。仓库展示其 AI 处理结果与对照。来源、作者及许可链接见 [演示照片说明](docs/demo-photos.md)。

The landscape photograph is by Bonnie Moreland and the portrait by Wilfredor, both under CC0 1.0. This repository includes AI-edited results and comparisons. See [demo photo sources](docs/demo-photos.md) for attribution and license links.
