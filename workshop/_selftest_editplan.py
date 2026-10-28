"""自测：编辑计划管理器（队列 / 草稿 / 冲突 / 导入导出）。"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import env, search, editplan, codec  # noqa: E402

ISO = os.environ.get("ACE3_TEST_ISO") or r"PATH\TO\TEST.iso"
TMP = os.path.join(env.WORK_DIR, "draft_test")
os.makedirs(TMP, exist_ok=True)
for f in os.listdir(TMP):
    os.remove(os.path.join(TMP, f))

idx = search.open_index(search.index_path_for(ISO))
q = editplan.PlanQueue()
par = q.bind(ISO)
print("父盘绑定：", par["path"], par["md5"])

# 1) 从命中建计划
hit = idx.search("什么？", limit=1)[0]
print("命中：", hit["eid"], hit["tag"], hit["k"], hit["slot"], repr(hit["text"]))
item, missing = editplan.item_from_hit(hit, "何？")
print("  缺字：", missing, " 新字节：", item.new_payload.hex())
assert not missing
assert item.old_payload == bytes.fromhex(hit["seg_hex"])
print("  动作：", q.upsert(item))
assert len(q.items) == 1

# 2) 同槽再改 = 覆盖
item2, _ = editplan.item_from_hit(hit, "何为？")
print("  动作：", q.upsert(item2))
assert len(q.items) == 1
print("  历史：", q.items[0].history)

# 3) 再加几条不同槽
for t, txt in (("我方损失如何？", "我方损失怎样？"), ("卫星情况如何？", "卫星状况如何？")):
    h = idx.search(t, limit=1)[0]
    it, miss = editplan.item_from_hit(h, txt)
    print("  + %s -> %s  缺字=%s  动作=%s"
          % (t, txt, miss, q.upsert(it)))

print("统计：", q.stats())

# 4) 草稿保存 / 读取
p1 = q.save_draft(TMP)
print("草稿：", p1, os.path.getsize(p1), "字节")
q2 = editplan.PlanQueue()
info = q2.load_draft(p1)
print("  回读：", info)
assert info["items"] == len(q.items)
assert q2.parent["md5"] == par["md5"]

# 5) 草稿损坏 -> 不覆盖好档
with open(p1, "r", encoding="utf-8") as f:
    good = f.read()
with open(p1, "w", encoding="utf-8") as f:
    f.write("{ broken json")
q3 = editplan.PlanQueue()
q3.bind(ISO)
try:
    q3.load_draft(p1)
    print("  ✗ 损坏草稿竟然读成功")
except editplan.PlanError as exc:
    print("  ✓ 损坏草稿被拒：", str(exc).split("\n")[0])
q3.items = q.items[:]
p2 = q3.save_draft(TMP)
print("  新草稿：", p2)
bad = [n for n in os.listdir(TMP) if ".corrupt_" in n]
print("  ✓ 损坏旧档已留证：", bad)
assert bad, "损坏的旧草稿应保留为 .corrupt_*"

# 6) 草稿列表
for d in q3.list_drafts(TMP):
    print("  草稿:", d["name"], d["items"], "项", d["ok"])

# 7) 导出 / 导入
ej = os.path.join(TMP, "plan_export.json")
q3.export_json(ej)
q4 = editplan.PlanQueue()
q4.bind(ISO)
info = q4.import_json(ej)
print("导入：", info)
assert len(q4.items) == len(q3.items)

# 8) 父盘 MD5 变化检测
fake = editplan.parent_identity(ISO)
fake["md5"] = "0" * 32
q5 = editplan.PlanQueue()
q5.parent = fake
ok, why = q5.parent_ok(ISO)
print("父盘变化检测：", ok, why[:60])
assert not ok

print("\n全部用例通过")
