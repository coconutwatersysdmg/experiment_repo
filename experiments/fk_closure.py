# -*- coding: utf-8 -*-
"""fk_closure.py —— 多表子集修复的"闭包见证"原型（v2）

日期：2026-11-16

用途
  1) 验证判定规则：I' 一致时，I' 是子集修复 <=> 对每个 t ∈ I\\I'，不存在
     一致超集 S ⊆ I 使 I' ∪ {t} ⊆ S。
  2) 对比三种做法与暴力枚举裁判的一致率：
     - 精确成组搜索（本文方案）
     - 唯一路径闭包（假设父键唯一时的简化版）
     - 单元见证（单表算法直接搬过来）
  3) 实测"见证大小"随模式深度/环长的增长（定理4的经验证据）

v2 相对 v1 的修正
  v1 把"父元组"当成唯一的一条（沿 parent_index 上溯）。真实脏数据里父表同一键值
  可能出现多条元组，加回子元组时可以任选其一，于是"闭包"不是一条路径，而是一棵
  选择树。v1 因此会误判（无环 97.8%、互引用 78.0%）。v2 改为对父候选做分支搜索。
"""

from __future__ import annotations

import random
import sys
from collections import defaultdict
from itertools import combinations, product

MAX_ORACLE_REMOVED = 12
MAX_SEARCH_STATES = 200000


# ---------------------------------------------------------------- 模式

class Rel:
    __slots__ = ("name", "attrs", "key", "fds")

    def __init__(self, name, attrs, key, fds=()):
        self.name = name
        self.attrs = tuple(attrs)
        self.key = tuple(key)
        self.fds = tuple((tuple(lhs), rhs) for lhs, rhs in fds)

    def pos(self, attrs):
        return [self.attrs.index(a) for a in attrs]


class FK:
    __slots__ = ("child", "cattrs", "parent", "pattrs")

    def __init__(self, child, cattrs, parent, pattrs):
        self.child = child
        self.cattrs = tuple(cattrs)
        self.parent = parent
        self.pattrs = tuple(pattrs)

    def __str__(self):
        return "%s[%s] -> %s[%s]" % (
            self.child, ",".join(self.cattrs), self.parent, ",".join(self.pattrs))


class Schema:
    def __init__(self, rels, fks):
        self.rels = {r.name: r for r in rels}
        self.fks = list(fks)


# ---------------------------------------------------------------- 一致性

def by_rel(inst):
    d = defaultdict(list)
    for r, t in inst:
        d[r].append(t)
    return d


def fd_ok(schema, inst):
    d = by_rel(inst)
    for name, tuples in d.items():
        rel = schema.rels[name]
        for lhs, rhs in rel.fds:
            lpos, rpos = rel.pos(lhs), rel.attrs.index(rhs)
            seen = {}
            for t in tuples:
                k = tuple(t[i] for i in lpos)
                if k in seen and seen[k] != t[rpos]:
                    return False
                seen[k] = t[rpos]
    return True


def fk_ok(schema, inst):
    d = by_rel(inst)
    for fk in schema.fks:
        ppos = schema.rels[fk.parent].pos(fk.pattrs)
        pkeys = {tuple(t[i] for i in ppos) for t in d.get(fk.parent, ())}
        cpos = schema.rels[fk.child].pos(fk.cattrs)
        for t in d.get(fk.child, ()):
            if tuple(t[i] for i in cpos) not in pkeys:
                return False
    return True


def consistent(schema, inst):
    return fd_ok(schema, inst) and fk_ok(schema, inst)


def dangling(schema, inst, only=None):
    """返回 [(子元组节点, fk, 缺失的父键值)]；only 限定只检查这些节点。"""
    d = by_rel(inst)
    out = []
    for fk in schema.fks:
        ppos = schema.rels[fk.parent].pos(fk.pattrs)
        pkeys = {tuple(t[i] for i in ppos) for t in d.get(fk.parent, ())}
        cpos = schema.rels[fk.child].pos(fk.cattrs)
        nodes = [(fk.child, t) for t in d.get(fk.child, ())]
        if only is not None:
            nodes = [n for n in nodes if n in only]
        for node in nodes:
            t = node[1]
            v = tuple(t[i] for i in cpos)
            if v not in pkeys:
                out.append((node, fk, v))
    return out


# ---------------------------------------------------------------- 唯一路径闭包

def parent_index(schema, inst):
    """(关系名,元组) -> [(父关系名,父元组)]，父元组唯一（取第一条）。"""
    d = by_rel(inst)
    pm = defaultdict(list)
    for fk in schema.fks:
        ppos = schema.rels[fk.parent].pos(fk.pattrs)
        idx = {}
        for t in d.get(fk.parent, ()):
            idx.setdefault(tuple(t[i] for i in ppos), t)
        cpos = schema.rels[fk.child].pos(fk.cattrs)
        for t in d.get(fk.child, ()):
            p = idx.get(tuple(t[i] for i in cpos))
            if p is not None:
                pm[(fk.child, t)].append((fk.parent, p))
    return pm


def ancestors(pm, node):
    seen = {node}
    stack = [node]
    while stack:
        x = stack.pop()
        for y in pm.get(x, ()):
            if y not in seen:
                seen.add(y)
                stack.append(y)
    return seen


def closure_check_greedy(schema, inst, cand, look=None):
    """不回溯版：每遇到悬空子元组，取第一个父候选；选错就判错。

    这是"见证是一条链"的直觉版本，用来量化"不做分支搜索会错多少"。
    """
    look = look if look is not None else parent_lookup(schema, inst)
    if not consistent(schema, cand):
        return False
    for node in sorted(inst - cand):
        add = frozenset({node})
        ok = False
        while True:
            S = cand | add
            if not fd_ok(schema, S):
                break
            miss = dangling(schema, S, only=add)
            if not miss:
                ok = True
                break
            (_, fk, v) = miss[0]
            k = schema.fks.index(fk)
            nxt = [p for p in look[k].get(v, ()) if (fk.parent, p) not in S]
            if not nxt:
                break
            add = add | {(fk.parent, nxt[0])}
        if ok:
            return False
    return True


# ---------------------------------------------------------------- 精确成组搜索

def parent_lookup(schema, inst):
    """每个 fk 一张 键值 -> 父元组列表 的表（脏数据下同一键值可能有多条）。"""
    d = by_rel(inst)
    look = []
    for fk in schema.fks:
        ppos = schema.rels[fk.parent].pos(fk.pattrs)
        idx = defaultdict(list)
        for t in d.get(fk.parent, ()):
            idx[tuple(t[i] for i in ppos)].append(t)
        look.append(idx)
    return look


def exists_superset_containing(schema, inst, cand, node, look, budget=MAX_SEARCH_STATES):
    """是否存在一致超集 S ⊆ I，使 cand ∪ {node} ⊆ S。

    只需要找"最小的"一致超集：加元组只能增加 FD 违规和外键义务，加多了不会更好。
    对每个悬空的子元组，逐个尝试它的父候选（脏数据下可能有多个）。
    """
    stack = [frozenset({node})]
    seen = set()
    states = 0
    while stack:
        add = stack.pop()
        if add in seen:
            continue
        seen.add(add)
        states += 1
        if states > budget:
            raise RuntimeError("搜索状态爆炸")
        S = cand | add
        if not fd_ok(schema, S):
            continue                      # FD 已冲突，此分支无解
        miss = dangling(schema, S, only=add)
        if not miss:
            return True, add              # 找到了一致超集
        (_, fk, v) = miss[0]
        k = schema.fks.index(fk)
        for p in look[k].get(v, ()):      # 逐个父候选分支
            pn = (fk.parent, p)
            if pn not in S:
                stack.append(add | {pn})
    return False, frozenset()


def closure_check_exact(schema, inst, cand, look=None):
    look = look if look is not None else parent_lookup(schema, inst)
    if not consistent(schema, cand):
        return False, None
    for node in sorted(inst - cand):
        ok, add = exists_superset_containing(schema, inst, cand, node, look)
        if ok:
            return False, (node, add)     # 能整组加回 → 不极大
    return True, None


def unit_check(schema, inst, cand):
    """单元见证：只试 cand ∪ {t}。单表算法的直接搬运。"""
    if not consistent(schema, cand):
        return False
    for node in sorted(inst - cand):
        if consistent(schema, cand | {node}):
            return False
    return True


def oracle_check(schema, inst, cand):
    """暴力裁判：枚举 I\\I' 的所有子集。"""
    if not consistent(schema, cand):
        return False
    removed = sorted(inst - cand)
    if len(removed) > MAX_ORACLE_REMOVED:
        raise ValueError("实例过大")
    for k in range(1, len(removed) + 1):
        for combo in combinations(removed, k):
            if consistent(schema, cand | set(combo)):
                return False
    return True


# ---------------------------------------------------------------- 模式构造

def schema_two_table():
    P = Rel("P", ["id", "val"], ["id"], [(("id",), "val")])
    C = Rel("C", ["cid", "pid", "cval"], ["cid"], [(("cid",), "pid"), (("cid",), "cval")])
    return Schema([P, C], [FK("C", ["pid"], "P", ["id"])])


def schema_ab_cycle():
    A = Rel("A", ["aid", "bref", "aval"], ["aid"], [(("aid",), "bref"), (("aid",), "aval")])
    B = Rel("B", ["bid", "aref", "bval"], ["bid"], [(("bid",), "aref"), (("bid",), "bval")])
    return Schema([A, B], [FK("A", ["bref"], "B", ["bid"]), FK("B", ["aref"], "A", ["aid"])])


def schema_self():
    E = Rel("E", ["empno", "mgr", "val"], ["empno"],
            [(("empno",), "mgr"), (("empno",), "val")])
    return Schema([E], [FK("E", ["mgr"], "E", ["empno"])])


def schema_chain(d):
    rels = [Rel("T%d" % i, ["id", "nxt", "val"], ["id"],
                [(("id",), "nxt"), (("id",), "val")]) for i in range(d)]
    fks = [FK("T%d" % i, ["nxt"], "T%d" % (i + 1), ["id"]) for i in range(d - 1)]
    return Schema(rels, fks)


# ---------------------------------------------------------------- 随机实例

def rand_instance(rng, schema, sizes, dom):
    inst = set()
    for rel in schema.rels.values():
        universe = list(product(range(dom), repeat=len(rel.attrs)))
        n = min(sizes.get(rel.name, 0), len(universe))
        for t in rng.sample(universe, n):
            inst.add((rel.name, tuple(t)))
    return frozenset(inst)


def rand_candidate(rng, schema, inst, tries=80):
    items = sorted(inst)
    for _ in range(tries):
        k = rng.randint(1, max(1, len(items) - 1))
        cand = frozenset(rng.sample(items, k))
        if len(cand) < len(items) and consistent(schema, cand):
            return cand
    return None


# ---------------------------------------------------------------- 实验

def experiment_A(seed=20261116, trials=400):
    rng = random.Random(seed)
    rows = []
    for tag, schema, sizes, dom in (
        ("无环两表", schema_two_table(), {"P": 3, "C": 4}, 2),
        ("无环三表链", schema_chain(3), {"T0": 2, "T1": 3, "T2": 2}, 2),
        ("互引用两表", schema_ab_cycle(), {"A": 3, "B": 3}, 2),
        ("无环两表 域3", schema_two_table(), {"P": 4, "C": 5}, 3),
        ("互引用 域3", schema_ab_cycle(), {"A": 4, "B": 4}, 3),
    ):
        n = c1_ok = c2_ok = u_ok = 0
        u_fp = 0
        attempts = 0
        while n < trials and attempts < 60000:
            attempts += 1
            inst = rand_instance(rng, schema, sizes, dom=dom)
            if not (2 <= len(inst) <= MAX_ORACLE_REMOVED + 1):
                continue
            cand = rand_candidate(rng, schema, inst)
            if cand is None:
                continue
            o = oracle_check(schema, inst, cand)
            e, _ = closure_check_exact(schema, inst, cand)
            u1 = closure_check_greedy(schema, inst, cand)
            u = unit_check(schema, inst, cand)
            n += 1
            c1_ok += (e == o)
            c2_ok += (u1 == o)
            u_ok += (u == o)
            if u and not o:
                u_fp += 1
        rows.append((tag, n, c1_ok, c2_ok, u_ok, u_fp))
    return rows


def max_witness_unique(schema, inst, cand):
    pm = parent_index(schema, inst)
    return max((len(ancestors(pm, n) - cand) for n in inst - cand), default=0)


def experiment_B():
    out = []
    for d in range(1, 9):
        s = schema_chain(d)
        m = 50
        inst = frozenset(("T%d" % i, (j, j, 0)) for i in range(d) for j in range(m))
        cand = frozenset()
        out.append(("链式无环 d=%d" % d, len(inst), max_witness_unique(s, inst, cand),
                    closure_check_exact(s, inst, cand)[0], unit_check(s, inst, cand)))
    for n in range(2, 9):
        s = schema_self()
        inst = frozenset(("E", (i, (i + 1) % n, 0)) for i in range(n))
        cand = frozenset()
        out.append(("自引用环 n=%d" % n, len(inst), max_witness_unique(s, inst, cand),
                    closure_check_exact(s, inst, cand)[0], unit_check(s, inst, cand)))
    return out


def selftest():
    s = schema_ab_cycle()
    inst = frozenset({("A", (0, 0, 0)), ("B", (0, 0, 0))})
    assert consistent(s, inst)
    cand = frozenset()
    ok, w = closure_check_exact(s, inst, cand)
    assert not ok and w[1] == inst, "A/B 互引用：空集非极大，见证应为整组"
    assert unit_check(s, inst, cand), "单元见证在此漏判"

    single = frozenset({("A", (0, 0, 0))})
    assert not consistent(s, single)
    assert not closure_check_exact(s, inst, single)[0]
    assert closure_check_exact(s, inst, inst)[0]

    s2 = schema_two_table()
    i2 = frozenset({("P", (0, 0)), ("C", (9, 0, 0))})
    assert not closure_check_exact(s2, i2, frozenset())[0]
    assert not unit_check(s2, i2, frozenset()), "无环时单元见证应同样有效"

    # 父键重复：P 里同时有 (0,0) 与 (0,1)，键 0 出现两次
    i3 = frozenset({("P", (0, 0)), ("P", (0, 1)), ("C", (9, 0, 0))})
    cand3 = frozenset({("P", (0, 0)), ("C", (9, 0, 0))})
    assert consistent(s2, cand3)
    ok3, _ = closure_check_exact(s2, i3, cand3)
    assert ok3, "剩下的 P(0,1) 键冲突加不进来，候选已是极大"
    assert closure_check_greedy(s2, i3, cand3), "此例首次选择也给同一结论"
    return True


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    lines = []

    def out(s=""):
        print(s)
        lines.append(s)

    out("=" * 78)
    out("fk_closure v2 —— 运行报告（2026-11-16）")
    out("=" * 78)
    assert selftest()
    out("[自检] 通过：A/B 互引用、无环两表、父键重复三组")
    out()

    out("-" * 78)
    out("实验 A：随机小实例（各 400 例），与暴力枚举裁判对比")
    out("-" * 78)
    out("%-10s %5s %12s %12s %12s %8s" %
        ("模式", "样本", "精确成组", "首次选择", "单元见证", "单元假阳"))
    for tag, n, c1, c2, u, fp in experiment_A():
        out("%-10s %5d %11.1f%% %11.1f%% %11.1f%% %8d" %
            (tag, n, 100.0 * c1 / n, 100.0 * c2 / n, 100.0 * u / n, fp))
    out()
    out("  精确成组 = 对父候选做分支搜索（本文方案）")
    out("  首次选择 = 每步只取第一个父候选，不回溯")
    out("  单元见证 = 单表算法直接搬过来，只试单独加一条")
    out("  单元假阳 = 说'是修复'但实际不是（漏判）")
    out()

    out("-" * 78)
    out("实验 B：见证大小 |R(t)| 随什么增长")
    out("-" * 78)
    out("%-16s %7s %8s %10s %10s" % ("实例", "|I|", "max|R|", "精确成组", "单元见证"))
    for tag, ni, mw, ce, un in experiment_B():
        out("%-16s %7d %8d %10s %10s" %
            (tag, ni, mw, "极大" if ce else "非极大", "极大" if un else "非极大"))
    out()

    out("-" * 78)
    out("示例：A/B 互引用，候选为空集")
    out("-" * 78)
    s = schema_ab_cycle()
    inst = frozenset({("A", (0, 0, 0)), ("B", (0, 0, 0))})
    ok, w = closure_check_exact(s, inst, frozenset())
    out("  精确成组：%s，见证 %s，需成组加回 %d 条" %
        ("是修复" if ok else "不是修复", w[0], len(w[1])))
    out("  单元见证：%s" % ("是修复" if unit_check(s, inst, frozenset()) else "不是修复"))
    out()

    path = "experiments/results_fk_closure.txt"
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n[已保存] %s" % path)


if __name__ == "__main__":
    main()
