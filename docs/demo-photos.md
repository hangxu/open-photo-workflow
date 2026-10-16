# Public demonstration photographs / 公开演示照片

Both source photographs are released under **CC0 1.0 Universal**. The comparisons show AI-edited derivatives made with the four-tile workflow.

| Example | Original work | Photographer | License |
| --- | --- | --- | --- |
| Landscape | [Lake Mountain Landscape](https://commons.wikimedia.org/wiki/File:Lake_Mountain_Landscape.jpg) | Bonnie Moreland, via ISO Republic / Wikimedia Commons | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| Portrait | [Woman in a headwrap in Quebec City](https://commons.wikimedia.org/wiki/File:Woman_in_a_headwrap_in_Quebec_City.jpg) | Wilfredor, via Wikimedia Commons | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |

The landscape input is the original 5184 × 3456 JPEG. The portrait input is Wikimedia's 3840 × 2560 rendition of the 8256 × 5504 original. The input images were not darkened, blurred or recolored for the comparison.

风景示例：去除紫灰偏色，提亮森林中间调，强调翡翠绿植被、清透湖水和暖白高光。

人像示例：暖铜色肤质、柔和高光、局部肤质修饰和黑色头巾质感；保留侧脸轮廓及闭眼表情。

Processing: built-in image_gen, four independent crop edits per photo, 12% overlap measured against the full image width/height, local registration and blending, then FSRCNN 2× and one light unsharp mask. Full prompts and actual dimensions are in [demo-prompts.json](demo-prompts.json).

Source licensing metadata was retrieved from Wikimedia Commons on 2026-10-16 and retained locally in each example's source-license.json.
