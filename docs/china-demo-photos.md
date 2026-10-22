# 中国公开样片 / China photo demonstrations

2026-10-22：两张原片的 Wikimedia Commons 元数据均标明 CC0 1.0。以下对照为原片与 AI 处理成片；输入未另行压暗、模糊或调色。

Both source photographs are marked CC0 1.0 in Wikimedia Commons metadata, retrieved on 2026-10-22. Comparisons show the original input and AI-edited result; inputs were not darkened, blurred, or recolored.

| 示例 / Example | 原片 / Source | 作者 / Author | 许可 / License |
| --- | --- | --- | --- |
| 桂林木龙湖 / Mulonghu Park, Guilin | [Mulonghupark Guilin Guangxi](https://commons.wikimedia.org/wiki/File:Mulonghupark_Guilin_Guangxi.jpg) | Chlukoe | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| 北京春节人像 / Beijing New Year portrait | [Girl in New Year Beijing](https://commons.wikimedia.org/wiki/File:Girl_in_New_Year_Beijing.jpg) | benniaotang | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |

| 示例 / Example | 输入 / Input | 四块合并 / Merged | FSRCNN 2× |
| --- | --- | --- | --- |
| 桂林 / Guilin | 5616×3744 | 2742×1828 | 5484×3656 |
| 北京 / Beijing | 3840×5760 | 1828×2743 | 3656×5486 |

风景采用青绿旅拍目标：提亮植被中间调，分离山石层次，青绿湖水与温暖木色。人像采用暖调纪实目标：改善蓝色偏色，保留孩子的神态、视线、姿势与遮住嘴的袖口。

The landscape uses a jade-green travel-editorial grade. The portrait uses a warm documentary grade, retaining the child's expression, gaze, pose, and sleeve covering the mouth.

每张四次内置 image_gen 调用，无重试。2×2 图块横纵重叠均为整幅对应尺寸的 12%，采用 SIFT/ECC 配准、色光匹配和余弦融合，合并后 FSRCNN 2×，最后一次轻锐化（1.2 / 80 / 2）。

Four built-in image_gen calls per photograph, no retries. Each axis overlaps by 12% of the full image dimension. SIFT/ECC registration, color matching and cosine blending are followed by FSRCNN 2× and light sharpening (1.2 / 80 / 2).

[完整提示词与实际尺寸 / Full prompts and dimensions](china-demo-prompts.json)
