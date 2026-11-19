# -*- coding: utf-8 -*-
"""审计实验（第 6 条）：公开脏数据上，"名义修复"里有多少其实不合法。

数据：HoloClean Flights（experiments/data/flight.csv，161 行 / 7 个航班）
模式：F(flight, sched, act_dept, gate, sched_arr, act_arr, arr_gate)
      FD: flight -> 其余 6 列；flight 为主键，满足 BCNF（第一篇设定）

做法：
  1. 用几种常见启发式生成"候选修复"（都是原表的子集）
  2. 用 fk_closure 的检查器审：一致性 + 极大性
  3. 统计不合法 / 非极大的比例，并给出可核验的反例

生成策略：
  G0 原始表            —— 应判不一致
  G1 每组保留第一条     —— 常见"去重"式清洗
  G2 每组保留多数向量行 —— frequency heuristic（保留与逐列多数最接近的原有行）
  G3 删除所有冲突组     —— 过度删除（宁可错删）
  G4 随机保留 50%       —— 1000 次，用于给出"比例"的分布

运行： python experiments/audit_repair.py
"""
from __future__ import annotations

import csv
import random
import sys
from collections import Counter, defaultdict

from fk_closure import (
    Rel,
    Schema,
    by_rel,
    closure_check_exact,
    consistent,
    fd_ok,
    fk_ok,
)

VALUE_ATTRS = ["sched", "act_dept", "gate", "sched_arr", "act_arr", "arr_gate"]
ATTRS = ["flight"] + VALUE_ATTRS
SRC_COLS = {
    "flight": "flight",
    "sched": "scheduled_dept",
    "act_dept": "actual_dept",
    "gate": "dept_gate",
    "sched_arr": "scheduled_arrival",
    "act_arr": "actual_arrival",
    "arr_gate": "arrival_gate",
}


def load_flights(path="experiments/data/flight.csv"):
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    inst = set()
    for r in rows:
        t = tuple((r[SRC_COLS[a]] or "").strip() for a in ATTRS)
        inst.add(("F", t))
    inst = frozenset(inst)
    fds = [(("flight",), a) for a in VALUE_ATTRS]
    schema = Schema([Rel("F", ATTRS, ["flight"], fds)], [])
    return schema, inst


def groups(inst):
    d = defaultdict(list)
    for _, t in inst:
        d[t[0]].append(t)
    return d


def g_all(inst):
    return inst


def g_first(inst):
    out = set()
    for f, ts in groups(inst).items():
        out.add(("F", sorted(ts)[0]))
    return frozenset(out)


def g_majority(inst):
    """逐列取多数，保留与该多数向量匹配列数最多的原有行（并列取字典序最小）。"""
    out = set()
    for f, ts in groups(inst).items():
        maj = []
        for j in range(1, len(ATTRS)):
            vals = Counter(t[j] for t in ts if t[j])
            maj.append(vals.most_common(1)[0][0] if vals else "")
        scored = [(sum(t[j + 1] == maj[j] for j in range(len(maj))), t) for t in ts]
        best = max(s[0] for s in scored)
        pick = min(t for s, t in scored if s == best)
        out.add(("F", pick))
    return frozenset(out)


def g_delete_conflicting(inst):
    """凡出现冲突的航班，整组删光。"""
    out = set()
    for f, ts in groups(inst).items():
        conflict = any(
            len({t[j] for t in ts if t[j]}) > 1 for j in range(1, len(ATTRS))
        )
        if not conflict:
            for t in ts:
                out.add(("F", t))
    return frozenset(out)


def g_random(inst, rng, p=0.5):
    return frozenset(("F", t) for _, t in inst if rng.random() < p)


def audit(schema, inst, cand):
    """返回 (一致?, 极大?, 可加回条数, 见证大小, 冲突列数)。"""
    ok_fd = fd_ok(schema, cand)
    ok_fk = fk_ok(schema, cand)
    consistent_flag = ok_fd and ok_fk
    removed = inst - cand
    addable = 0
    max_w = 0
    if consistent_flag and removed:
        for node in sorted(removed):
            is_rep, w = closure_check_exact(schema, inst, cand)
            if not is_rep:
                # 逐个测太慢；用短路：只要整体非极大，再精确定位可加回条数
                pass
            break
        # 精确统计：对每个被删元组单独问"能否整组加回"
        from fk_closure import exists_superset_containing, parent_lookup
        look = parent_lookup(schema, inst)
        for node in sorted(removed):
            can, add = exists_superset_containing(schema, inst, cand, node, look)
            if can:
                addable += 1
                max_w = max(max_w, len(add))
        maximal_flag = (addable == 0)
    else:
        maximal_flag = False
    # 冲突列数：按航班分组统计（每个航班内部有多少列取值冲突）
    conflict_cols = 0
    for f, ts in groups(cand).items():
        for j in range(1, len(ATTRS)):
            if len({t[j] for t in ts if t[j]}) > 1:
                conflict_cols += 1
    return consistent_flag, maximal_flag, addable, max_w, conflict_cols


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    schema, inst = load_flights()
    lines = []

    def out(s=""):
        print(s)
        lines.append(s)

    out("=" * 90)
    out("审计实验 —— HoloClean Flights（161 行 / 7 航班 / 6 个 FD）")
    out("=" * 90)
    out("模式 F(flight, sched, act_dept, gate, sched_arr, act_arr, arr_gate)")
    out("FD: flight -> 其余 6 列（flight 为主键，BCNF）")
    out()

    out("-" * 90)
    out("表 1：常见启发式生成的候选修复，审计结果")
    out("-" * 90)
    out("%-22s %7s %8s %8s %8s %8s %8s" %
        ("生成策略", "|I'|", "一致", "极大", "可加回", "冲突列", "判定"))
    named = [
        ("G0 原始表", g_all(inst)),
        ("G1 每组保留首行", g_first(inst)),
        ("G2 每组保留多数行", g_majority(inst)),
        ("G3 删除所有冲突组", g_delete_conflicting(inst)),
    ]
    for tag, cand in named:
        c, m, add, mw, cc = audit(schema, inst, cand)
        verdict = "合法修复" if c and m else ("不一致" if not c else "非极大")
        out("%-22s %7d %8s %8s %8d %8d %8s" %
            (tag, len(cand), "是" if c else "否", "是" if m else "否", add, cc, verdict))
    out()

    out("-" * 90)
    out("表 2：增量式清洗 —— 从合法修复出发随机回加被删元组（1000 次）")
    out("-" * 90)
    out("  场景：清洗系统先产出合法修复，后续增量地放回若干元组（未复核）")
    rng = random.Random(20261116)
    base = g_majority(inst)          # 从合法修复出发
    removed = sorted(inst - base)
    n = n_ok = n_inc = n_nmax = 0
    add_sum = 0
    for _ in range(1000):
        k = rng.randint(1, min(5, len(removed)))
        cand = frozenset(set(base) | set(rng.sample(removed, k)))
        c, m, add, mw, cc = audit(schema, inst, cand)
        n += 1
        if c and m:
            n_ok += 1
        elif not c:
            n_inc += 1
        else:   # 一致但非极大：这一格我们的检查器能抓、单元检查抓不到（多表时）
            n_nmax += 1
            add_sum += add
    out("  样本数                       : %d" % n)
    out("  仍为合法子集修复             : %d (%.1f%%)" % (n_ok, 100.0 * n_ok / n))
    out("  不一致（放回引入 FD 违规）   : %d (%.1f%%)" % (n_inc, 100.0 * n_inc / n))
    out("  非极大（一致但删多了/放错）  : %d (%.1f%%)" % (n_nmax, 100.0 * n_nmax / n))
    out("  非极大样本平均可加回         : %.1f 条" %
        (add_sum / n_nmax if n_nmax else 0))
    out()
    out("  说明：此表回答'名义修复在增量更新后是否仍合法'——检查器的用武之地。")
    out()

    out("-" * 90)
    out("表 3：可核验反例（G3 过度删除）")
    out("-" * 90)
    cand = g_delete_conflicting(inst)
    from fk_closure import exists_superset_containing, parent_lookup
    look = parent_lookup(schema, inst)
    shown = 0
    for node in sorted(inst - cand):
        can, add = exists_superset_containing(schema, inst, cand, node, look)
        if can:
            out("  被删 %s" % (node[1],))
            out("  可整组加回 %d 条 -> %s" % (len(add), sorted(a[1] for a in add)))
            shown += 1
            if shown >= 3:
                break
    out()

    path = "experiments/results_audit.txt"
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("[已保存] %s" % path)


if __name__ == "__main__":
    main()
