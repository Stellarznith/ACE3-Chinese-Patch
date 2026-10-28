# 安装器验证记录

本页记录打补丁工具（`installer/`）实际做过的验证。**没有证据的条目不计入「通过」。**

## 一、结论摘要

| 项目 | 环境 | 结果 |
|---|---|---|
| 引擎直调（`engine.install()`），真实 4.4 GB 物料 | 开发机 | 6/6 判据通过 |
| GUI 全路径（真实 tkinter `mainloop`），真实 4.4 GB 物料 | 开发机 | 14/14 判据通过 |
| 引擎边界用例（错误版本、损坏补丁、空间不足、取消、中文路径等） | 开发机（模拟文件） | 见 `installer_tests/test_engine.py` |
| 补丁生成与 Schema 校验 | 开发机 | 见 `installer_tests/test_make_patch.py` |
| 浏览按钮目录记忆 | 开发机 | 见 `installer_tests/test_browse_memory.py` |
| EXE 界面布局 | 开发机 | 见 `installer_tests/check_layout.py` |
| 干净 Windows 机器双击 EXE | — | **未验证** |

## 二、真实 4.4 GB 物料的两条独立路径

两条路径都用发行实物物料，无 mock、无缩小文件。

### 2.1 引擎直调

直接 `import engine` 后调用

```
engine.install(PATCH, SRC, DEST, cancel=..., progress=..., xdelta_executable=XD)
```

即 GUI「开始安装汉化补丁」按钮最终落到的那条代码路径，差别只是回调改成打印 stdout。

| # | 判据 | 结果 |
|---|---|---|
| 1 | 产出大小 == 清单 `target.size_bytes`（4,447,076,352） | 通过 |
| 2 | 产出 SHA-256 == 清单 `target.sha256` | 通过 |
| 3 | 产出与参照成品盘逐字节相同 | 通过 |
| 4 | 原版 ISO 未被修改（内容 + 大小 + 修改时间） | 通过 |
| 5 | 原版 ISO 前置 SHA-256 == 清单 `source.sha256` | 通过 |
| 6 | 无残留 `*.part` | 通过 |

### 2.2 GUI 全路径

实例化 `installer/app.py` 的真实 `App` 对象，在真实 `tkinter` `mainloop` 内用 `root.after(200, ui.start)` 触发按钮动作，由工作线程 + `root.after` 回主线程更新进度与按钮状态，走完
「读清单 → 校验补丁 → 校验源 ISO → 还原 → 校验产出 → 提交 → 弹成功框 → 按钮复位」。

两处刻意的环境等价改写（均已声明）：

1. `app.resource_dir` 覆盖为打包目录，复现 frozen 模式下「从 EXE 同目录加载 `xdelta3.exe`」；
2. `tkinter.messagebox` 替换为记录器，避免模态框阻塞自动化。

| # | 判据 | 结果 |
|---|---|---|
| 1 | 未触发看门狗超时 | 通过 |
| 2 | 弹出成功消息框（含「安装成功」） | 通过 |
| 3 | 无失败/取消消息框 | 通过 |
| 4 | 最终 stage 含「安装完成」 | 通过 |
| 5 | 最终 detail 指向输出路径 | 通过 |
| 6 | `busy` 已复位 | 通过 |
| 7 | start 按钮恢复 `normal` | 通过 |
| 8 | cancel 按钮转为 `disabled` | 通过 |
| 9 | 「打开输出目录」按钮转为 `normal` | 通过 |
| 10 | 产出大小 4,447,076,352 | 通过 |
| 11 | 产出 SHA-256 == 目标指纹 | 通过 |
| 12 | 无残留 `.part` | 通过 |
| 13 | 原版 ISO 未被修改（SHA-256 + size + mtime） | 通过 |
| 14 | 原版前置 SHA-256 == 清单 `source.sha256` | 通过 |

链路观测：`0.2s` 出图 → `3.3s` 读清单 → `3.6s` 校验补丁 → `3.8s` 校验源 ISO → `7.0s` 生成目标 → `7.2s` 应用补丁 → `14.8s` 校验产出 → `22.8s` 成功消息框 + 按钮复位 → `30.8s` 全部判据通过，总墙钟 **30.8 s**。

> 首版驱动脚本曾在 `mainloop` 外轮询，工作线程调用 `root.after` 抛
> `RuntimeError: main thread is not in main loop`。那是测试脚本写法问题，不是产品缺陷；
> 改成在 `mainloop` 内用 `root.after` 轮询后全部通过。此记录留档以免误判。

## 三、引擎内部各阶段实测轨迹

| 阶段 | 耗时 | 观测 |
|---|---|---|
| 补丁 SHA-256 校验（约 427 MB） | 3.1 → 3.4 s | 0→100% 进度回调正常，无跳变 |
| 原版 ISO SHA-256 校验（4.14 GB） | 3.4 → 6.8 s | 4 MiB 分块流式，进度粒度 1% |
| xdelta3 还原 | 6.8 → 13.5 s | 子进程以参数数组调用；stderr 独立捕获后回报「无错误输出，退出 0」 |
| 产出 SHA-256 校验 | 13.5 → 16.9 s | — |
| 原版二次 SHA-256 防篡改复核 | → 20.2 s | 大小与哈希均未变，未触发中止 |
| `_commit_no_replace` 提交 | 20.2 s | 原子改名，`.part` 转正 |
| 报告「安装完成。」100% | 20.2 s | — |

## 四、本页覆盖不到的部分（不得据此宣称已验证）

1. **干净 Windows 机器**：以上都在开发机执行，未在无 Python、无开发工具的 64 位 Windows 上跑 EXE。
2. **真实 EXE 双击启动 + 点击按钮**：GUI 路径用的是同一份 `app.py` 源码实例化，不是打包后的 EXE 本体；EXE 本体只验证了窗口出图与按钮布局（`installer_tests/check_layout.py`）。
3. **中文路径 / 长路径 / 只读目录 / 空间不足** 等边界：由 `installer_tests/test_engine.py` 的模拟测试覆盖，非真实盘测试。
4. **真实 4.4 GB 场景下的中途取消**：未做；模拟测试已覆盖取消清理。
5. **游戏可运行性**：产出镜像与成品母盘逐字节相同，但「游戏能否正常跑、汉化是否显示正确」属实机验收，本页不做结论。

## 五、如何自行复跑

```powershell
python -m pip install -r installer/requirements-generator.txt
python -m unittest discover -s installer_tests -v
python installer_tests/test_browse_memory.py
```

真实 4.4 GB 物料的两条路径需要自备原版 ISO 与补丁，脚本不在本仓库内提供（它们引用了作者本机路径）。按[安装说明](../docs/INSTALL.md)手动跑一遍 `xdelta3 -d` 并核对成品 SHA-256，即可得到等价的离线结论。
