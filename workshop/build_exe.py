"""把 ACE3 汉化工坊打成 Windows 文件夹式 EXE（无控制台窗口）。

设计要点（都是本工程实证过的约束，不是猜的）：

1. **文件夹式（onedir）**：任务书要求"包含必要运行库的文件夹式发布"，
   用户不装 Python / numpy 也能跑。
2. **windowed（无控制台）**：``--noconsole`` 会让 ``sys.stdout`` / ``sys.stderr``
   变成 ``None``；工程里被复用的历史模块（``lz_encode``、``_r104_audit2``、
   ``_r211_override``、``locator_core`` …）在 import 时都执行
   ``sys.stdout.reconfigure(...)``，所以 ``main.py`` 在导入业务模块**之前**
   先装 ``_NullIO`` 顶替（见 ``main._ensure_stdio``）。
3. **业务代码全部来自 ``tools/`` 根与 ``tools/ace3_text_locator/``**：这两处
   是冻结/复用的权威实现，不复制、不改写，只通过 ``--paths`` + ``--hidden-import``
   收进包里。
4. **数据不打包**：码表 TSV（``tmp_r203`` / ``tmp_r205``）、索引库
   （``tools/ace3_text_locator/db``）、字体缓存都是运行时从工程根读的只读数据；
   打进包里反而会和工程事实脱钩。所以 ``core/env.py`` 在 frozen 模式下用
   ``ace3_root.txt`` / 向上查找来定位工程根。

用法::

    python build_exe.py            # 完整打包
    python build_exe.py --clean    # 先清 build/dist 再打包
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _find_root(start: str, levels: int = 6) -> str:
    """向上找含 ``tools/`` 的目录：发布仓库里是 ``publish/``，源码树里是工程根。

    旧写法 ``dirname(dirname(HERE))`` 只对 ``tools/ace3_editor_gui`` 成立，
    搬到 ``publish/workshop`` 之后会指到上一层之外，所以改成按目录特征查找。
    """
    cur = start
    for _ in range(levels):
        if os.path.isdir(os.path.join(cur, "tools")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return os.path.dirname(start)


ROOT = _find_root(HERE)                                # publish/ 或 工程根
TOOLS = os.path.join(ROOT, "tools")
LOCATOR = os.path.join(TOOLS, "ace3_text_locator")

APP_NAME = "ACE3汉化工坊"

# 需要显式收进包里的历史模块（PyInstaller 静态分析看不穿 sys.path 注入）
HIDDEN = [
    # 本项目
    "core", "core.env", "core.codec", "core.slots", "core.fontcov",
    "core.relayout", "core.writer", "core.search", "core.editplan",
    "core.pipeline", "core.estimate", "core.settings", "core.paths",
    "ui", "ui.theme", "ui.preview", "ui.widgets", "ui.workers",
    "ui.results", "ui.editor", "ui.queue", "ui.checkdesk",
    "ui.indexmgr", "ui.main_window",
    # 冻结检索层
    "locator_core", "query", "build_index",
    # 被检索层间接依赖的底层
    "_r223_apply_plan", "_r104_glyphfix2", "_r104_audit2", "_r104_text_audit",
    "_r211_cmap", "_r211_override", "_r206_memfix",
    "_r213_lz", "lz_encode", "lz_decode_entry",
    "ace3_pack", "front_atlas",
]


def _clean() -> None:
    for d in (os.path.join(HERE, "build"), os.path.join(HERE, "dist")):
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
            print("  清理 %s" % d)
    spec = os.path.join(HERE, APP_NAME + ".spec")
    if os.path.exists(spec):
        os.remove(spec)


def build(clean: bool = False) -> int:
    if clean:
        _clean()

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--windowed",                        # 无控制台窗口
        "--onedir",                          # 文件夹式
        "--name", APP_NAME,
        "--distpath", os.path.join(HERE, "dist"),
        "--workpath", os.path.join(HERE, "build"),
        "--specpath", HERE,
        "--paths", HERE,
        "--paths", TOOLS,
        "--paths", LOCATOR,
        "--noupx",
        # 强制管理员运行：把 requestedExecutionLevel 写进 PE manifest，
        # 双击就提权（本机 ConsentPromptBehaviorAdmin=0，不会弹 UAC 框）。
        # 守则要求「只在工具自己目录下写」，没有管理员权限时连 work 目录
        # 都可能被拒，所以这不是可选项。
        "--uac-admin",
    ]
    # 程序图标：ico 由 assets/ace3.ico 提供（多尺寸 16/24/32/48/64/128/256）
    _ico = os.path.join(HERE, "assets", "ace3.ico")
    if os.path.isfile(_ico):
        cmd += ["--icon", _ico]
        print("  图标：%s" % _ico)
    else:
        print("  [警告] 未找到 %s，将使用默认图标" % _ico)
    # 不需要的 Qt 大件，剔掉能少几十 MB
    for mod in ("PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
                "PySide6.QtQml", "PySide6.QtQuick", "PySide6.Qt3DCore",
                "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization",
                "PySide6.QtNetwork", "PySide6.QtSql", "PySide6.QtTest",
                "tkinter", "matplotlib", "scipy", "pandas", "IPython"):
        cmd += ["--exclude-module", mod]
    for h in HIDDEN:
        cmd += ["--hidden-import", h]
    cmd.append(os.path.join(HERE, "main.py"))

    print("打包中…（首次约 1–3 分钟）")
    r = subprocess.run(cmd, cwd=HERE)
    if r.returncode != 0:
        print("PyInstaller 失败，退出码 %d" % r.returncode)
        return r.returncode

    out_dir = os.path.join(HERE, "dist", APP_NAME)
    exe = os.path.join(out_dir, APP_NAME + ".exe")
    if not os.path.exists(exe):
        print("没有生成 %s" % exe)
        return 1

    # 把工程根固定下来：EXE 放在工程内任意位置也能找到数据
    with open(os.path.join(out_dir, "ace3_root.txt"), "w", encoding="utf-8") as f:
        f.write(ROOT + "\n")
    # Qt 窗口图标（任务栏/Alt-Tab）读的是 exe 同目录的 ace3.ico
    if os.path.isfile(_ico):
        try:
            shutil.copyfile(_ico, os.path.join(out_dir, "ace3.ico"))
        except OSError as e:
            print("  [警告] 复制图标失败：%s" % e)
    # 附一份使用说明（离线、无联网要求）
    readme = os.path.join(out_dir, "使用说明.txt")
    with open(readme, "w", encoding="utf-8") as f:
        f.write(_README % {"root": ROOT, "exe": APP_NAME + ".exe"})

    size = sum(os.path.getsize(os.path.join(dp, fn))
               for dp, _dn, fns in os.walk(out_dir) for fn in fns)
    print("\n完成：%s" % exe)
    print("目录总大小：%.1f MB" % (size / 1048576.0))
    return 0


_README = """ACE3 汉化工坊 · 使用说明

一、这是什么
  用于 ACE3（Another Century's Episode 3）中文化的图形编辑器：
  搜索文字 → 定位文本 → 改中文 → 实时验证字库与容量 → 暂存修改 →
  一次性生成新的 ISO。父盘只读，绝不覆盖。

二、怎么启动
  双击 %(exe)s。程序强制以管理员身份运行（manifest 已声明，本机 UAC
  不弹框）。全程离线，不需要联网，不需要安装 Python。

三、数据从哪来
  程序会读取工程根下的只读数据（索引库、码表、字体缓存）：
      %(root)s
  若工程被移动了位置，把本目录下的 ace3_root.txt 改成新的工程根路径即可。

四、不写系统盘（硬约束）
  所有产物只能落在本工具目录内：草稿、检查报告、日志、临时文件、新 ISO。
  临时目录已钉到 work\\tmp，不会往 %%TEMP%% 写。
  选到系统盘或工具目录之外的路径会被当场拦下并提示改道。

五、基本流程
  1. 顶部「打开 ISO…」选择要改的盘（第一次会花几分钟建索引）。
  2. 左栏搜索（支持文字、EID、码位、偏移）。
  3. 中栏改写中文：右侧会实时显示字库检查（PASS/WARN/BLOCK）与容量。
  4. Ctrl+Enter 加入修改队列；队列会自动存草稿，关掉程序也能恢复。
  5. Ctrl+Shift+E 打开「统一写盘检查台」→ 阻断项清零后可生成新 ISO。
  6. 写盘完成后会给出新 ISO 路径、MD5、独立回读报告与差分白名单。

六、注意事项
  * 字库为 BLOCK 的字符不允许写盘；WARN 表示部分字体包缺字，实机渲染包
    恒为 UNKNOWN，请自行确认后再写。
  * 预览只是近似模拟，不保证与 PS2 实机像素一致。
  * 压缩资源会整条解压→改字→整条重压，并做独立解压回读比对。
  * 任何一步失败都不会提交产物，父盘与修改队列保持原样。
  * 索引记的盘被挪走时，版本下拉会标「（盘不在原处）」；用「打开 ISO…」
    找到新位置即可自动认回（大小与 MD5 都对得上时），不必重建。
"""


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean", action="store_true")
    a = ap.parse_args()
    raise SystemExit(build(clean=a.clean))
