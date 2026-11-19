# -*- coding: utf-8 -*-
"""扩展验证实验（2026-11-16）
  实验 C：值域扩到 4-5，验证"父键重复时必须分支搜索"（补 fk_closure 实验 A）
  实验 D：Employee 深链的 |A(t)| 分布，支撑定理 4"有环退化到数据规模"（补实验 B）

复用 fk_closure.py 的判定函数，不改动已跑通的原型。
运行： python experiments/exp_witness.py
"""
from __future__ import annotations

import random
import statistics
import sys

from fk_closure import (
    MAX_ORACLE_REMOVED,
    ancestors,
    closure_check_exact,
    closure_check_greedy,
    parent_index,
    rand_candidate,
    rand_instance,
    schema_ab_cycle,
    schema_chain,
    schema_self,
    schema_two_table,
    unit_check,
    oracle_check,
)


def exp_C(trials=600, seed=20261117):
    """值域 4-5 下，三种判定与暴力裁判的一致率。"""
    rng = random.Random(seed)
    rows = []
    configs = [
        ("无环两表 域4", schema_two_table(), {"P": 4, "C": 5}, 4),
        ("无环两表 域5", schema_two_table(), {"P": 5, "C": 6}, 5),
        ("互引用 域4", schema_ab_cycle(), {"A": 4, "B": 4}, 4),
        ("互引用 域5", schema_ab_cycle(), {"A": 5, "B": 5}, 5),
        ("无环三表链 域3", schema_chain(3), {"T0": 3, "T1": 4, "T2": 3}, 3),
    ]
    for tag, schema, sizes, dom in configs:
        n = c_exact = c_greedy = c_unit = 0
        greedy_wrong = unit_miss = attempts = 0
        while n < trials and attempts < 300000:
            attempts += 1
            inst = rand_instance(rng, schema, sizes, dom)
            if not (2 <= len(inst) <= MAX_ORACLE_REMOVED + 1):
                continue
            cand = rand_candidate(rng, schema, inst)
            if cand is None:
                continue
            o = oracle_check(schema, inst, cand)
            e, _ = closure_check_exact(schema, inst, cand)
            g = closure_check_greedy(schema, inst, cand)
            u = unit_check(schema, inst, cand)
            n += 1
            c_exact += (e == o)
            c_greedy += (g == o)
            c_unit += (u == o)
            if g != o:
                greedy_wrong += 1
            if u and not o:
                unit_miss += 1
        rows.append((tag, n, c_exact, c_greedy, c_unit, greedy_wrong, unit_miss))
    return rows


def employee_chain(n):
    """Employee(empno,mgr) 单链：0->1->...->n-1，根自引用。empno 唯一。"""
    return frozenset(("E", (i, i + 1 if i < n - 1 else i, 0)) for i in range(n))


def employee_tree(depth, fanout):
    """Employee 树：根自引用，其余 mgr 指向父。用于看扇出是否影响 |A(t)|。"""
    n = sum(fanout ** i for i in range(depth))
    return frozenset(
        ("E", (i, 0 if i == 0 else (i - 1) // fanout, 0)) for i in range(n)
    )


def chain_acyclic(depth, width):
    """无环外键链，每表 width 条。用于看数据量增大时 |A(t)| 是否恒定。"""
    return frozenset(
        ("T%d" % i, (j, j, 0)) for i in range(depth) for j in range(width)
    )


def witness_stats(schema, inst):
    """返回 |A(t)| 的 (min, mean, p50, p90, max)。"""
    pm = parent_index(schema, inst)
    sizes = sorted(len(ancestors(pm, node)) for node in inst)
    n = len(sizes)
    p = lambda q: sizes[min(n - 1, int(q * n))]
    return sizes[0], statistics.mean(sizes), p(0.5), p(0.9), sizes[-1]


def exp_D():
    rows = []
    # (a) 有环：Employee 单链，长度从 10 到 500
    for n in (10, 20, 50, 100, 200, 500):
        inst = employee_chain(n)
        mn, av, p50, p90, mx = witness_stats(schema_self(), inst)
        rows.append(("自引用链 n=%d" % n, len(inst), mn, av, p50, p90, mx))
    # (b) 有环但受控：Employee 树，深度固定、扇出变大
    for b in (2, 3, 5, 10):
        inst = employee_tree(4, b)
        mn, av, p50, p90, mx = witness_stats(schema_self(), inst)
        rows.append(("自引用树 d=4 b=%d" % b, len(inst), mn, av, p50, p90, mx))
    # (c) 无环：外键链深度固定、每表数据量变大
    for w in (5, 10, 25, 50, 100):
        inst = chain_acyclic(8, w)
        mn, av, p50, p90, mx = witness_stats(schema_chain(8), inst)
        rows.append(("无环链 d=8 w=%d" % w, len(inst), mn, av, p50, p90, mx))
    return rows


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    lines = []

    def out(s=""):
        print(s)
        lines.append(s)

    out("=" * 96)
    out("扩展验证实验 exp_witness —— 运行报告（2026-11-16）")
    out("=" * 96)
    out()

    out("-" * 96)
    out("实验 C：值域 4-5，三种判定 vs 暴力裁判（各 600 例）")
    out("-" * 96)
    out("%-16s %5s %10s %10s %10s %10s %10s" %
        ("模式", "样本", "精确成组", "首次选择", "单元见证", "首次错", "单元漏判"))
    for tag, n, ce, cg, cu, gw, um in exp_C():
        out("%-16s %5d %9.1f%% %9.1f%% %9.1f%% %10d %10d" %
            (tag, n, 100.0 * ce / n, 100.0 * cg / n, 100.0 * cu / n, gw, um))
    out()
    out("  精确成组 = 分支搜索（本文方案）；首次选择 = 不回溯；单元见证 = 单表算法直搬")
    out()

    out("-" * 96)
    out("实验 D：|A(t)| 分布（min / mean / p50 / p90 / max）")
    out("-" * 96)
    out("%-20s %7s %6s %8s %6s %6s %6s" %
        ("实例", "|I|", "min", "mean", "p50", "p90", "max"))
    for tag, ni, mn, av, p50, p90, mx in exp_D():
        out("%-20s %7d %6d %8.1f %6d %6d %6d" %
            (tag, ni, mn, av, p50, p90, mx))
    out()
    out("  (a) 自引用链：|A(t)| 随 n 增长 -> 有环退化到数据规模")
    out("  (b) 自引用树：深度固定，扇出增大 -> max 仍等于深度")
    out("  (c) 无环链：数据量涨 20 倍，max 恒为 8 -> 模式常数")
    out()

    path = "experiments/results_witness.txt"
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("[已保存] %s" % path)


if __name__ == "__main__":
    main()
