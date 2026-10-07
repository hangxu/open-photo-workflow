# 参考实现 / Reference implementation

代码仅供参考，用来说明如何把工作流落实为本地处理步骤。安装 SKILL 不需要运行这些代码，也不需要安装其依赖。本次分享不附带测试套件或一键运行环境。

- `auto_photo/camera_input.py`：机内 JPEG 优先的基础图准备。
- `auto_photo/raw_control.py`、`rawtherapee.py`：RAW 显影参数与引擎调用。
- `auto_photo/workflow.py`、`generation_budget.py`：批次、调用登记与恢复。
- `auto_photo/grade.py`、`superres.py`、`restoration.py`、`tiled.py`：调光、超分、回贴和分块实验。
- `auto_photo/workflow_report.py`、`delivery.py`：静态相册和成片打包。

实现使用 Python，基础库涉及 Pillow、NumPy、rawpy；部分步骤还需 RawTherapee、OpenCV contrib、FSRCNN 权重或系统 ICC。当前文件锁依赖 POSIX，实际修图在 macOS 上验证过。移植时请让 Agent 按自己的环境和任务选择、修改。代码不会直接调用内置 image_gen；需要宿主 Agent 桥接。命令和数据结构见 [实现参考](../docs/workflow.md)。

## English

This code illustrates local steps of the workflow and is provided for reference only. Skill installation does not require running it or installing its dependencies. This sharing edition does not include a test suite or a ready-to-run environment.

The modules cover camera-JPEG input preparation, RAW controls, batch state, call registration, grading, optional restoration/tiling, static galleries, and delivery. They use Python with Pillow, NumPy, and rawpy; selected operations also need RawTherapee, OpenCV contrib, FSRCNN weights, or system ICC profiles. Locking requires POSIX; real photo runs were exercised on macOS. Adapt the code to the task and environment rather than assuming it is portable unchanged.

The implementation does not call built-in image_gen directly; a host Agent must bridge that step. See the [implementation reference](../docs/workflow.md) for commands and schemas (Chinese).
