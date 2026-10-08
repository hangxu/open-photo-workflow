# 可替换的图片生成步骤

默认使用 Codex 内置 `image_gen`，消耗 ChatGPT Pro 的共享额度，无需 API Key。用户明确选择其他图片编辑工具时，只替换生成步骤；基础图、摄影目标、2×2 切块、配准拼合、FSRCNN 超分和验收沿用原流程。不因内置工具不可用而自动切换到外部 API。

Wan 是已试过的一个例子。其他工具只要能编辑给定图片、返回可下载的成片，就可以由 Agent 调用后导入；这不表示仓库已内置所有服务的客户端。调用前核对该工具的编辑能力、图片限制、尺寸参数、结果下载方式和额度。四块各生成一次，按服务限额决定串行或并行。

## Wan 2.6 单次调用参考

[`reference-code/wan_image_edit.py`](../reference-code/wan_image_edit.py) 只演示一次图片编辑，不是完整服务集成。它需要 Python 和 Pillow；仅安装 SKILL 无需安装这些依赖。

密钥预先放入本地环境变量 `DASHSCOPE_API_KEY`，或用 `--api-key-env` 指定已有变量名。脚本不自动读取 `.env`，不把密钥当命令行参数，也不保存密钥、图片 base64、下载地址或完整服务响应。不要把密钥写进提示词、生成记录或提交文件。

从仓库根目录运行，先准备准确输入和不超过 2000 字符的 UTF-8 提示词文件，再调用：

```sh
python reference-code/wan_image_edit.py /path/to/input.jpg \
  --prompt-file /path/to/prompt.txt \
  --output /path/to/NEW_RUN \
  --base-url https://YOUR_WORKSPACE.cn-beijing.maas.aliyuncs.com/compatible-mode/v1 \
  --size 2K
```

示例域名必须换成自己的服务地址。这个 Wan 编辑请求使用原生接口，不是 OpenAI 的 `/images/edits`：

```text
POST https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation
Authorization: Bearer <从环境变量读取>
```

请求使用 `model: wan2.6-image`，在 `input.messages[].content` 传入编辑提示词和图片 data URL。参数为 `size: 2K`、`n: 1`、`enable_interleave: false`、`prompt_extend: false`、`watermark: false`、`stream: false`。`n` 明确为 1，避免服务默认返回多张；脚本也可指定 `--size 1K`。接口限制与参数以 [阿里云文档](https://help.aliyun.com/zh/model-studio/wan-image-generation-api-reference) 为准。

- 输入每边 240–8000 像素，文件不超过 10 MB。超过限制时，另存合适的输入副本，不覆盖基础图；脚本不会偷偷压缩或改尺寸。
- `2K` 指约 2048² 的像素量，不是长边 4K；竖图实验曾返回 1664×2512，具体看实际文件。输出比例与尺寸取整仍需检查。
- 输出目录必须是新的。发送前写调用记录，每次执行最多一个生成 POST，无自动重试；超时或结果未知也可能消耗额度，不要换目录盲目重发。下载失败同样不重新生成。
- 原始返回字节保存为 `result.*`，真实文件名、尺寸、格式、服务披露的用量和耗时写入 `record.json`。下载 GET 不算新生成。
- Wan 编辑的 token 字段为 0 不代表免费，需看 `usage.image_count` 及服务额度。它与 ChatGPT Pro 无关；试用额度低时优先串行。

## 接回整图或四块流程

Agent 查看输入并确定提示词后，整图调用一次；2×2 则对 TL、TR、BL、BR 分别调用，各用独立输出目录。默认每次只输入当前块，保持裁切和统一风格；可见眼部遵守 [眼神保护](../skills/photo-workflow/references/photographic-goals.md#眼神与眼部保护)。四块齐全后按真实返回尺寸合并，不预先补齐 4K，再按需超分。

若借用批次参考代码，在 `reference-code/` 中照常登记和导入：

```sh
python -m auto_photo workflow generation-begin output/BATCH PHOTO_ID --tile TL
# 只有 should_call:true 才调用所选工具，保存返回的 attempt_id。
# 使用上述 Wan 脚本或当前宿主的图片编辑工具生成，取得原生文件和记录。
python -m auto_photo workflow import-generated output/BATCH PHOTO_ID /path/to/result.png \
  --tile TL --attempt-id ATTEMPT_ID --metadata /path/to/record.json
python -m auto_photo workflow run output/BATCH
```

整图省略 `--tile`；其余三块替换块 ID，`result.png` 换成实际文件名。独立切块脚本则直接把四份结果交给 `merge`，见 [执行说明](../skills/imagegen-tiled-upscale/references/execution.md)。不需要修改计划 schema 或增加 provider 框架。保留实际提示词与块 ID 的对应关系，记录所选服务和请求型号；服务未披露的实际型号、用量留空，不把请求值当成返回证据。

内置 `image_gen` 仍按宿主公开 schema 调用，不给它添加并不存在的 `size/model/quality` 参数；外部 API 的尺寸写在其真实支持的参数里。只替换工具后也需要原有的一次视觉验收，不能仅凭像素数认定修图成功。
