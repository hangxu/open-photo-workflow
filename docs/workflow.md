# 可选参考实现：照片集工作流

本文件说明 `reference-code/auto_photo` 的实现细节，供阅读、改写或按需借用。技能的主体是 Agent 工作流，使用技能不要求运行这份代码，不要求安装 Python、auto_photo 或 RawTherapee。独立流程见 [photo-workflow](../skills/photo-workflow/SKILL.md)。

以下命令示例从仓库 `reference-code/` 目录运行，输入输出的相对路径也以该目录为基准。所需库和外部工具由使用者按功能准备；本文不是安装或测试指南。

```sh
cd /path/to/open-photo-workflow/reference-code
```

参考 CLI `python -m auto_photo workflow` 处理 JPG/JPEG/PNG 和受引擎支持的 RAW，包括索尼 ARW。支持扩展名由代码决定，实际机型与压缩模式仍需按输入判断。输入、旧交付只读；schema 2 批次用 `state.json` 保存来源、计划、版本、调用和复核，历史 schema 1 不原地迁移。这些是本实现的约定，不要求其他工具照搬。

本地 CLI 负责准备、显影、调色、后处理和记录，不调用宿主内置 imagegen。生成步骤由 Agent 通过宿主工具完成，再把原生结果导入。使用 Pro 登录时，这些调用走账户共享用量，本项目无需 API key；工具可用性和限额以宿主为准，不承诺无限调用、固定模型或原生输出分辨率。用户自行接入的外部规划器另按其服务配置。

## 以交片效果决定工作量

先看整集，确定用途、摄影目标、查看尺度和瑕疵容忍度，再协调整集的肤色、亮度、反差和主体/背景关系。基础图优先选原生长边至少 3840 的同拍 JPEG 或 RAW 内嵌 JPEG，必要时才重显影；随后选择本地调色或一次整图生成，按需超分，批量验收并交付静态相册或成片包。默认屏幕验收长边 2560，可配置；这是查看尺度，不是输出缩图。minor 模式接纳正常观看自然好看的轻微纹理/背景瑕疵，strict 更严。印刷另需按实际印幅检查，2560 屏幕查看不证明印刷合格。

用全量编号预览了解题材与光线，按可辨认容量分页，共享集级摘要。模型必须实际看图片；路径、时间戳、检测框不能冒充视觉观察。一次逻辑规划可以有多次调用，不把几百张压成无法看清的一图。只看代表图时仍是初步方案，冻结前完成全量归组。

审美风格与路线分开；允许逐图参数变化来获得协调观感，不把相同参数当作唯一目标，也不以“提亮了一点”冒充完成修图。可见但模糊的细节允许授权的自然增强；遮挡、画外或身份不明的部分不补造，不按性别、人数或含脸与否强制生成。

| 路线 | 执行与用途 |
| --- | --- |
| C | RAW 中完成显影光色，或 JPG/PNG 本地光色与受限主体提亮，输出全尺寸 JPG；不磨皮瘦身 |
| A | 整图生成，导入原生图后按需超分/锐化；适合实际需要的修饰或光影效果 |
| B | A 后最后回贴重要保护区；不是把所有小字都升级为保护工程 |
| T | 四块生成后本地配准融合；必须说明目标查看尺度上的可见收益，并有至少四次调用预算 |

整图已满足观看用途时不升级四块；跨块分别塑形会破坏几何。先整图几何母版再切块是额外一次调用的实验，不默认增加步骤。

## 预览优先，RAW 渲染后置

```sh
python -m auto_photo workflow prepare "照片目录" \
  --output output/session-01 --brief "准确编辑要求与交付用途"
python -m auto_photo workflow requests output/session-01 --max-photos 8
```

默认只扫描直接子文件，`--recursive` 才递归。发现同目录同 stem、唯一 RAW/JPG 且已知拍摄元数据不冲突时，将它们作为候选同拍归组：RAW 是正式来源，JPG 优先预览。缺失元数据不构成同拍证明；`--separate-pairs` 可分开处理。显式传单个 RAW/JPG 也可能使用其同名配对；需要只按指定文件处理时同样加此参数。其他同名文件保留并用顺序编号区分，无内容去重。

prepare 默认只做长边 1280 的规划预览：优先配对 JPG，再 RAW 嵌入预览，最后才小尺寸显影。记录 preview_kind/preview_source 和尺寸依据，不把预览冒充正式输入。RAW 全尺寸处理延迟到 run：C 将配方交给 RAW 引擎直接形成成片；新批次 A/B/T 首次需要生成时按同拍JPEG→原生长边≥3840的内嵌JPEG→RAW显影回退准备完整输入；规划小图仍与正式输入分开。旧批次无策略字段则保持旧显影方式，显式 raw_profile 也保留指定显影。RAW 显影可用 `--raw-profile`、`--rawtherapee-cli` 指定实际引擎配置。

`--preview-edge` 只改变规划预览，`--no-detect` 关闭候选检测。prepare 不生成相册或规划请求副本；显式 requests 才写 `planning_exports/request-*.json`。分页当前支持 1–32、默认 8，这是程序容量，不是模型图片/token 限额。图像处理按 EXIF 归正、尊重 ICC 转 sRGB；无 ICC RGB 明确按 sRGB 假设。

## 直接处理或代表样片

熟悉小批用 `calibration:direct`，首张可兼样片与成片；不强制草稿仪式：

```sh
python -m auto_photo workflow plan output/session-01 --response plan.json
python -m auto_photo workflow run output/session-01
```

新风格或高风险用 `calibration:sample`，按风格、操作和风险选少量典型及边界样片：

```sh
python -m auto_photo workflow plan output/session-01 --response plan.json --draft
python -m auto_photo workflow run output/session-01 --ids SAMPLE_ID_1 SAMPLE_ID_2
# 完成生成桥接及实际样片复核，通过后再冻结
python -m auto_photo workflow freeze output/session-01
python -m auto_photo workflow run output/session-01
```

calibration 记录选择，是否为草稿由 CLI 的 `--draft` 控制；不加它则保存后直接冻结。草稿只允许显式选择部分 ID 运行。样片通过不要求再修订；需要校准时最多一轮组级调整。冻结计划修改须 `plan --revise`，需要再审样可同时加 `--draft`。相同计划冻结复用样片；只改变后处理而生成提示/方法未变时，复用原生图，新成片版本重新复核。

### 可选外部规划器

```sh
python -m auto_photo workflow plan output/session-01 \
  --command '["/absolute/python", "/absolute/vision_adapter.py"]'
```

显式 argv 接口，无默认服务商、无自动查找密钥、不调用内置 imagegen。适配器从 stdin 收请求，真正将预览作为图片输入，stdout 返回 plan 或 `{plan,usage,model}`；不经 shell。外部调用要求非空 brief。分页携带共享风格和摘要，已验证响应复用，失败或未知调用不静默再发。CLI 的 collection_overview 是路径/尺寸清单，不自动证明模型已看全量。

## schema 2 示例

替换真实批次/照片 ID、来源版本与看图结论；计划须覆盖所有成功准备的照片：

```json
{
  "schema_version": 2,
  "batch_id": "真实批次ID",
  "collection_strategy": {
    "intent": "交付自然暖调，主体明暗有层次，允许不影响正常观看的轻瑕疵",
    "style_notes": "组内肤色、亮度协调，按每张实际光线调整",
    "calibration": "direct",
    "representative_ids": ["photo-c"],
    "review_policy": "按目标尺度批量对照，显著异常补局部",
    "generation_budget": 1,
    "delivery": {"viewing_long_edge": 2560, "tolerance": "minor", "purpose": "screen"}
  },
  "styles": {
    "warm": {
      "preset": "warm_indoor",
      "adjustments": {"exposure_ev": 0.1, "highlights": -0.1, "shadows": 0.1},
      "prompt": "保持自然暖调与真实材质，主体和背景有清楚但自然的明暗关系。"
    }
  },
  "photos": [
    {
      "id": "photo-c", "source_revision": 1,
      "category": "portrait", "people": 1, "visible_faces": 1,
      "route": "C", "style_id": "warm", "scene_id": "room-1",
      "adjustments": {}, "retouch": "none", "risks": [],
      "protect_regions": [], "generation": null, "needs_review": false
    },
    {
      "id": "photo-a", "source_revision": 1,
      "category": "portrait", "people": 1, "visible_faces": 1,
      "route": "A", "style_id": "warm", "scene_id": "room-1",
      "adjustments": {}, "retouch": "natural", "risks": [],
      "protect_regions": [],
      "generation": {
        "method": "whole", "scale": 1, "sharpen": false, "max_attempts": 1,
        "prompt": "按授权目标温和修整可见皮肤，保留身份、年龄感、表情和轮廓。"
      },
      "needs_review": true
    }
  ]
}
```

`delivery.viewing_long_edge` 为正整数（1–100000），默认 2560；tolerance 为 minor/strict、默认 minor；purpose 为 screen/print、默认 screen。calibration 可选 direct/sample。generation_budget 为本批已登记生成交接上限，不能低于当前计划基线（method 为 whole 每张 1、tiled 每张 4）；省略时上限就是基线，返工需要显式留出余量。

每图必填 `id/source_revision/category/people/visible_faces/route/risks/needs_review`，可选字段见示例。category 为 portrait/group/still_life/landscape；retouch 为 none/natural/portrait_roles/group_realistic，名称不暗含性别/人数强制规则。C 必须 retouch none、generation null。scene_id 需视觉确认同场景光线。

生成字段：method whole/tiled、scale 1/2/4、sharpen 布尔、prompt、max_attempts 1/2（默认 1）、可选 upgrade_reason；所有新 tiled 方法计划都必须有升级理由，包括 A/B 路线兼容的 tiled 方法；B 分块后仍执行保护区回贴。max_attempts 按每个整图/单块目标计，整批生命周期内不因 revise 清零；不是自动尝试次数。scale/sharpen 是本地处理配置，不是内置 imagegen 的输出参数。共享 styles.prompt 与逐图提示合成一次。

### 光色与局部主体亮度

相机内嵌预览已经带有机内曲线、色彩和锐化，不能当作中性 RAW 显影的零参数结果。RAW 的正曝光补偿不保证成片比相机预览更亮。更换显影基底或引擎后，先用一张代表照片比较实际输出与相机预览的主体亮度、肤色和反差，再复用配方；已有样片即可，不增加逐图校准。验收须确认摄影目标得到改善，不能只凭尺寸正确、人物文字未变化就通过。允许轻微瑕疵不等于接受整体光色退步。

可选 `reference-code/auto_photo/profiles/rawtherapee/camera-matched.pp3`（prepare 时指定 `--raw-profile`）用 RawTherapee 的 HistogramMatching 从相机内嵌预览估计曲线，再对完整 RAW 像素显影；不是放大预览，也不保证逐像素复现相机色彩。从 `reference-code/` 运行时可用 `python -c 'from auto_photo.resources import rawtherapee_profile; print(rawtherapee_profile("camera-matched"))'` 取得实际路径。C 路线及显式显影的 neutral 基底保留，新批次生成路线默认机内JPEG优先。先用代表图选基底，相机预览的明暗取向本身不合适时仍可手动显影。

RawTherapee 5.13 匹配模式允许关闭自动曝光并保留手动曝光，但会重置 Contrast 和 Curve2。当前匹配模式配方因此支持曝光、饱和度、白平衡与人物局部提亮，不接受会被静默清零的 contrast/highlights/shadows；这些调整可在匹配显影后执行。相机降噪/锐化并不随曲线复制，高 ISO 样片需另选适度的 RAW 降噪参数。单张照片的降噪和人物参数不能作为全批默认值。

匹配行为依据：[RawTherapee 5.13 simpleprocess.cc](https://github.com/RawTherapee/RawTherapee/blob/5.13/rtengine/simpleprocess.cc#L857)。

预设 neutral/warm_indoor/clean_portrait/landscape。adjustments 支持 exposure_ev `[-2,2]`、temperature/tint `[-0.15,0.15]`、contrast `[-0.3,0.3]`、saturation `[0.5,1.5]`、highlights/shadows `[-1,1]`。饱和度为倍率，其余按引擎记录的映射处理；相对温度不是 Kelvin。RAW 优先在显影阶段处理白平衡、高光阴影与整体曝光；JPG 使用已有像素的受限光色变换，不恢复不存在的动态范围。

style 或 photo 顶层可指定 RAW 专用 `white_balance_kelvin:2000..15000`，同层可加 `wb_green:0.5..2`；wb_green 要求同层有 Kelvin。JPG/PNG 不接受绝对 Kelvin，使用相对 temperature/tint。

photo 顶层可有 `subject_lighting:[{bbox:[x1,y1,x2,y2],ev:0.2}]`，最多 8 个区域，bbox 归一到 `[0,1]` 且面积为正，ev `[-1,1]`。这是羽化椭圆亮度调整，不是人物分割、脸部识别或物理重布光。RAW 的整体曝光、白平衡及高光阴影先在显影中完成；若配置椭圆局部，再对临时 8-bit 无损 sRGB 显影图处理，最终只编码一次 JPG，不宣称 RAW 引擎原生实现这些局部蒙版。

### 基础图准备与 RAW 创意显影

`python -m auto_photo.raw_pipeline camera-base SOURCE.ARW NEW_OUTPUT [--preview-edge 1600]`：同拍JPEG→内嵌JPEG→RAW显影回退。相机JPEG按真实完整解码尺寸判断原生长边≥3840，不用RAW元数据尺寸替代；保持原比例，正式图不缩放。`--preview-edge`仅另存小规划图。只有必要时规范方向和色彩，无需时保留原JPEG字节，不重复降噪锐化。可用 `--no-paired-jpeg`禁用同拍查找、`--paired-jpeg`明确配对、`--min-edge`改门槛、`--force-raw`强制显影。默认保存final.jpg与record.json；RAW回退另留配置。当前rawpy只提供LibRaw选中的内嵌预览，未实现所有容器版本枚举。

该策略为camera-input-v2，已接入新批次A/B/T；旧批次恢复和显式raw_profile不切换政策。改艺术配方不使合格基础图失效。RAW回退仍采用匹配或自动曝光基底，没有完整的跨机型审美保证。机内JPEG降级原因、实际来源、色彩、方向、维度和耗时全部记录，无哈希。

`python -m auto_photo.raw_pipeline develop SOURCE.ARW control.json NEW_OUTPUT [--preview-edge 1600]`（兼容raw_control）接受模型看图后的结构化配方；此处preview-edge表示限制RAW渲染尺寸，省略则全尺寸。扩展控制尚未接入批次plan。C对RAW来源仍直接显影；需要轻调机内JPEG时，先camera-base，再将final.jpg作为JPEG输入执行C，明确来源变化。

摄影目标语言见 [摄影目标与提示词](../skills/photo-workflow/references/photographic-goals.md)，不按风格名称硬编码参数。RAW候选若不如机内图则标为未选用；选择JPEG调光不等于宣告纯RAW方案成功。

配方包含 `baseline:neutral|camera_matched|automatic`、已有 RAW `recipe`，可选 `tone_curve`、`gradient`、`denoise`、布尔型 `capture_sharpening`。automatic 不接受手动曝光、对比或高光阴影配方，以免自动计算覆盖声明的值；记录不虚构自动计算后的曝光值。`tone_curve.points` 是 2–10 个归一化输入/输出点，使用原生 FilmLike 曲线；只适用于 neutral，避免被匹配曲线覆盖。原生 `gradient` 用归正后的画面坐标：`side:top|bottom`、`exposure_ev`（正提亮/负压暗）、`center:[x,y]`、`feather_percent`（相对图像对角线）。中心转换为原生整数单位，分辨率为图像尺寸的 0.5%；羽化也按整数百分比执行。不同 EXIF 方向和顶部/底部渐变仍需按实际输出检查，不能把引擎定义当成跨机型实拍验证。

例如 `{"baseline":"camera_matched","recipe":{"adjustments":{"exposure_ev":0.2,"saturation":1.04}},"gradient":{"side":"top","exposure_ev":-0.35,"center":[0.5,0.3],"feather_percent":25}}`。全部调光在 RawTherapee 内部完成，直接编码 JPG；此入口拒绝会触发显影后 PNG 调光的 `subject_lighting`。原生渐变属于显影管线内的浮点 RGB 处理，不宣称它是传感器域曝光或精确人物分割。

控制记录区分模型要求、编译到引擎的参数和观察结果；初始 `observed_effects:null` 表示尚未验证，不把成功写入 PP3 当成效果已经达标。模型工作方式是看图后制定方案、低分辨率检查、最多一次有依据的调整、选定后全尺寸显影。只有用户要多个方向才增加候选；基础显影不需要逐张 LLM。

预览色彩也属于控制闭环：嵌入 ICC 优先；无 ICC 但 EXIF 明确 `ColorSpace=65535` 且 `InteropIndex=R03` 时，按 Adobe RGB 转为 sRGB。RAW 内嵌预览缺失色彩标签时继承 RAW 元数据。macOS 使用已安装的 AdobeRGB1998 ICC，其他主机可设 `AUTO_PHOTO_ADOBE_RGB_ICC`。不能把已知 Adobe RGB 的预览只贴上 sRGB 标签。

### 保护区规则

B 必须有 `protect_regions:[{id,kind,polygon,roi?,exclude?,target_exclude?}]`，kind 为 text/watch/logo/document；按归正输入归一坐标，至少三个不同点且不自交。exclude/target_exclude 分别保护源侧/生成侧遮挡关系，生成后复核真实边界。最后一次采样回贴，其后不全图超分锐化。重要姓名日期、明确要求准确的商拍信息要保护；非主体包装/背景小字按用途和正常观看影响判断，不一票否决也不预判为轻微。

## 生成桥接与尝试预算

run 只自动执行本地阶段。生成请求保存在 state 的 generation_request，Agent 每次发工具前登记：

```sh
python -m auto_photo workflow generation-begin output/session-01 PHOTO_ID
```

仅返回 `should_call:true` 才调用内置 imagegen；先查看返回工单的准确 input。`should_call:false` 表示已有待定交接，不重复发送。记录 attempt_id，原生图按原字节保存后导入：

```sh
python -m auto_photo workflow import-generated output/session-01 \
  PHOTO_ID /absolute/generated.png --attempt-id ATTEMPT_ID --metadata generation.json
python -m auto_photo workflow run output/session-01 \
  --cv-runtime /path/to/opencv-contrib --model-dir /path/to/fsrcnn-models
```

T 先 run 准备 TL/TR/BL/BR；每块 generation-begin/import-generated 都加 `--tile TL`（或其他块），完整四块后 run 融合。A/B 选择 tiled 方法时同样逐块登记/导入；B 在融合与后处理后回贴。新增整图母版另算一次生成。独立本地命令见 [分块执行说明](../skills/imagegen-tiled-upscale/references/execution.md)。

明确返工用 `generation-begin ... --retry-reason "具体失败原因"`；受目标 max_attempts 与批次 generation_budget 双重限制。失败、未知结果仍占预算；不能重复登记等待中的请求。登记只计交接，不证明服务端收费，也无法拦截绕过入口直接发出的调用。

复用历史原生图用 `import-generated ... --reused`，不先登记、不重生成，不与 attempt-id 同传。计划预算声明仍满足 whole 1/tiled 4 的基线，但这些 reused 导入实际新增 reservation 为 0，不能混淆声明与消耗。已有图替换加 --replace。无 attempt-id 且未标 reused 的导入是 untracked，不虚算为本轮零调用。metadata 只填真实披露的型号、用量、耗时，未知留空；复用历史不等于历史免费。

## 批量视觉验收

每张生成结果在目标查看尺度至少对照一次，先看自然、协调、层次和主体表现；显著异常才补必要局部。不重复要求父代理再看整批大图。数值提醒不是审美结论：C 数值异常要求本图复核；B 可生成带数值提醒的候选供视觉判断，但严重几何不足仍硬失败；T 缺配准、明显错裁切仍阻止发布。

- none：达到目标且无需要记录的问题，可通过。
- minor：正常观看自然好看，轻微纹理重绘、非关键背景差异、放大才见的弱接缝可在 minor 容忍度下通过并正常交付，不返工。
- major：身份明显变化、缺人、严重肢体/构图错误，或交付关键姓名日期/主体重要文字错误，拒绝通过。strict 不接受 minor。

提示词中的保留要求用于减少漂移，验收仍按用户用途、查看尺度和约定容忍度判断，不把每处差异都变成返工门槛。小字看用途、位置和目标尺度影响，不能仅凭字符有差异决定整图退回；也不能未看效果就预判包装文字是轻微。示例复核：

```json
[{
  "id":"真实照片ID", "final_revision":"当前最终图版本", "passed":true,
  "severity":"minor", "viewing_long_edge":2560,
  "issues":["非关键背景纹理轻微重绘，目标尺度不影响主体观感"],
  "notes":"已按目标尺度对照整体，并补看实际发现的异常局部。", "reviewer":"实际检查者"
}]
```

```sh
python -m auto_photo workflow review output/session-01 --decisions reviews.json
```

每项必填 id/final_revision/passed/notes，其余可选；新审核应写清 severity、尺度和已知 issues。major 不可通过，minor 在 strict 下不可通过；旧缺字段记录仍兼容。批准绑定当前版本，当前查看要求提高或容忍度收紧后需要满足新要求，不能沿用较弱结论。

C 的代表批准只传播到同场景、同风格、有效参数完全一致、数值正常且无待审标记的图；参数可以逐图变化，但这些图须批量视觉复核，不能伪称满足同参数传播。传播记录引用双方版本，代表图否决会撤销传播。

## 交付、指标与恢复

```sh
python -m auto_photo workflow package output/session-01
python -m auto_photo workflow report output/session-01
python -m auto_photo workflow metrics output/session-01
```

package 立即选择当前已审 JPG（包括通过的 minor）写入 `delivery/final-jpg.zip`，包内 DELIVERY.json 列省略项。未完成图不阻塞交付，不等任务、不查其他聊天、不先重做验收。重复打包使用 final-jpg-002.zip 等新名，显式 --output 要求目标不存在；无可交付图明确报错。ZIP 仅有格式自带 CRC，无额外哈希/全包回读。

report 只写静态 index.html，显示固定原图/当前结果、目标尺度与已接受问题，不轮询或自动刷新。RAW 未正式显影时原图侧使用如实标明来源的预览。普通 prepare/plan/run/import/review 不重写相册；package --gallery 在 ZIP 完成后生成一次相册，相册失败不撤回 ZIP。

metrics 只读状态、输出计数不生成文件：photos、input_files、approved、approved_with_minor_issues、rejected、generation_reservations、untracked_imports、first_final_seconds、first_approval_seconds。reservations 是登记次数，不是实测计费，未知 token_usage 为 null；LLM 规划/复核、imagegen 与 Agent 调度也不能混称为一次调用。

```sh
python -m auto_photo workflow stop output/session-01
python -m auto_photo workflow resume output/session-01
python -m auto_photo workflow cleanup output/session-01
```

本地临时处理后原子发布，恢复复用已提交产物。stop 禁止后续本地任务，不撤回服务端调用；未知请求先核对返回，不自动重发。cleanup 只清理无活动发布依赖的 `.work`，保留原片、当前交付、原生生成及恢复依赖，不手删 state 修复流程。

内部零内容/路径/参数哈希，以显式版本、文件元数据和直接配方比较复用；不能检测保留元数据的外部字节变化，来源替换需新批次。默认长期保留原片引用、预览、必要正式输入、昂贵原生结果、实际成片版本和状态；后处理临时多格式副本及时回收，诊断按需启用。

历史实验不替代本批验收。集级样片覆盖率、几何母版加分块的收益和跨机型兼容性仍需实测；目标无法达成时说明未完成，不把普通调色冒充已完成美化。
