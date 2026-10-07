# 可选参考实现的计划与验收

本文描述 `reference-code/auto_photo` 使用的 JSON 字段，供阅读或借用代码时参考。使用主技能不必创建这些文件，也不必按路线字母执行。

schema 2；复制请求中的批次 ID、照片 ID 和 `source_revision`，覆盖所有成功准备的照片。源、产物和计划以版本绑定，不添加未定义字段或内容摘要。

## 集级目标和校准

`collection_strategy` 支持 `intent/style_notes/representative_ids/review_policy/generation_budget`，以及：

```json
{
  "calibration": "direct",
  "delivery": {"viewing_long_edge": 2560, "tolerance": "minor", "purpose": "screen"}
}
```

这是 `collection_strategy` 内部片段。`viewing_long_edge` 是验收时的查看尺度，不缩小最终输出；屏幕默认 2560，可按用途改。`purpose` 为 `screen|print`，印刷仍应在 intent 说明实际印幅并作相应用途检查，2560 屏幕查看不证明印刷合格。`tolerance` 为 `minor|strict`。

熟悉的小批选 `calibration:direct`，直接保存计划，首张兼作样片；新风格或高风险操作选 `sample`，用草稿先验证少量典型和边界样片，必要时一次组级校准后冻结。任何模式中合格样片都复用，不为流程重做。只看代表图不能冒充已看全量。

先定义协调的肤色、亮度、反差与主体/背景关系，再分配路线。允许逐图参数不同以获得协调观感；一个固定曝光补偿不适合所有图，不以轻微提亮冒充商业摄影效果。

## 共享风格和逐图操作

`styles` 为风格 ID → `{preset, adjustments, prompt?}`，RAW 还可在 style/photo 顶层设 `white_balance_kelvin` 与 `wb_green`。每图必填 `id/source_revision/category/people/visible_faces/route/risks/needs_review`，可选 `scene_id/style_id/adjustments/retouch/protect_regions/generation/subject_lighting/white_balance_kelvin/wb_green`。`category` 为 `portrait/group/still_life/landscape`，人数与可见脸数不控制美化方式/超分。`scene_id` 需视觉确认同场景光线，不能只按时间。

| 路线 | 约束与用途 |
| --- | --- |
| C | `retouch:none`、`generation:null`；按引擎能力改光色，保留身份和几何，不磨皮瘦身 |
| A | 通常整图生成修饰或所需光影效果，prompt 写真实目标；也兼容 tiled 方法 |
| B | 生成后回贴 `protect_regions`，仅为交付中重要的保护目标增加复杂度 |
| T | 实验分块，`method:tiled`；目标查看尺度上需有预期可见收益与至少四次调用预算 |

`retouch` 为 `none/natural/portrait_roles/group_realistic`，名称不引入性别/人数规则。生成配置包含 `prompt`、`method:whole|tiled`、`scale:1|2|4`、`sharpen:boolean`、`max_attempts:1|2` 和 `upgrade_reason`。默认不超分、不锐化；max_attempts 约束每个整图或单块目标的尝试，额外一次要有具体原因，不自动消耗到上限。所有 tiled 方法（含 A/B）的 upgrade_reason 写出目标尺度下为什么整图不足，不能只写“像素更多”。generation_budget 不能低于当前计划基线（whole 每图 1，tiled 每图 4）；省略时上限即基线，返工须留出余量。max_attempts 按目标在整批生命周期计，不随 revise 清零。共享风格提示与逐图操作合成一次。B 使用 tiled 时仍在融合/后处理后回贴；已有原生结果可 reused 导入，预算声明基线不等于实际已登记消耗。

这些是本地路线与预算字段，不是内置 imagegen 的型号/尺寸参数。发送每次生成前须登记预算，失败或返回未知也算已发出的尝试；登记不代表图像服务后台已开始或已完成。

C 预设 neutral/warm_indoor/clean_portrait/landscape。adjustments：exposure_ev [-2,2]、temperature/tint [-0.15,0.15]、contrast [-0.3,0.3]、saturation [0.5,1.5]、highlights/shadows [-1,1]。相对温度不是 Kelvin；RAW 专用绝对白平衡为 white_balance_kelvin 2000–15000，同层可加 wb_green 0.5–2；JPG/PNG 使用相对调整。photo-only subject_lighting 为最多八个 `{bbox:[x1,y1,x2,y2],ev:-1..1}`，归一坐标的羽化椭圆曝光，不是分割/物理重布光。RAW 的整体光色在显影中完成，椭圆局部用临时 8-bit 无损显影图处理后一次编码最终 JPG。记录有效配方，逐图参数按真实光线变化。

B 区域结构为 `{id,kind,polygon,roi?,exclude?,target_exclude?}`，`kind:text|watch|logo|document`。坐标按归正输入归一到 `[0,1]`，至少三点且不自交；exclude/target_exclude 分别为源侧/生成侧遮挡。生成后按实际新边界复核；粗框不等于分割，源模糊字不能补造后声称准确。

## 正式验收与轻瑕疵

每张生成结果按目标查看尺度至少批量对照一次：先判断主体自然、肤色与亮度协调、光影有层次且整体比原片更适合交付。显著问题才追加必要局部，不默认对每图逐像素找差异，也不要求父代理重复整批看图。

- `severity:none`：达到目标且无已发现需要记录的问题，可通过。
- `severity:minor`：目标尺度自然好看，只有轻微纹理重绘、非关键背景差异或放大才见的弱接缝；tolerance 为 minor 时可以 `passed:true`，备注并正常打包，不安排返工。
- `severity:major`：身份明显变化、缺人、严重肢体/构图问题，或承担交付信息的姓名、日期、明确要求准确的商拍文字错误；拒绝通过。`strict` 下轻瑕疵也不能批准。

非主体包装/背景小字先看用途和正常观看影响：既不一票否决，也不预判必为轻微。关键字与非关键纹理分开判断，字符不完全一致本身不能决定整张照片是否应废弃。尚未看过的内容不能预填通过。

审核使用当前版本，可批量提交：

```json
[{
  "id":"真实照片ID", "final_revision":"当前产物版本", "passed":true,
  "severity":"minor", "viewing_long_edge":2560,
  "issues":["非关键背景纹理有轻微重绘，目标查看尺度未影响主体观感"],
  "notes":"已按目标尺度对照整体，肤色、亮度与主体关系可接受；异常局部已补看。",
  "reviewer":"实际检查者"
}]
```

批准只绑定本版本；新版本必须复核。`major` 不可通过，minor 在 strict 下不可通过；旧审核缺这些可选字段仍兼容，但新验收应写清真实尺度与瑕疵。

C 的代表批准只传播到同场景、同风格、有效参数完全一致、数值正常且无待审标记的图；参数不同可以批量视觉审核，但不伪称满足传播条件。数值光色异常提示本图单独复核，不直接证明不好看。B/T 是否产生可审候选由实际程序门控决定；不能手改状态绕过硬失败。

## 成本和证据

规划/复核 LLM、imagegen、本地处理与 Agent 调度分别计数。四块至少四次，新整图母版再加一次；失败/未知结果不从计数中抹去。页数由可辨认程度决定，一次逻辑规划不等于一次调用。样片覆盖率、几何母版后分块收益仍需对应场景实测，不借历史样片替本批背书。
