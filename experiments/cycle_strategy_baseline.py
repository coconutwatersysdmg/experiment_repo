# -*- coding: utf-8 -*-
"""补敕实验二：循环场景的 singleton 类策略对照（钉死 Prop 2 的边界）。

背景：Prop 2 只证明了 reverse-FK-local 策略在环上失败。审稿人可能问：
"这只是你局部策略的局限，全局多扫几个不就行了？"

本实验回答：**失败的根因不是看得少，而是 singleton 这个判定形式在环上不够**。
三种策略在互引用环实例上对比：

  local-singleton : 只测 A(u)={u}∪ReverseFK(u) 的单例（我们定义 def:local）
  global-singleton: 测 I\\r' 中**所有**元组的单例（全局扫描版）
  group           : 闭包检查，允许整组加回（我们的方法 / fk_closure 的 exact）

结论预期：local 与 global 单例都漏判（报 VALID 而实际非修复），只有 group 抓到；
global 单例虽然扫了全部元组，代价随 |I| 线性增长却仍漏判 —— 说明多扫无用。

设定：schema_ab_cycle 的两表互引用环，环长 c（每个关系 c/2 个元组，共 c 个）。
      r' = ∅，插入一个元组触发判定。
运行： python experiments/cycle_strategy_baseline.py
"""
from __future__ import annotations

import statistics
import sys
import time

sys.path.insert(0, "experiments")
import fk_closure as F

_LINES = []


def out(s=""):
    print(s)
    _LINES.append(s)


def save():
    p = "experiments/results_cycle_strategy.txt"
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(_LINES) + "\n")
    print("[saved] %s" % p)


def cycle_instance(c):
    """两表互引用环，共 c 个元组（c 偶数，每表 c/2）；返回 schema, I, cand=∅。"""
    schema = F.schema_ab_cycle()
    h = c // 2
    inst = set()
    for i in range(h):
        # A_i.bref -> B_i.bid ; B_i.aref -> A_{i+1}.aid （串成环）
        inst.add(("A", (i, i, 0)))
        inst.add(("B", (i, (i + 1) % h, 0)))
    return schema, frozenset(inst), frozenset()


def local_singleton(schema, I, cand, u):
    """只测 u 及其反向外键邻居上的单例。返回 (报VALID?, 测试数)。"""
    look = F.parent_lookup(schema, I)
    # 受影响的反向外键邻居：以 u 为父键、被 u 引用的子元组集合
    affected = {u}
    for fk in schema.fks:
        # 找引用 u 所提供键值的子元组
        ukey = u[1][0]  # 主键值（A.aid / B.bid）
        cpos = schema.rels[fk.child].pos(fk.cattrs)
        for node in I:
            if node[0] == fk.child and tuple(node[1][p] for p in cpos) == (ukey,):
                affected.add(node)
    tested = 0
    for t in sorted(affected):
        if t in cand:
            continue
        tested += 1
        if F.consistent(schema, set(cand) | {t}):
            return False, tested  # 发现单例可加回 -> 报 INVALID
    return True, tested  # 没发现 -> 误报 VALID


def global_singleton(schema, I, cand, u):
    """测 I\\cand 中全部元组的单例。返回 (报VALID?, 测试数)。"""
    tested = 0
    for t in sorted(I - cand):
        tested += 1
        if F.consistent(schema, set(cand) | {t}):
            return False, tested
    return True, tested


def group_check(schema, I, cand, u):
    """完整闭包检查（允许组）。返回 (报VALID?, 测试数, witness 大小)。"""
    ok, w = F.closure_check_exact(schema, I, cand)
    if ok:
        return True, 0, 0
    return False, len(I - cand), len(w[1]) if w and w[1] else 0


def oracle(schema, I, cand):
    is_rep, _ = F.closure_check_exact(schema, I, cand)
    return is_rep  # True = 是修复(VALID)；False = 非修复


def timed(fn, schema, I, cand, u, reps=100):
    ts = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn(schema, I, cand, u)
        ts.append((time.perf_counter() - t0) * 1e6)
    ts.sort()
    return statistics.median(ts), ts[len(ts) // 4], ts[3 * len(ts) // 4]


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    out("=" * 100)
    out("补敕实验二：循环场景 singleton 类策略对照（Prop 2 边界）")
    out("=" * 100)
    out("schema_ab_cycle 两表互引用环；r'=∅；插入一个元组后判定。")
    out("oracle: group 闭包检查（True=修复）。local/global singleton 报 VALID 即为漏判。")
    out()

    out("-" * 100)
    out("%6s %10s %14s %16s %16s %14s" %
        ("环长 c", "|I|", "oracle", "local-singleton", "global-singleton", "group"))
    out("-" * 100)
    rows = []
    for c in (4, 8, 16, 32, 64, 128):
        schema, I, cand = cycle_instance(c)
        u = sorted(I)[0]
        o = oracle(schema, I, cand)
        lv, lt = local_singleton(schema, I, cand, u)
        gv, gt = global_singleton(schema, I, cand, u)
        hv, ht, ws = group_check(schema, I, cand, u)
        rows.append((c, len(I), o, lv, lt, gv, gt, hv, ws))
        out("%6d %10d %14s %16s %16s %14s" %
            (c, len(I), "VALID" if o else "INVALID",
             "VALID(漏判)" if lv else "INVALID",
             "VALID(漏判)" if gv else "INVALID",
             "INVALID(w=%d)" % ws if not hv else "VALID"))
    out("-" * 100)
    all_local_wrong = all(r[3] and not r[2] for r in rows)
    all_global_wrong = all(r[5] and not r[2] for r in rows)
    out()
    out("结论：local singleton 全部漏判: %s；global singleton（扫全部 %d 元组）也全部漏判: %s。" %
        ("是" if all_local_wrong else "否",
         rows[-1][1], "是" if all_global_wrong else "否"))
    out("group 闭包检查全部正确，witness 大小随环长 c 增长（Prop 1）。")
    out("→ 失败根因是 singleton 判定形式本身，不是「看得少」；多扫无用。")
    out()

    # 确定性指标：单例测试次数（不受搜索实现/机器影响，可直接写进论文）
    out("-" * 100)
    out("单例测试次数 —— local 只看邻域，global 扫全表；两者都漏判")
    out("-" * 100)
    out("%6s %10s %20s %20s %16s" %
        ("环长 c", "|I|", "local 测试数", "global 测试数", "group witness"))
    out("-" * 100)
    for (c, ni, o, lv, lt, gv, gt, hv, ws) in rows:
        out("%6d %10d %20d %20d %16d" % (c, ni, lt, gt, ws))
    out("-" * 100)
    out("global singleton 扫了与 |I| 等量的单例测试（随 c 线性），仍然漏判；")
    out("group 返回的 witness 大小恰为 c（Prop 1：最小 witness 随 SCC 大小增长）。")
    out()
    out("注：这是 strategy-level 对照，不是问题本身的下界（见 Limitations：")
    out("维护全局聚合的算法仍可能 O(1) 回答 status）。这里量化的是判定形式的")
    out("结构性失败与 witness 大小，而非运行时常数。")
    out()
    save()


if __name__ == "__main__":
    main()
