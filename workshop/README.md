# ACE3 汉化工坊

《Another Century's Episode 3 THE FINAL》汉化工程的图形化编辑工具。PySide6 界面，用来打开游戏 ISO、建立文本检索索引、查找与编辑文本槽、检查字库覆盖、推演写盘容量，并把改动写回镜像。

本目录是**源码**。工具只读写你自己提供的镜像文件，不联网、不检查更新、不上传任何数据。

## 目录结构

```
workshop/
├─ main.py              启动入口（GUI、--smoke、--selftest）
├─ ace3edit.py          命令行入口（info / find / check / build）
├─ build_exe.py         PyInstaller 打包脚本
├─ ACE3汉化工坊.spec     打包描述（全相对路径）
├─ core/                业务层：编解码、槽表、字库覆盖、编辑计划、写盘流水线
├─ ui/                  界面层：主窗口、编辑器、预览、结果表、索引管理
├─ assets/              ace3.ico（程序图标）、ace3_logo.png（品牌图）
└─ _selftest_*.py 等     自测与验收脚本（见下文「测试」）
```

`core/` 与 `ui/` 依赖上级目录的 `tools/`（资源格式底层模块与文本定位器），因此本目录**不能单独搬走运行**：`tools/` 必须与 `workshop/` 处于同一父目录。

`tools/` 侧除了 `.py` 模块，还必须带 **`tools/data/map_r203.tsv`**（R203 码表，157 KB）。`core/codec.py` 的码表来自 `_r211_cmap` → `_r206_memfix.load_cur()`，后者按 `tools/data/map_r203.tsv` → `<工程根>/tmp_r203/map_r203.tsv` 的顺序找；两处都没有会抛 `FileNotFoundError`，整个码表与所有文本解析都起不来。发布包里它随 `publish/tools/data/` 一起分发。

## 运行环境

- Python 3.10 以上（开发与打包使用 3.12）
- 依赖：`PySide6`、`numpy`、`Pillow`
- Windows（界面与提权逻辑针对 Windows 编写）

```powershell
pip install PySide6 numpy Pillow
python main.py
```

工程根由启动逻辑自动定位：从本文件所在目录向上找同时含 `tools/` 与 `workshop/`（或 `工程档案库/`）的目录。也可以用环境变量 `ACE3_ROOT` 指定。

启动时 `core/env.py` 的 `ensure_sys_path()` 会把两个目录插到 `sys.path` 最前面，扁平 import（`import lz_encode`、`from _r213_lz import …`、`from _r104_audit2 import …`）靠它们解析：

1. `<工程根>/tools`
2. `<工程根>/tools/ace3_text_locator`

外加 `main.py` 自己把 `workshop/` 插进 `sys.path`（供 `import core` / `import ui`）。三者缺一，`core.codec` 或检索功能会 `ModuleNotFoundError`。

## 命令行入口

```powershell
python main.py                            # 正常起窗
python main.py --smoke                    # 起窗 2 秒自动关（验证能启动）
python main.py --selftest out.json        # 无头跑全链路（会真写盘）
python main.py --selftest out.json --no-write   # 只读，不产生 ISO
```

`ace3edit.py` 是不带界面的等价入口，适合脚本化：

```powershell
python ace3edit.py info
python ace3edit.py find "何？" --iso <你的镜像.iso>
python ace3edit.py check --iso <镜像> --plan <草稿.json>
python ace3edit.py build --iso <镜像> --plan <草稿.json> --out <输出目录>
```

## 使用流程

1. 打开 ISO，工具读取盘面目录表与文本资源。
2. 建索引（检索缓存落在 `tools/ace3_text_locator/db/<盘名>/`，共享缓存在 `db/_shared/`）。索引绑定父盘 MD5，换盘后会要求重新建。
3. 查找文本槽，在编辑器里改译文；预览按字形度量近似，不是 PS2 渲染模拟器。
4. 检查字库覆盖与容量：缺字、超长、标签变动都会在写盘前拦下。
5. 导出编辑计划，再 `build` 写出新镜像。原盘全程只读，产物是新文件。

## 写盘安全约束

- **只写自己的目录**：默认拒绝写到系统盘，`tempfile` 与 `TMP`/`TEMP` 在启动时钉到 `work/tmp`。
- **不覆盖已有文件**：输出走临时文件再提交，目标已存在则拒绝。
- **计划绑定父盘**：编辑计划记录父盘 MD5，换盘后旧计划不会被套用。
- **标签指纹校验**：写盘前比对标签序列与参数（带顺序），增删标签、改参数、互换先后都会被判为差异并阻止。

## 已知边界

这些是设计边界，不是待修 Bug：

1. **标签只能整体不动**：写盘校验标签序列与参数，但「把插入点在正文里前挪或后挪几个字」不会被判出。界面不提供挪标签的操作，该能力未实现。
2. **实机渲染包恒为 UNKNOWN**：覆盖检查的 PASS 只代表字库包静态覆盖充分，不代表 PCSX2 或实机上一定由某个包渲染出来。
3. **预览不是像素级还原**：对话框预览按字形度量近似，不能当作实机效果。
4. **跨版本不认偏移**：计划与父盘 MD5 绑定，换盘即失效。
5. **不写系统盘的规避手段**：本机实测「程序在 E: 盘、往 C: 盘写」会失败，根因未定位；程序改为一律不写系统盘来规避，根因本身仍标 `UNKNOWN`。
6. **名字块只能块内重排**：四个目标资源尾部余量为 0，扩写只能吃掉块内 padding，超出即拒绝，不推动块外数据。
7. **战斗武器指令资源**：压缩写盘路径已由单个 EID 证明可用，武器指令列表这批数据在测试盘上是非压缩资源，该专项标 `NOT_RUN`。

## 打包

```powershell
python build_exe.py --clean
```

产物在 `dist/ACE3汉化工坊/`。spec 使用全相对路径，整包搬到任何盘符都能直接构建；`pathex` 指向本目录与上级 `tools/`、`tools/ace3_text_locator/`。

打包出的 EXE 申请管理员权限（`uac_admin=True`），因为写盘流程需要访问镜像所在位置。EXE 不是单文件绿色版：它仍需要访问工程目录里的 `tools/`（只读码表与检索工具）以及对应 ISO 的检索索引库。

## 测试

本目录含自测与验收脚本，全部为**只读或写入临时目录**，父盘不被改动：

```powershell
python _selftest_codec.py       # 编解码与码表
python _selftest_paths.py       # 路径与写盘约束（50 项）
python _selftest_editplan.py    # 编辑计划
python _selftest_fontcov.py     # 字库覆盖
python _selftest_relayout.py    # 重排
python _selftest_writer.py      # 写盘
python _selftest_jobqueue.py    # 任务队列
python _selftest_indexmgr.py    # 索引管理
python _selftest_capacity.py    # 容量推演
```

其中 `_selftest_slots.py`、`_selftest_nametext.py`、`_selftest_writer.py`、`_selftest_capacity.py`、`_selftest_relayout.py`、`_selftest_names_gui.py`、`_verify_all_slots.py`、`_acceptance_names.py` 需要一份真实的游戏镜像在位。镜像路径**不写死**，用环境变量指定：

```powershell
$env:ACE3_TEST_ISO = "D:\你的镜像.iso"
python _selftest_slots.py
```

没有设置也没放镜像时会以 `FileNotFoundError: PATH\TO\TEST.iso` 退出——这是缺测试盘，不是代码缺陷。

`_selftest_gui.py`、`_selftest_names_gui.py` 与 `_acceptance*.py`、`_gui_mouse_acceptance.py` 需要 Qt 环境与真实镜像，并会占用较多临时空间（完整验收约 15 GB）。

## 许可

本目录源码按 MIT 许可发布，见仓库根目录 [LICENSE](../LICENSE)。游戏资源、字库与第三方组件不在本项目授权范围内，见[第三方说明](../THIRD_PARTY_NOTICES.md)与[许可范围](../LICENSE-SCOPE.md)。
