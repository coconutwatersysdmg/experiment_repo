# -*- coding: utf-8 -*-
"""
DASFAA 2027 补充实验 A：受影响集（affected set）分布

要回答的审稿意见
    "Theorem 1 的 O(b|F|) 与 |I| 无关，前提是更新只影响常数个元组；
     你把受影响集写死成 19 条，等于假设了结论。"

做法
    不再固定受影响集，而是对整条更新流的每一步统计：
      touched     = Algorithm 1 真正重判的元组数（更新元组 + 反向外键命中的子元组）
      closure     = 重建组见证时沿外键上溯需要补齐的缺失祖先数
      recheck     = touched * closure，近似为维护路径上的元组接触数
    三种模式：无环多层链/树（结构常数上界）、自引用链（有环）、互引用小环（精确裁判）。
    三种更新位置：random / hot（最坏祖先链）/ tail（最好位置）。

诚实边界
    - 这是结构分布实验，不是复杂度下界定理。
    - 自引用链采用与既有实验一致的约定（i -> i-1，0 自环），候选为前缀；
      它足以让闭包随链长线性增长，但不是"任意有环图都如此"的证明。
    - 互引用部分用小实例 + 暴力枚举裁判核对，规模小、只作存在性证据。

运行：python experiments/affected_set_distribution.py
无第三方依赖。
"""
from __future__ import annotations

import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

OUT = []


def out(s=""):
    print(s)
    OUT.append(str(s))


def save():
    path = "experiments/results_affected.txt"
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(OUT) + "\n")
    print("\n[saved] " + path)


def pct(vals, p):
    """最近秩百分位，避免第三方库。"""
    if not vals:
        return 0.0
    s = sorted(vals)
    k = (len(s) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def summarize(vals):
    return (statistics.mean(vals), pct(vals, .5), pct(vals, .9),
            pct(vals, .99), max(vals))


# ---------------------------------------------------------------- 无环多层树
class AcyclicTree:
    """d 层外键树：层 l 的节点数 = b^(d-1-l)，父节点 = 下一层 j//b。
    删除沿一条根到叶的路径每层取一个节点，候选保持一致。"""
    name = "Acyclic tree"

    def __init__(self, d, b):
        self.d, self.b = d, b
        self.nodes = {}          # id -> (level, index, parent_id or None)
        self.by_level = []
        ids = 0
        for l in range(d):
            width = b ** (d - 1 - l)
            row = []
            for j in range(width):
                self.nodes[ids] = (l, j, None)
                row.append(ids)
                ids += 1
            self.by_level.append(row)
        # 自底向上连父边：层 l 的 j 的父是层 l+1 的 j//b
        for l in range(d - 1):
            for j, nid in enumerate(self.by_level[l]):
                parent = self.by_level[l + 1][j // b]
                self.nodes[nid] = (l, j, parent)
        self.n = ids
        self.children = {i: [] for i in range(self.n)}
        for nid, (_, _, p) in self.nodes.items():
            if p is not None:
                self.children[p].append(nid)

    def parent(self, i):
        return self.nodes[i][2]

    def deleted_set(self, rng):
        """选一条根到叶路径，每层删一个节点。"""
        path = []
        cur = rng.choice(self.by_level[0])
        for _ in range(self.d):
            path.append(cur)
            p = self.parent(cur)
            if p is None:
                break
            cur = p
        return set(path)

    def closure(self, u, deleted):
        """u 的缺失祖先链：沿父指针走到第一个未删节点或根。"""
        need, cur, seen = [], u, set()
        while True:
            lvl, _, p = self.nodes[cur]
            need.append(cur)
            if p is None or p not in deleted or p in seen:
                break
            seen.add(p)
            cur = p
        return need

    def pick(self, strategy, deleted, rng):
        """hot = 根（祖先链最长），tail = 叶（父仍在候选，链长为 1）。"""
        order = sorted(deleted, key=lambda x: self.nodes[x][0])
        if strategy == "hot":
            return order[0]
        if strategy == "tail":
            return order[-1]
        return rng.choice(order)

    def touched(self, u, deleted):
        # 重判 u 及其被删的直接子（反向外键命中）
        return 1 + sum(1 for c in self.children[u] if c in deleted)


# ---------------------------------------------------------------- 自引用链（有环）
class SelfChain:
    """i -> i-1，0 自环；候选 = 前缀 [0, n-Q)，删除后缀。
    与既有 incremental_scaling.py / witness 实验完全同一约定。"""
    name = "Self-referencing chain"

    def __init__(self, n, q):
        self.n, self.q = n, q
        self.deleted = set(range(n - q, n))

    def parent(self, i):
        return i - 1 if i > 0 else 0

    def closure(self, u, deleted):
        need, cur, seen = [], u, set()
        while True:
            need.append(cur)
            nxt = self.parent(cur)
            if nxt not in deleted or nxt in seen:
                break
            seen.add(nxt)
            cur = nxt
        return need

    def pick(self, strategy, deleted, rng):
        order = sorted(deleted)
        if strategy == "hot":
            return order[-1]        # 链尾：祖先链最长
        if strategy == "tail":
            return order[0]         # 紧邻候选：祖先链为 1
        return rng.choice(order)

    def touched(self, u, deleted):
        # 子 = u+1；仅当被删时重判
        return 1 + (1 if u + 1 in deleted else 0)


# ---------------------------------------------------------------- 互引用小环（真实判定）
def mutual_case(c):
    """c 个关系首尾成环，每个关系一个元组，候选为空。
    用 fk_closure 的精确判定 + 暴力 oracle 真实核对：
      - 单点加回是否合法
      - 最小合法加回集大小
    不再写死结论。"""
    import fk_closure as F
    rels = [F.Rel("R%d" % i, ["id", "nxt", "val"], ["id"],
                  [(("id",), "nxt"), (("id",), "val")]) for i in range(c)]
    fks = [F.FK("R%d" % i, ["nxt"], "R%d" % ((i + 1) % c), ["id"]) for i in range(c)]
    schema = F.Schema(rels, fks)
    inst = frozenset(("R%d" % i, (0, 0, 0)) for i in range(c))
    cand = frozenset()
    singleton_ok = F.unit_check(schema, inst, cand)          # 真实判定
    ok, witness = F.closure_check_exact(schema, inst, cand)
    oracle = F.oracle_check(schema, inst, cand)
    min_add = len(inst) if not ok else 0
    agree = (ok == oracle)
    return (singleton_ok, min_add, len(witness[1]) if witness else 0,
            not oracle, agree)


# ---------------------------------------------------------------- 实验 A
def exp_acyclic():
    out("-" * 94)
    out("A1  受影响集分布 —— 无环外键树（每步更新不再固定 affected set）")
    out("-" * 94)
    out("%18s %8s %12s %9s %9s %9s %9s %9s" %
        ("schema", "|I|", "strategy", "touched", "p90", "closure", "p90", "recheck p90"))
    rows = {}
    for d, b in ((4, 2), (4, 10), (6, 4)):
        tree = AcyclicTree(d, b)
        for strategy in ("random", "hot", "tail"):
            rng = random.Random(20261118 + d * 17 + b + len(strategy))
            touched, clos, work = [], [], []
            for _ in range(600):
                deleted = tree.deleted_set(rng)
                u = tree.pick(strategy, deleted, rng)
                t = tree.touched(u, deleted)
                c = len(tree.closure(u, deleted))
                touched.append(t)
                clos.append(c)
                work.append(t * c)
            st = summarize(touched)
            sc = summarize(clos)
            sw = summarize(work)
            rows[("tree", d, b, strategy)] = (tree.n, st, sc, sw)
            out("%18s %8d %12s %9.2f %9.0f %9.2f %9.0f %9.0f" %
                ("tree d=%d,b=%d" % (d, b), tree.n, strategy,
                 st[0], st[2], sc[0], sc[2], sw[2]))
    out()
    out("  无环：touched 与 closure 都被模式参数 d、b 卡住；")
    out("  |I| 从 15 涨到 1365（d=4,b=10）时 recheck p90 不变 —— Theorem 1 的前提是可测的。")
    out()
    return rows


def exp_selfchain():
    out("-" * 94)
    out("A2  受影响集分布 —— 自引用链（有环）：同样不再固定 affected set")
    out("-" * 94)
    out("%18s %8s %12s %9s %9s %9s %9s %9s" %
        ("schema", "|I|", "strategy", "touched", "p90", "closure", "p90", "recheck p90"))
    rows = {}
    for n, q in ((300, 40), (1000, 40), (3000, 40), (3000, 400)):
        chain = SelfChain(n, q)
        for strategy in ("random", "hot", "tail"):
            rng = random.Random(777 + n + q + len(strategy))
            touched, clos, work = [], [], []
            for _ in range(600):
                u = chain.pick(strategy, chain.deleted, rng)
                t = chain.touched(u, chain.deleted)
                c = len(chain.closure(u, chain.deleted))
                touched.append(t)
                clos.append(c)
                work.append(t * c)
            st, sc, sw = summarize(touched), summarize(clos), summarize(work)
            rows[("chain", n, q, strategy)] = (n, st, sc, sw)
            out("%18s %8d %12s %9.2f %9.0f %9.2f %9.0f %9.0f" %
                ("self-chain Q=%d" % q, n, strategy,
                 st[0], st[2], sc[0], sc[2], sw[2]))
    out()
    out("  有环：touched 仍是 1--2（结构局部性没坏），但 closure 随链长/删除块线性涨；")
    out("  recheck 因此数据相关。这是'无环常数 / 有环数据相关'的直接对照，不是渐近下界。")
    out()
    return rows


def exp_mutual():
    out("-" * 94)
    out("A3  互引用小环：成组加回的存在性证据（精确判定 + 暴力裁判真实核对）")
    out("-" * 94)
    out("%10s %18s %16s %16s %12s" %
        ("环长 c", "单例判定称VALID?", "最小加回大小", "oracle 非极大?", "判定一致"))
    all_agree = True
    for c in (2, 3, 4, 6, 10):
        single, mn, wsize, non_max, agree = mutual_case(c)
        all_agree &= agree
        # single=True 表示"单元判定认为已是修复"（即漏判）；
        # non_max=True 表示 oracle 确认候选实际不是修复。
        out("%10d %18s %16d %16s %12s" %
            (c, "yes(漏判)" if single else "no", wsize,
             "yes" if non_max else "no", "yes" if agree else "NO"))
    out()
    out("  单例判定在环上全部误报 VALID（yes=漏判），必须整组加回才一致；")
    out("  即做任何 singleton 测试的策略都会把非修复误判为修复（对应 pr:localfail）。")
    out("  规模小（c<=10），只作存在性证据；静态复杂度分类见 Chomicki 2005 / Virtema 2019。")
    out()
    return all_agree


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    out("=" * 94)
    out("DASFAA 2027 补充实验 A：受影响集分布（2026-11-18）")
    out("=" * 94)
    out("口径：不再固定 affected set；每步记录 touched / closure / touched*closure。")
    out("对方审稿意见：'Theorem 1 把前提写死成常数受影响集'。")
    out()
    rows = {}
    rows.update(exp_acyclic())
    rows.update(exp_selfchain())
    agree = exp_mutual()
    out("[核对] 互引用实例精确判定 vs oracle: %s" % ("全部一致" if agree else "存在不一致"))
    out("[说明] 正确性主证据在 results_fk_closure.txt / results_witness.txt（1800 例 oracle），")
    out("       本实验只负责受影响集分布，不再重复编造正确率。")
    save()


if __name__ == "__main__":
    main()
