# ACE3 中文化补丁安装工具

独立 Windows 安装器源码。EXE 不包含游戏 ISO 或任何汉化补丁；安装器不修改输入 ISO、不联网，也不对元数据发布者做身份认证。

## 开发测试

需要 Python 3.10 或更新版本；运行界面需 Python 带 Tk。安装器核心使用标准库；补丁制作脚本需要 `jsonschema`（Draft 2020-12）。

```powershell
python -m pip install -r installer/requirements-generator.txt
python -m unittest discover -s installer_tests -v
python installer/app.py
```

生成端测试使用独立的小型文件和官方 Xdelta3 3.2.2；测试覆盖实际编码、Schema 校验、随包 decoder 还原、安装器引擎消费生成的 FULL 补丁及逐字节比较。

真实 4.4 GB 场景另有两条独立实测（不属于上面那批模拟测试）：用发行版 `xdelta3.exe` 与 `ACE3_CN_v1.0.0_full.xdelta`，从原版日文 ISO 还原出完整镜像，产出 SHA-256 与候选盘逐字节一致，原版 ISO 前后 SHA-256 与修改时间未变。

- 引擎直调（`engine.install()`）：6 项判据全过。
- GUI 全路径（真实 `tkinter` `mainloop` 内触发 `app.App.start()`，工作线程 + `root.after` 回主线程更新进度与按钮状态）：14 项判据全过，含成功弹框、按钮复位、无残留 `.part`。

记录见 [`VERIFICATION.md`](VERIFICATION.md)（完整判据表与链路轨迹）。两条实测都在开发机上完成，不等同于干净 Windows 机器上双击 EXE 的验证，也不代表游戏运行已验收。

## 界面行为

三个「浏览…」按钮各自记住上次打开的目录：补丁、输入 ISO、输出 ISO 互不影响。选择文件或目录后写入 `%LOCALAPPDATA%\ACE3_CN_Patcher\settings.json`，下次启动（含重启电脑）仍从上次的目录打开。记忆的目录如果已被删除或改名，就退回默认位置，不会把无效路径塞进对话框。

打开对话框前程序会把主窗口提到前台并临时置顶，避免 Windows 前台锁把对话框压到主窗口后面——那种情况下点「浏览…」看起来像没反应。对话框关闭后立即取消置顶。

输出路径也可以直接在输入框里粘贴或键入，不一定要用对话框。

自动测试：`installer_tests/test_browse_memory.py`（三个键相互独立、重启后仍记得、已删除目录不再记忆、设置文件损坏安全降级、写入失败不抛异常、取消选择不破坏已有路径）。

## 生成 FULL / UPDATE 补丁

入口：`installer/make_patch.py`。该命令按冻结的 `release_manifest.schema.json` 生成同目录、同主体的 `.xdelta` 和 `.json`，从 source、target、patch 实际文件读取精确大小与 SHA-256；生成后以 Draft 2020-12 校验 Schema。不会覆盖已有补丁或 JSON。

FULL 必须提供经独立核实的纯净原版 ISO SHA-256 确认值；工具再与实际输入计算值逐字节核对。UPDATE 的 source 应是指定旧版中文版 ISO，工具记录其实际指纹；不得用版本名称推测兼容性。

```powershell
python installer/make_patch.py `
  --type FULL `
  --source "D:\\ACE3\\原版日文.iso" `
  --source-version "已核实原版标签" `
  --target "D:\\ACE3\\目标中文版.iso" `
  --target-version "经用户确认的目标版本标签" `
  --patch "D:\\发行暂存\\ACE3_CN_v1.0.0_full.xdelta" `
  --xdelta "D:\\发行暂存\\xdelta3.exe" `
  --expected-source-sha256 "<已独立确认的64位SHA-256>"
```

UPDATE 使用 `--type UPDATE`、指定旧版中文版 `--source` 和其版本标签；不要传 FULL 专用 `--expected-source-sha256`。正式发行的目标中文版版本和文件必须先由用户确认；不得把 R390 或任一开发盘当作正式版。

## 补丁包结构

选择补丁时，安装器读取相同目录、相同主体名的 JSON。例如：

```text
ACE3_CN_v1.0.0_full.xdelta
ACE3_CN_v1.0.0_full.json
xdelta3.exe
```

`release_manifest.schema.json`、`release_manifest_protocol.md` 是冻结的协议 v1；补丁制作 Agent 交接要求见 `PATCH_AUTHOR_HANDOFF.md`；FULL 和 UPDATE 的说明及测试示例位于 `examples/`。示例的文件哈希和大小仅为测试占位值，不能用于实际安装。每次版本更新只更换 `.xdelta` 和对应 `.json`，不得更改 v1 字段语义。

`patch.format_version` 记录生成补丁所用的 Xdelta3 编码器版本。它是描述性元数据，不表示或推断补丁格式兼容性；安装器不据此推断兼容性。

## 校验和安全

安装器严格拒绝未知 schema 版本和未知字段。补丁必须满足安全 ASCII 文件名规则，且 basename 与 JSON `patch.filename` 精确一致。安装前依次校验输入 ISO 的大小与 SHA-256、补丁大小与 SHA-256；还原输出先写入目标旁的 `.part`，再校验目标大小与 SHA-256。通过后才以不覆盖方式提交。源 ISO 安装前后复核 SHA-256；现有目标或预存 `.part` 都不会被覆盖或删除。Xdelta3 通过 shell=False 参数数组启动。

SHA-256 仅证明文件与所选元数据的指纹一致，不认证元数据来源。错误、取消和不匹配不会提交目标 ISO。

## 构建 Windows 64 位便携版

在含 Tk 的 Windows x64 Python 环境安装 PyInstaller 后，从仓库根目录运行：

```powershell
python -m PyInstaller --noconfirm --clean ACE3_CN_Patcher.spec
Copy-Item installer/release_manifest.schema.json dist/ACE3_CN_Patcher/release_manifest.schema.json
Copy-Item installer/release_manifest_protocol.md dist/ACE3_CN_Patcher/release_manifest_protocol.md
Copy-Item installer/PATCH_AUTHOR_HANDOFF.md dist/ACE3_CN_Patcher/PATCH_AUTHOR_HANDOFF.txt
Copy-Item installer/third_party/xdelta3-3.2.2/xdelta3.exe dist/ACE3_CN_Patcher/xdelta3.exe
Copy-Item installer/third_party/xdelta3-3.2.2/LICENSE-xdelta3.txt dist/ACE3_CN_Patcher/LICENSE-xdelta3.txt
Copy-Item installer/third_party/xdelta3-3.2.2/NOTICE.txt dist/ACE3_CN_Patcher/NOTICE-xdelta3.txt
Copy-Item installer/README.md dist/ACE3_CN_Patcher/使用说明.txt
```

构建步骤会把经核验的 Xdelta3 3.2.2 `xdelta3.exe`、Apache License 2.0 文本和来源/哈希记录放在 EXE 旁；安装器从 EXE 所在目录启动该程序，不执行补丁目录中的程序。补丁 `.xdelta` 与同名 `.json` 由补丁发行者提供，用户亦可选择其他位置的补丁。包中不得放入游戏 ISO。上游发行页、官方压缩包 SHA-256、可执行文件 SHA-256 和许可证见 `third_party/xdelta3-3.2.2/NOTICE.txt`。

当前工作区已存在一套实际生成并实测过的 FULL 补丁物料（补丁 + 同名 JSON + 版本说明），其源与目标指纹均是从盘上实际计算得到，并已完成一次真实 4.4 GB 还原闭环。该套物料对应状态为 `PATCH_READY / RELEASE_PENDING_RUNTIME`：镜像层面的可还原性已验证，游戏运行与汉化显示尚未实机验收。

尚无 UPDATE 补丁。若要做版本更新补丁，其 source 必须是用户明确指定的旧版中文 ISO，工具记录其实际指纹，不得用版本名称推测兼容性。

## 发行前验证

1. 对实际源 ISO、目标 ISO 和 `.xdelta` 计算精确大小与 SHA-256，写入对应同名 JSON。**（已完成）**
2. 使用计划随包发行的 Xdelta3，从该源 ISO 还原目标 ISO；确认还原输出 SHA-256 等于发行目标记录，源 ISO 前后哈希相同。**（已完成两条独立路径：引擎直调 6/6、GUI 全路径 14/14，见 [`VERIFICATION.md`](VERIFICATION.md)）**
3. 核实 Xdelta3 可执行文件来源、版本与许可证/再分发条件。`format_version` 记录的是生成补丁所用编码器版本，不替代该核验。**（已完成，记录在 `third_party/xdelta3-3.2.2/NOTICE.txt`）**
4. 在干净 64 位 Windows 环境验证 EXE，覆盖中文路径、权限、取消和输出冲突；分别记录模拟测试与真实 ISO 结果。**（未完成：目前只有开发机上的 EXE 界面布局验证、GUI 全路径实测与模拟测试）**
5. 为最终发行目录中的 EXE、Xdelta3、补丁、JSON 和文档生成 SHA-256SUMS。**（已完成，逐项复核通过）**
6. 游戏运行与汉化显示的实机验收。**（未完成，`RUNTIME_NOT_RUN` 仍成立）**

安装成功只证明输出与所选 JSON 中目标指纹相符，不代表 PCSX2 或实际游戏运行已通过测试。
