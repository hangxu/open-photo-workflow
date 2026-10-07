# 可选参考代码：照片集执行

本文只说明仓库 `reference-code/auto_photo` 的一种实现。使用 `photo-workflow` 技能不要求运行这份代码，也不要求先配置 Python 或 RAW 引擎；Agent 可以直接用宿主已有工具完成主文工作流。这里的路线字母、状态文件和命令是参考实现约定。

如需借用代码，以下示例从仓库的 `reference-code/` 目录运行，输入、输出相对路径也以该目录为基准。所需 Python 库与外部工具由使用者按实际功能准备，本文不提供安装流程。字段细节见仓库 `docs/workflow.md` 和代码。

```sh
cd /path/to/open-photo-workflow/reference-code
```

可选的 `scripts/build_gallery.py` 包装入口会查找仓库中的 `reference-code/auto_photo`，也兼容已可导入的包。`--project`、`AUTO_PHOTO_ROOT` 或 `AUTO_PHOTO_PROJECT` 可以指向仓库根或 `reference-code`。这些只是运行参考脚本时的定位方式。

## 预览优先与校准

```sh
python -m auto_photo workflow prepare INPUT --output output/BATCH --brief "准确编辑要求及交付用途"
python -m auto_photo workflow requests output/BATCH
```

prepare 只做预览，不生成相册或全尺寸 RAW 输入。同目录、同名、唯一 RAW/JPG 且已知拍摄元数据不冲突时归为候选同拍，以 RAW 为正式来源、JPG 优先预览；缺失元数据不构成同拍证明。需要分开处理时加 `--separate-pairs`。无可用配对 JPG 时优先嵌入预览，再小尺寸显影；记录真实预览来源。RAW 正式渲染后置，规划预览不能冒充修图输入。检测框、时间、路径不等于模型已看图。

熟悉小批在计划中用 `calibration:direct`，首张可兼样片；保存计划默认冻结：

```sh
python -m auto_photo workflow plan output/BATCH --response plan.json
python -m auto_photo workflow run output/BATCH
```

新风格或高风险操作用 `calibration:sample`，先草稿、后选代表 ID 运行，再批量审样片；通过才冻结，无需强制发生一次修订：

```sh
python -m auto_photo workflow plan output/BATCH --response plan.json --draft
python -m auto_photo workflow run output/BATCH --ids SAMPLE_ID_1 SAMPLE_ID_2
# 样片经过实际视觉复核通过后再冻结
python -m auto_photo workflow freeze output/BATCH
python -m auto_photo workflow run output/BATCH
```

样片通过后保持原产物，不重生成。草稿可以校准；冻结后修改需 `plan --revise`。生成提示与方法未变，仅后处理调整时复用昂贵原生结果，当前批准不自动转移到新版本。

## 基础图准备与调光入口

```sh
# 默认：同拍JPEG → 合格内嵌JPEG → RAW显影回退
python -m auto_photo.raw_pipeline camera-base input/photo.ARW output/NEW_CAMERA_BASE
# 严格只使用ARW自身，不查找同拍JPEG；仍优先提取合格内嵌JPEG
python -m auto_photo.raw_pipeline camera-base input/photo.ARW output/NEW_EMBEDDED --no-paired-jpeg
# 显式要求重显影
python -m auto_photo.raw_pipeline camera-base input/photo.ARW output/NEW_RAW_BASE --force-raw
# LLM看图制定方案后，在RAW引擎内直接调光
python -m auto_photo.raw_pipeline develop input/photo.ARW control.json output/NEW_DEVELOP
```

`camera-base` 用原生JPEG完整解码尺寸检查长边≥3840（`--min-edge` 可覆盖），不强制16:9，不缩放正式输入；默认同拍候选必须唯一且已知拍摄元数据不冲突。`--paired-jpeg` 可显式指定，冲突仍拒绝。按EXIF/RAW方向归正，正确转换ICC或继承的Adobe RGB信息，不额外调光、降噪、锐化。无需变换时保留原JPEG字节；需要时一次质量96、4:4:4、sRGB编码。rawpy当前只能提取LibRaw选中的预览，无法保证枚举到容器内所有版本；未取到合格JPEG才回退RAW。基本技术检查不冒充视觉质量保证。

`camera-base --preview-edge 1600` 只额外生成小的规划预览，正式 `final.jpg` 始终保留完整尺寸。默认只存 `final.jpg` 与 `record.json`；RAW回退另保留所需控制配置。RAW回退仍使用匹配/自动曝光的保守起点，不保证复刻相机直出。新目录不得已存在。

`develop --preview-edge 1600` 则将本次RAW显影输出限制为小样；省略时全尺寸。它读取 `baseline`、`recipe`、可选 `tone_curve/gradient/denoise/capture_sharpening`，详见项目 `docs/workflow.md`；这些扩展字段尚未接入批次plan schema。此入口只做原生RAW光色，不调用imagegen。

新批次A/B/T默认自动调用机内JPEG优先策略；显式 `--raw-profile` 继续采用指定显影，旧批次恢复也保留原行为。C对RAW来源仍直接显影；若整集判断轻调机内图即可，先 `camera-base` 再将其 `final.jpg` 作为JPEG输入做C，而不是声称已做RAW显影。基础图不随艺术提示词/配方修改而重新准备。

审美规划使用 [摄影目标与提示词](photographic-goals.md)。风格示例是语言目标，不是一套通吃所有场景的参数。对照没有改善时标为未选用，保留更好的机内基底；无需为走流程生成整套中间图。

## 生成桥接与预算登记

run 只自动执行本地阶段。C 直接出 JPG；A/B/T 准备准确输入和 state 中的 generation_request，等待 Agent 调用宿主内置 imagegen。Pro 登录方式使用账户共享用量，本项目不需要 API key；这不保证工具可用、无限用量、固定型号或输出尺寸。每次新调用先登记：

```sh
python -m auto_photo workflow generation-begin output/BATCH PHOTO_ID
```

**只有返回 `should_call:true` 才调用 imagegen**，先看返回工单的准确 input。记录 attempt_id；`should_call:false` 表示已有待定请求，不重复发送、不用轮询登记消耗预算。回到已有返回或明确的停止/返工决定。

```sh
python -m auto_photo workflow import-generated output/BATCH PHOTO_ID /absolute/generated.png --attempt-id ATTEMPT_ID --metadata generation.json
python -m auto_photo workflow run output/BATCH
```

原生文件保持原字节。metadata 只写实际披露的模型、用量和来源。T 每块登记/导入都加 `--tile TL`（或 TR/BL/BR），完整四块后 run 才融合。确需重试用 `generation-begin ... --retry-reason "具体失败原因"`，受每目标 max_attempts 和批次预算双重约束；失败/未知结果也占预算。登记只记录工具交接，不证明后台收费或完成。

导入历史原生结果用 `--reused`，不先登记、不重新生成，不能同时传 attempt-id。没有 attempt-id 且未标 reused 的导入记 untracked，不能被当作本轮零生成证据。替换已有结果另加 `--replace`，新版本重新验收。

超分可提供 `--cv-runtime /path/to/opencv` 与 `--model-dir /path/to/models`。scale 1 不超分，sharpen false 不锐化；按交付目标决定，不按人数。已发请求结果未知时先核对返回，不能为恢复盲目再调用。

## 复核、交付与清理

按 [计划与验收](plan-schema.md) 提交当前 id/final_revision/passed/notes，建议写 severity/issues/viewing_long_edge。minor 通过就是可交付成片；strict 或 major 不允许以轻瑕疵名义放行。B 数值提醒候选需视觉检查，严重几何不足仍硬失败；T 错裁切或缺配准不能通过。

```sh
python -m auto_photo workflow review output/BATCH --decisions reviews.json
python -m auto_photo workflow package output/BATCH
python -m auto_photo workflow report output/BATCH
python -m auto_photo workflow metrics output/BATCH
```

package 立即打包当前已审 JPG 和省略项，不等未完成图、不检查其他聊天；重复打包用新名称，显式 output 不覆盖旧包。需要相册时 `package --gallery` 在 ZIP 完成后生成一次。report 只生成静态 index.html；metrics 只读状态输出指标，不生成额外文件。次数为已登记交接，未知 token/费用不补零。

```sh
python -m auto_photo workflow stop output/BATCH
python -m auto_photo workflow resume output/BATCH
python -m auto_photo workflow cleanup output/BATCH
```

stop 禁止后续本地任务，不撤回服务器请求；resume 复用可恢复阶段，不自动重发生成。cleanup 仅清理无活动发布依赖的临时工作项，保留原片、交付、昂贵原生结果和恢复依赖；不用手改状态修复。版本和元数据不证明内容完整性。ZIP 仅使用格式自带 CRC，无额外哈希清单和全包回读。
