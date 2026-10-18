# manifests —— 校验清单

每次构建记录一份 JSON，与 Release 一同发布。字段：

```json
{
  "version": "1.0.0",
  "built_at": "2026-10-18T00:00:00+08:00",
  "commit": "<仓库 commit>",
  "source": { "file": "original.iso", "size": 0, "sha256": "" },
  "steps": [
    { "name": "extract", "out_sha256": "" }
  ],
  "output": { "file": "ace3-zh-1.0.0.iso", "size": 0, "sha256": "" },
  "patch":  { "file": "ace3-zh-1.0.0.xdelta", "size": 0, "sha256": "" },
  "verified_screens": ["主菜单", "读档列表", "..."],
  "unverified": ["实机", "全路线通关"]
}
```

## 为什么留这个

它是**事后复盘与信任建立**的唯一依据：玩家可以用它核对自己下载到的补丁是否被篡改，
维护者可以在出问题时判断"到底哪一步变了"。

校验值只能证明文件一致，**不能**证明文件安全或来源身份——这点在 README 里也写明了。