# 第三方组件与权利说明

> 核实时间：2026-10-28（xdelta3 条目已按实际采用的二进制重新核实并填入指纹）。已核实的写明来源与哈希；无法核实的保持「待确认」，不填推测值。

## 一、差分补丁工具 xdelta3

| 项目 | 内容 |
|---|---|
| 上游仓库 | `https://github.com/jmacd/xdelta` |
| 采用版本 | **3.2.2**（release tag `v3.2.2`，资产 `xdelta3-3.2.2-windows-x86_64.zip`） |
| 许可证（已核实） | **Apache License 2.0**：上游 README 的 License 一节说明，3.0 起由原作者重新授权为 APL，3.1、3.2.x 沿用 |
| 历史 GPL 版本 | 另存于 `https://github.com/jmacd/xdelta-gpl`，本项目不采用 |
| 上游资产 SHA-256 | `d48f7651d86b78fdca397057b46601f89131f37219675df736db70af44ec3379` |
| 本仓库内的文件 | `installer/third_party/xdelta3-3.2.2/`：`xdelta3.exe`（340,992 B，SHA-256 `732980d52f9220dc81817ccdd0c295eadce8a0048a24c2a771a73f791ac824e9`）、`xdelta3decode.exe`（48,128 B，SHA-256 `67f84255bd009a65b1252784f8b3ebc72b5907766bc366c571e8b85fa7002a06`）、`LICENSE-xdelta3.txt`、`NOTICE.txt` |
| 与上游的关系 | 未修改的原样副本；两个可执行文件的版本横幅均实测为 `Xdelta version 3.2.2` |
| 分发方式 | 随安装器发行包分发独立可执行文件（`ACE3_CN_Patcher.zip` 内同样附带），并在本仓库源码树内保留同一份副本 |

分发义务（已按实际选择履行）：

- 发行包内附带 Apache-2.0 许可全文（`LICENSE-xdelta3.txt`）与来源说明（`NOTICE.txt`：上游地址、release tag、资产名、上游资产哈希、两个二进制的哈希）。
- APL 不要求提供源码，但不得移除版权与许可声明；本仓库与发行包均未修改这两个可执行文件。
- 未采用 GPL 版本（3.0 以前旧版或第三方 GPL 构建），因此不产生对应的源码提供义务。

> 若要替换为本项目自行编译的版本，需重新计算并更新上述哈希，同时在本页与 `NOTICE.txt` 两处同步修改。

## 二、中文字形来源

| 项目 | 内容 |
|---|---|
| 字形生成方式 | 由工程内渲染器用字体文件渲染出灰度位图，再写入游戏字库；游戏中显示的是**位图**，不是字体文件 |
| 采用的字体 | **Source Han Sans SC Bold**（思源黑体 Bold），文件 `SourceHanSansSC-Bold.otf` |
| 字体版本（读自 name 表） | `Version 2.005;addfeatures 5.0.0b21` |
| 版权声明（读自 name 表） | `© 2014-2025 Adobe (http://www.adobe.com/), with Reserved Font Name 'Source'.` |
| 字体许可（读自 name 表） | **SIL Open Font License 1.1**（`licenseURL: http://scripts.sil.org/OFL`） |
| 是否随发行包分发字体文件 | **否**。发行包与发行仓库内均不含任何 `.otf`/`.ttf` 文件，只含由它渲染出的位图数据 |

据此形成的说明与义务：

- 本项目**没有修改**该字体，也没有以「Source」为名发布任何衍生字体；OFL 对保留字体名（Reserved Font Name）的限制不因本项目触发。
- 发行物里不含字体软件本体，因此不产生「随包附带 OFL 全文」的分发义务；为便于核对，仍在本页写明字体名称、版本、版权与许可。
- OFL 1.1 不约束用该字体排版/渲染出的产物。本项目的产物是游戏字库位图，属渲染结果，不属 Font Software。
- 字体文件本身**不纳入**本仓库，也不纳入任何发行包（体积与许可范围都不适合随仓库走）。

> 若将来改用其它字体重新生成字库，必须同步更新本节的名称、版本、版权、许可四项，并重新过一遍实机回归。

## 三、构建与开发期工具

### 3.1 打补丁工具（`installer/`）

| 工具 | 用途 | 许可 | 是否随包分发 |
|---|---|---|---|
| Python | 运行时 | PSF License | 是（PyInstaller 打包时随附 `python3xx.dll`） |
| Tkinter / Tcl-Tk | 图形界面 | PSF License（Tcl/Tk 部分为 BSD 式） | 随 Python 打包 |
| xdelta3 3.2.2 | 差分编解码 | Apache-2.0 | 是，见第一节 |

### 3.2 汉化工坊（`workshop/`）

| 工具 | 用途 | 许可 | 是否随包分发 |
|---|---|---|---|
| PySide6 / Qt 6.12.0 | 图形界面 | **LGPL-3.0**（Qt 公司双许可中的开源一侧） | 是：工坊发行包 `ACE3_Workshop_v1.0.4.zip` 的 `_internal/PySide6/` 与 `_internal/shiboken6/` |
| Pillow（PIL） | 图像与图标处理 | MIT-CMU（HPND 式） | 同上，`_internal/PIL/` |
| NumPy 2.3.5 | 数值计算 | BSD-3-Clause | 同上，`_internal/numpy/`（包内自带 `numpy-2.3.5.dist-info/LICENSE.txt`） |
| psutil | 进程与系统信息 | BSD-3-Clause | 同上，`_internal/psutil/` |
| PyYAML | YAML 解析 | MIT | 同上，`_internal/yaml/` |
| charset-normalizer | 字符编码识别 | MIT | 同上，`_internal/charset_normalizer/` |

关于 PySide6 的说明：工坊以**动态链接**方式使用 PySide6/Qt（打包后为同目录下的独立 DLL），未修改 Qt 源码。若分发打包好的工坊 EXE，按 LGPL-3.0 需附许可文本、声明所用版本，并保持用户可用自己的兼容 Qt 库替换——PyInstaller 的一目录（`_internal/`）布局本身满足可替换性。源码形式分发时（本仓库即此形态）不产生额外义务。

许可履行方式（已按实际选择执行）：`ACE3_Workshop_v1.0.4.zip` 内附 `LICENSES/` 目录，含 `LICENSE-Python-3.12.txt`、`LGPL-3.0.txt`、`GPL-3.0.txt`、`LICENSE-Pillow.txt`、`LICENSE-NumPy.txt`、`LICENSE-psutil.txt`、`LICENSE-PyYAML.txt`、`LICENSE-charset-normalizer.txt` 与一份中文 `README.txt` 说明。打包产物本身只保留了 NumPy 的 dist-info，其余组件的许可全文由该目录补齐。

版本判定依据：`_internal/PySide6/Qt6Core.dll` 的文件版本资源为 `6.12.0.0`（ProductName=`Qt6`）；`numpy-2.3.5.dist-info` 目录名给出 NumPy 版本。Pillow、psutil、PyYAML、charset-normalizer 在打包产物内未保留可读版本资源或 dist-info，本页不写推测版本号。

注：psutil、PyYAML、charset-normalizer 在 `workshop/` 源码中没有被直接 import，是打包闭包连带收进的运行时依赖；既然随包分发，一并附许可。


### 3.3 仅开发期使用、不随包分发

| 工具 | 用途 | 许可 |
|---|---|---|
| Git for Windows（MinGit） | 版本管理 | GPL-2.0 |
| PyInstaller | 打包 EXE | GPL-2.0 附例外条款（打包产物不受 GPL 约束） |

## 四、游戏原始内容

游戏《Another Century's Episode 3 THE FINAL》的程序、文本、图像、音频、字体及其他素材，权利归其权利人所有。本项目不主张这些内容的任何权利，也不通过本项目授予他人使用、复制或传播游戏本体的许可。

## 五、维护规则

- 新增依赖必须在本页登记：名称、版本、用途、许可、分发方式、核实日期。
- 无法核实的许可写「待确认」，不得填写推测值或「应该没问题」。
- 不得把第三方代码并入 MIT 范围而不作声明，见[许可范围](LICENSE-SCOPE.md)。
- 发现许可冲突时先标记为阻塞项，不擅自发布。
