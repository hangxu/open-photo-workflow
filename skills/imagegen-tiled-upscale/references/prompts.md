# 准确图块与共享风格

先确定整集风格和本图操作，逐块只补实际可见的差异。提示词无需复述全部项目规则；不能把不可辨认的脸、下颌或衣料细节描述成已知内容。下面的保留要求用于减少漂移，不是逐像素一致性承诺，也不把已约定可接受的轻微瑕疵变成返工硬门槛。

## 默认单输入

```text
Edit only this exact photo crop. Improve natural photographic clarity and existing visible detail while preserving the established color and lighting.
This crop shows [仅描述实际可见的部位与物体].
Keep the same crop boundaries, scale, perspective, positions, expressions, proportions and edge intersections. Do not zoom out, complete cut-off people, add content, recompose, or apply unrequested beautification. Preserve identity and natural texture. Do not invent unreadable lettering or hidden features.
Return this crop only, with the same aspect ratio.
```

下半身块可以补：`Heads and faces are outside this crop; keep them outside.` 不要为无可见脸的图块写“改善所有人的脸”。需要轮廓改变时回到整图方案，不让四块分别塑形。

## 必要时追加上下文

```text
Image 1 is the ONLY edit target. Image 2 is context only.
Return Image 1 edge to edge, with its original blank space and cut-off content. Do not return Image 2 or its wider framing.
[当前图块可见内容与改进目标]
```

若返回全景，首选单输入作至多一次有原因的重试，不无限叠加否定词。

## 工具桥接

根据当前可用工具 schema 调用，不添加未公开的 `size/model/quality` 参数。准确本地图在编辑前先查看，所有目标有本地路径时用 `referenced_image_paths`；不要同时传 `num_last_images_to_include`。保存工具实际返回的原生文件，不把再编码副本冒充原生。脚本与普通 `workflow run` 不会自己调用内置 imagegen。

每块记录输入路径/版本、输入张数、实际提示词、尝试次数、结果尺寸及已披露用量。完整一张通常四次生成；若已有母版无需再生成，若新建整图几何母版则加一次。不要以提示词中请求的分辨率或型号代替实际结果。
