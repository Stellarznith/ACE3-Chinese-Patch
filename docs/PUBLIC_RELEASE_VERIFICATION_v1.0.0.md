# v1.0.0 公开发布验收记录

- 仓库 URL: https://github.com/Stellarznith/ACE3-Chinese-Patch
- Release URL: https://github.com/Stellarznith/ACE3-Chinese-Patch/releases/tag/v1.0.0
- 发布时间 (UTC): 2026-10-28T17:27:36Z
- 发布时间 (Asia/Shanghai): 2026-10-29 01:27:36 +08:00
- private: False / visibility: public
- default_branch: main
- Release id: 426918018 / draft: False / prerelease: False
- tag: refs/tags/v1.0.0 -> commit da5423665e3022b19cef1d38810ed961edf37240
- assets: 11 / 合计 496699035 字节

| 附件 | 字节 | SHA-256（GitHub 服务端 digest） |
| --- | --- | --- |
| `ACE3_CN_Patcher.zip` | 11682160 | `383cf7c90a7b6bdb10b84fd1b454ce75b08cd96beed0ce219ff50d001a8a0e92` |
| `ACE3_CN_v1.0.0_full.json` | 776 | `e022774577e3c898fb433c44dfd9d6804e96dfd702414eeae1dfcc99cf7032aa` |
| `ACE3_CN_v1.0.0_full.xdelta` | 426654389 | `74944d8127bc9c02e863a813f22da089dc508f8df4c2bb53bf397e1e314a2acd` |
| `ACE3_CN_v1.0.0_readme.txt` | 6966 | `0094315cdcd03b5b537d4ccc5acdbdbd314adc54b940e9a960b5294c21c51574` |
| `ACE3_Workshop_v1.0.4.zip` | 57946402 | `e1957bddc51c19493b8e5ac0799ce9b0de62621df9dedadbb7a3ede15d1bfb60` |
| `LICENSE-xdelta3.txt` | 9156 | `6cf63d87586c7c25c3ab8e62eef5c75bbaa982b0a6f9c00c59e2720255aac9ec` |
| `NOTICE-xdelta3.txt` | 929 | `1f3062dda7f369a5da75ad59972f512b3a06c358d3570cc825ca2b06c44a13ed` |
| `SHA256SUMS.txt` | 863 | `6d345ff917ff45282063fbd972d455c862490afde336acb0034ad99dffa86384` |
| `patch_manifest.json` | 8274 | `5889aa627adc8b8f7c2c5d3117fd6e08ea5b7610538c015522e882c99b9fb383` |
| `xdelta3.exe` | 340992 | `732980d52f9220dc81817ccdd0c295eadce8a0048a24c2a771a73f791ac824e9` |
| `xdelta3decode.exe` | 48128 | `67f84255bd009a65b1252784f8b3ebc72b5907766bc366c571e8b85fa7002a06` |

## 匿名（未登录）访问核验

- 仓库主页 HTTP 200，无登录墙，无 Private 标记；Issues、Releases、LICENSE、INSTALL 入口均可见
- Release 页面 HTTP 200，11 个附件全部列在页面上
- 每个附件匿名 HEAD 均 HTTP 200，Content-Length 与登记值一致
- 9 个较小附件匿名完整下载并重算 SHA-256，与服务端 digest 全部相等
- 2 个大附件（`ACE3_CN_v1.0.0_full.xdelta` 426,654,389 B、`ACE3_Workshop_v1.0.4.zip` 57,946,402 B）首尾各 64 KiB 抽样逐字节与本地文件一致，且服务端 digest 与本地文件 SHA-256 全等
- `SHA256SUMS.txt` 匿名取回，10 条逐条对照：9 条同名命中；1 条为 `使用说明.txt` → 附件实际名 `ACE3_CN_v1.0.0_readme.txt`，哈希 `0094315c…` 一致

## 内容未变化

- 补丁 SHA-256 `74944d8127bc9c02e863a813f22da089dc508f8df4c2bb53bf397e1e314a2acd`（服务端 digest == 本地文件重算）
- 正式中文版 ISO MD5 `47a9c9e3c18a823ef311ff7ea8150577` / SHA-256 `368f4a7f6c5dafcb14d822d8fb3a44585c1ab23a32bfad11d84b2eea1635bc12`
- 本次公开发布未重建补丁、未替换附件、未修改仓库内容（main 仍为 `da5423665e3022b19cef1d38810ed961edf37240`）
