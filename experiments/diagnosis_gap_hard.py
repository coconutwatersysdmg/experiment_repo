# -*- coding: utf-8 -*-
"""
DASFAA 2027 补充实验 D：诊断反例的困难实例（gap 不再恒为 0）

要回答的问题
    原有 results_diagnosis.txt 报 "gap = 0.0%" —— 全是在极小随机实例上得到的。
    本实验构造有结构的多表实例，看闭式 DFS 取首解的反例是否会明显大于最小加回集。

机制
    closure_check_exact 按 sorted(I \\ cand) 的顺序返回第一个找到的见证：
      - 若先遇到一条长链的根元组，返回 d 条元组的见证；
      - 而同一实例里末层元组可以单独加回，最小见证只有 1 条。
    这是"诊断输出选择的反例不是最小反例"，不是判定错，也不是 repair 距离上界错。

诚实口径
    - 判定仍与暴力 oracle 一致；只量化"首个反例"与"最小反例"的差距。
    - 小规模精确枚举；结论是存在性证据，不声称一般下界。

运行：python experiments/diagnosis_gap_hard.py
无第三方依赖。
"""
from __future__ import annotations

import itertools
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fk_closure as F

OUT = []


def out(s=""):
    print(s)
    OUT.append(str(s))


def save():
    path = "experiments/results_gap_hard.txt"
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(OUT) + "\n")
    print("\n[saved] " + path)


def min_add_for_node(schema, inst, cand, node, removed):
    """含 node 的最小一致加回集大小（暴力枚举）。"""
    others = [r for r in removed if r != node]
    for k in range(0, len(others) + 1):
        for combo in itertools.combinations(others, k):
            if F.consistent(schema, cand | {node} | set(combo)):
                return k + 1
    return None


def make_instance(d, n_short=1, seed=0):
    """d 层无环链（长链）+ 若干末层单元组（短见证），候选为空。"""
    schema = F.schema_chain(d)
    inst = set()
    for i in range(d):
        inst.add(("T%d" % i, (0, 0, 0)))                 # 长链：见证长度 d
    for k in range(1, 1 + n_short):
        inst.add(("T%d" % (d - 1), (k, k, 0)))           # 末层：见证长度 1
    return schema, frozenset(inst), frozenset()


def exp_chain():
    out("-" * 96)
    out("D1  困难实例：DFS 首解反例 vs 最小反例（d 层链 + 一个末层可单加元组）")
    out("-" * 96)
    out("%8s %10s %14s %16s %10s %12s" %
        ("depth d", "|I|", "DFS 首解", "最小反例", "gap", "oracle 一致"))
    rows = []
    for d in (2, 3, 4, 5, 6, 8, 10):
        schema, inst, cand = make_instance(d)
        removed = sorted(inst - cand)
        ok, w = F.closure_check_exact(schema, inst, cand)
        oracle = F.oracle_check(schema, inst, cand)
        first = len(w[1]) if w else 0
        mn = min(min_add_for_node(schema, inst, cand, node, removed) or 10 ** 6
                 for node in removed)
        rows.append((d, len(inst), first, mn, first - mn))
        out("%8d %10d %14d %16d %10d %12s" %
            (d, len(inst), first, mn, first - mn, "yes" if ok == oracle else "NO"))
    out()
    out("  判定本身 100% 与 oracle 一致；但首个反例比最小反例大 d-1 条。")
    out("  原文 'gap=0%' 只成立于其随机小实例，不能写成诊断输出的普遍性质。")
    out()
    return rows


def exp_random(n_cases=300, seed=20261118):
    out("-" * 96)
    out("D2  随机混合链：gap>0 的频率与分布（%d 例，d=6）" % n_cases)
    out("-" * 96)
    rng = random.Random(seed)
    gaps = []
    agree = 0
    for _ in range(n_cases):
        d = 6
        schema, inst, cand = make_instance(d, n_short=rng.randint(1, 3))
        # 随机再删掉链上若干层，制造不同形状
        inst = frozenset(r for r in inst if rng.random() < 0.85)
        if len(inst - cand) > 10 or len(inst - cand) < 2:
            continue
        removed = sorted(inst - cand)
        ok, w = F.closure_check_exact(schema, inst, cand)
        oracle = F.oracle_check(schema, inst, cand)
        agree += (ok == oracle)
        if ok or w is None:
            continue
        first = len(w[1])
        mn = min((min_add_for_node(schema, inst, cand, node, removed) or 10 ** 6)
                 for node in removed)
        gaps.append(first - mn)
    if gaps:
        pos = sum(1 for g in gaps if g > 0)
        out("%14s %12d" % ("有效样本", len(gaps)))
        out("%14s %12d (%.1f%%)" % ("gap > 0", pos, 100.0 * pos / len(gaps)))
        out("%14s %12.2f" % ("平均 gap", sum(gaps) / len(gaps)))
        out("%14s %12d" % ("最大 gap", max(gaps)))
        out("%14s %12d/%d" % ("判定一致", agree, n_cases))
    out()
    out("  注：这里的 gap 是'首个反例 - 最小反例'，不是 repair 距离上界的紧度。")
    out()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    out("=" * 96)
    out("DASFAA 2027 补充实验 D：诊断反例困难实例（2026-11-18）")
    out("=" * 96)
    out("目的：检验'gap 恒为 0'是否是小实例假象。")
    out()
    exp_chain()
    exp_random()
    save()


if __name__ == "__main__":
    main()
