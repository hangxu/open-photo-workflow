# 安装 SKILL / Install the skills

[中文 README](README.md) · [English README](README.en.md)

## 中文

安装的是两份 Agent 工作指引，不需要先安装 Python、RAW 引擎或超分模型。把 [README 的安装提示词](README.md#交给-agent-安装)交给 Agent 即可。

手动安装也可以：

1. 下载或克隆本仓库。
2. 将 `skills/photo-workflow` 和 `skills/imagegen-tiled-upscale` 两个完整目录复制到 Agent 的技能目录。Codex 使用 `$CODEX_HOME/skills`，未设置 `CODEX_HOME` 时为 `~/.codex/skills`。
3. 已有同名技能时先比较并保留旧版。新建会话确认发现技能；必要时重启客户端。
4. 在任务中使用 `$photo-workflow`，需要分块实验时再指定 `$imagegen-tiled-upscale`。

其他 Agent 请使用各自的技能安装目录。没有内置图像工具时，安装 SKILL 不会补出这项能力；不要因此自动购买或切换 API。

`reference-code/` 中的 Python 代码仅供参考，不是安装要求。需要实际运行时，Agent 再阅读其中的说明，按当前环境准备所需工具。SKILL 内 `scripts/` 也是可选的参考实现入口，不必为了装技能运行它们。

## English

These are Agent instructions. Installing them does not require Python, a RAW engine, or super-resolution models. Give an Agent the [installation prompt in the README](README.en.md#ask-an-agent-to-install-it), or install manually:

1. Download or clone this repository.
2. Copy the complete `skills/photo-workflow` and `skills/imagegen-tiled-upscale` directories into your Agent's skills directory. Codex uses `$CODEX_HOME/skills`, or `~/.codex/skills` when `CODEX_HOME` is unset.
3. Compare existing installations and keep the old version before replacement. Start a new session to check discovery; restart the client if needed.
4. Invoke `$photo-workflow`; use `$imagegen-tiled-upscale` when you want a tiling experiment.

Other Agents use their own skill locations. Installing instructions does not add a missing image-generation tool; do not automatically purchase or switch APIs.

The Python code in `reference-code/` is optional reference material. Read its notes and prepare the required tools only when an actual task needs them. Skill `scripts/` are optional wrappers for that implementation, not installation checks.
