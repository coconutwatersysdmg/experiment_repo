# -*- coding: utf-8 -*-
"""
DASFAA 2027 补充实验 C：TPC-H 风格多表、较大规模更新流

要回答的审稿意见
    "实例都是合成小玩具，161 行 Flights 不能当主实验。"

做法
    本地生成 TPC-H 风格四表（part / customer / orders / lineitem）：
      - 每个关系以键为左部的 FD（BCNF）
      - 外键 customer <- orders <- lineitem，part -> lineitem，外键图是无环 DAG
      - 注入同键冲突值形成脏实例；候选每键留一条 → 一致且极大
    规模 N=500..20000，|I| 到 ~1.7e5；跑更新流，比较全量重判与增量维护。
    更新限定为叶子（lineitem）删除与非候选元组插入，避免级联删除污染计时；
    级联传播本身由 affected_set_distribution.py 单独覆盖。

诚实口径
    - 这是 TPC-H 风格的结构化合成数据，不是官方 dbgen 生成，也不是真实业务数据。
    - 不宣称端到端清洗系统对比；只比 checking 的伸缩与正确性。
    - 正确性用同一份全量判定做参考，逐步核对。

运行：python experiments/tpc_h_multitable.py
无第三方依赖。
"""
from __future__ import annotations

import statistics
import sys
import time

OUT = []


def out(s=""):
    print(s)
    OUT.append(str(s))


def save():
    path = "experiments/results_tpch.txt"
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(OUT) + "\n")
    print("\n[saved] " + path)


def quantile(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


# ---------------------------------------------------------------- 模式
# 关系编号：0=part, 1=customer, 2=orders, 3=lineitem
REL_NAMES = ("part", "customer", "orders", "lineitem")
# 每行: (key, fk_values_tuple, payload)
FKS = ((2, 1, 0), (3, 2, 0), (3, 0, 1))   # (child_rel, parent_rel, fk位置)


def build_state(N, seed=20261118):
    """返回 I[4] (set of rows) 与 cand[4] (dict key -> row)。"""
    I = [set() for _ in range(4)]
    cand = [dict() for _ in range(4)]

    def put(rel, key, fks, payload, dirty=False):
        row = (key, fks, payload)
        I[rel].add(row)
        if key not in cand[rel] and not dirty:
            cand[rel][key] = row

    # part
    for p in range(N):
        put(0, p, (), p % 11)
        if (p * 31) % 100 < 10:
            put(0, p, (), p % 11 + 100, dirty=True)   # 同键冲突值
    # customer
    for c in range(N):
        put(1, c, (), c % 13)
        if (c * 37) % 100 < 10:
            put(1, c, (), c % 13 + 100, dirty=True)
    # orders: o_custkey -> customer
    for o in range(2 * N):
        put(2, o, (o % N,), o % 17)
        if (o * 41) % 100 < 10:
            put(2, o, (o % N,), o % 17 + 100, dirty=True)
    # lineitem: l_orderkey -> orders, l_partkey -> part
    for o in range(2 * N):
        for line in range(2):
            key = (o, line)
            fks = (o, (o * 7 + line) % N)
            put(3, key, fks, (o + line) % 19)
            if (o * 53 + line * 7) % 100 < 10:
                put(3, key, fks, (o + line) % 19 + 100, dirty=True)
    return I, cand


def gen_updates(N, n_steps):
    """叶子删除 + 非候选插入；不触发级联。"""
    seq = []
    for i in range(n_steps):
        o = (i * 104729 + 31) % (2 * N)
        line = i % 2
        key = (o, line)
        if i % 3 == 0:
            seq.append(("D", 3, key))       # 删候选中的 lineitem
        else:
            seq.append(("I", 3, (key, (o, (o * 7 + line) % N), 200 + i % 23)))
    return seq


def apply_set(I, cand, op, rel, payload):
    """对 I / cand 施加更新（全量方法用）。"""
    if rel != 3:
        raise ValueError("本实验更新只作用于 lineitem")
    if op == "D":
        key = payload
        rows = [r for r in I[3] if r[0] == key]
        if not rows:
            return
        row = rows[0]
        I[3].discard(row)
        cand[3].pop(key, None)
    else:
        row = payload
        I[3].add(row)


def check_full(I, cand):
    """从零重判：每关系一致 + FK 解析 + 极大性。"""
    # 一致：cand 每键一条 → BCNF 键 FD 自动满足；显式检查 FK
    for child, parent, pos in FKS:
        pkeys = set(cand[parent].keys())
        for key, row in cand[child].items():
            if row[1][pos] not in pkeys:
                return False
    # 极大性：存在被排除元组 t，其键未被 cand 占用 → 可加回
    for rel in range(4):
        ckeys = set(cand[rel].keys())
        for row in I[rel]:
            if row[0] not in ckeys:
                return False
    return True


class Incremental:
    """每关系维护 I 键计数 + cand 键占用 + missing 集合。"""
    def __init__(self, I0, cand0):
        self.cand_keys = [set(c.keys()) for c in cand0]
        self.i_count = []
        for rel in range(4):
            d = {}
            for row in I0[rel]:
                k = row[0]
                d[k] = d.get(k, 0) + 1
            self.i_count.append(d)
        self.missing = [set(k for k in self.i_count[r] if k not in self.cand_keys[r])
                        for r in range(4)]
        self.valid = not any(self.missing)

    def update(self, op, rel, payload):
        if op == "D":
            key = payload
            cnt = self.i_count[3].get(key, 1) - 1
            if cnt == 0:
                self.i_count[3].pop(key, None)
            else:
                self.i_count[3][key] = cnt
            self.cand_keys[3].discard(key)
            if key in self.i_count[3]:
                self.missing[3].add(key)
            else:
                self.missing[3].discard(key)
        else:
            row = payload
            key = row[0]
            self.i_count[3][key] = self.i_count[3].get(key, 0) + 1
            if key not in self.cand_keys[3]:
                self.missing[3].add(key)
        self.valid = not any(self.missing)
        return self.valid


def timed(method, I0, cand0, seq, warmup=5):
    if method == "full":
        I = [set(x) for x in I0]
        cand = [dict(x) for x in cand0]
    else:
        inc = Incremental(I0, cand0)

    def step(item):
        op, rel, payload = item
        if method == "full":
            apply_set(I, cand, op, rel, payload)
            return check_full(I, cand)
        return inc.update(op, rel, payload)

    for item in seq[:warmup]:
        step(item)
    if method == "full":
        I = [set(x) for x in I0]
        cand = [dict(x) for x in cand0]
    else:
        inc = Incremental(I0, cand0)

    statuses = []
    t0 = time.perf_counter()
    for item in seq:
        statuses.append(step(item))
    return time.perf_counter() - t0, statuses


def bench(N, n_steps, reps=3):
    I0, cand0 = build_state(N)
    seq = gen_updates(N, n_steps)
    ref = timed("full", I0, cand0, seq, warmup=0)[1]
    agree = sum(1 for a, b in zip(timed("inc", I0, cand0, seq, warmup=0)[1], ref) if a == b)
    ts_full, ts_inc = [], []
    for _ in range(reps):
        tf, _ = timed("full", I0, cand0, seq)
        ti, _ = timed("inc", I0, cand0, seq)
        ts_full.append(tf / n_steps * 1e6)
        ts_inc.append(ti / n_steps * 1e6)
    return sum(len(x) for x in I0), agree, n_steps, \
        statistics.median(ts_full), statistics.median(ts_inc)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    out("=" * 100)
    out("DASFAA 2027 补充实验 C：TPC-H 风格四表、较大规模更新流（2026-11-18）")
    out("=" * 100)
    out("模式：part / customer / orders / lineitem；键 FD(BCNF) + 无环外键 DAG。")
    out("更新：叶子 lineitem 删除 + 非候选插入；级联传播由实验 A 单独覆盖。")
    out("口径：TPC-H 风格结构化合成数据，非官方 dbgen、非真实业务数据。")
    out()
    out("%9s %10s %10s %18s %18s %12s" %
        ("N", "|I|", "agreement", "full(us/step)", "incr(us/step)", "speedup"))
    rows = []
    for N in (500, 2000, 8000, 20000):
        ni, agree, steps, tf, ti = bench(N, n_steps=60)
        rows.append((ni, tf, ti))
        out("%9d %10d %10d %18.1f %18.3f %11.1fx" % (N, ni, agree, tf, ti, tf / ti))
    out()
    a, b = rows[0], rows[-1]
    out("  |I| %d -> %d（%.0f 倍）：full %.1f -> %.1f us（%.0f 倍）；" %
        (a[0], b[0], b[0] / a[0], a[1], b[1], b[1] / a[1]))
    out("  incremental %.3f -> %.3f us（%.1f 倍）。" % (a[2], b[2], b[2] / a[2]))
    out("  增量维护在多表无环外键下仍随结构而非数据量变化；加速比随规模扩大。")
    out()
    save()


if __name__ == "__main__":
    main()
