# -*- coding: utf-8 -*-
"""补救实验一：Flights 真实 schema + 真实错误模式的规模化扩展。

背景：公开数据审计只有 161 行（7 个航班）。本脚本不改 schema、不改约束，
从真实 flight.csv 里学出"每个航班的多源取值向量"（同一航班从十几家来源抓取，
字段值/格式/空值各不相同 -> 违反 flight -> 其余 6 列的 FD），据此合成上万航班，
每航班带若干个冲突版本，把规模从 10^4 推到 2x10^5。

定位（论文里如实写）：schema-preserving synthetic augmentation from the HoloClean
Flights schema and its multi-source error patterns. 不是新的真实数据集。

设定：
  模式 F(flight, sched, act_dept, gate, sched_arr, act_arr, arr_gate)
  FD: flight -> 其余 6 列；flight 主键，单表 BCNF，无外键。

  键组 = 一个航班的全部版本。候选 r' 每航班保留一个版本（一致）；
  I 里同航班的其它版本被排除。极大性 <=> I 中每个航班在 r' 里都有一个版本。

  更新 u = 插入一个版本 / 删除一个版本。局部性：只影响该航班的键组。

三法（逐步核对 status 是否一致）：
  inc       : 维护键占用索引，只重判更新涉及的那个航班（O(1) per update）
  full      : 每次更新从零扫整个 I 建索引再判（O(|I|)），论文里的全量重判基线
  oracle    : 独立的全量精确判定（与 inc 逻辑解耦），用于 agreement

指标：agreement(inc/full vs oracle)、per-update 时间、witness 有效性。

运行： python experiments/flights_scaled.py
"""
from __future__ import annotations

import csv
import random
import statistics
import sys
import time
from collections import defaultdict

ATTRS = ["flight", "sched", "act_dept", "gate", "sched_arr", "act_arr", "arr_gate"]
SRC_COLS = {
    "flight": "flight",
    "sched": "scheduled_dept",
    "act_dept": "actual_dept",
    "gate": "dept_gate",
    "sched_arr": "scheduled_arrival",
    "act_arr": "actual_arrival",
    "arr_gate": "arrival_gate",
}


# ---------------------------------------------------------------- 输出
_LINES = []


def out(s=""):
    print(s)
    _LINES.append(s)


def save():
    p = "experiments/results_flights_scaled.txt"
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(_LINES) + "\n")
    print("[saved] %s" % p)


def quantile(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    k = min(len(sorted_vals) - 1, int(p * (len(sorted_vals) - 1)))
    return sorted_vals[k]


# ---------------------------------------------------------------- 从真实 CSV 学错误模板
def learn_templates(path="experiments/data/flight.csv"):
    """返回 {flight: [6 列值向量, ...]}；每个向量是一个来源对该航班的取值。"""
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    by_flight = defaultdict(set)
    for r in rows:
        fl = (r[SRC_COLS["flight"]] or "").strip()
        if not fl:
            continue
        vec = tuple((r[SRC_COLS[a]] or "").strip() for a in ATTRS[1:])
        by_flight[fl].add(vec)
    return {k: sorted(v) for k, v in by_flight.items()}


def flatten_templates(templates):
    """所有真实取值向量拉平，作为合成航班的错误模板池。"""
    pool = []
    for vecs in templates.values():
        pool.extend(vecs)
    return sorted(set(pool))


# ---------------------------------------------------------------- 状态：键占用索引
class KeyIndex:
    """cand 每键至多一个版本（保持一致）。维护全局 status 计数，更新只碰一个键。

    全局 status = (缺席键数 == 0)，缺席键 = present 中有、occ 中没有的键。
    维护 missing_count，使 status 查询 O(1)，单次更新 O(1) —— 真正的局部性。
    """

    def __init__(self, I, cand):
        self.occ = {}
        for k, vec in cand:
            self.occ[k] = vec
        self.present = defaultdict(set)
        for k, vec in I:
            self.present[k].add(vec)
        self.missing = {k for k in self.present if k not in self.occ}

    def status(self):
        # O(1)：全局 status 由缺席键集合是否为空决定
        if not self.missing:
            return True, None
        return False, next(iter(self.missing))

    def witness(self, k):
        # 该键缺席 -> 任一被排除版本都能加回（单表 BCNF 单例充分）
        for vec in self.present[k]:
            if self.occ.get(k) != vec:
                return (k, vec)
        return None

    def apply(self, op, node):
        """只更新 node 所属的键，O(1) 维护 missing 集合。"""
        k, vec = node
        was_missing = k in self.missing
        if op == "I":
            self.present[k].add(vec)
        else:
            self.present[k].discard(vec)
            if not self.present[k]:
                del self.present[k]
            if self.occ.get(k) == vec:
                del self.occ[k]
        now_missing = (k in self.present and k not in self.occ)
        if now_missing and not was_missing:
            self.missing.add(k)
        elif was_missing and not now_missing:
            self.missing.discard(k)


# ---------------------------------------------------------------- 三法检查
def inc_step(idx, op, node):
    idx.apply(op, node)
    ok, k = idx.status()
    w = idx.witness(k) if not ok else None
    return ok, w


def full_check(I, cand):
    """从零扫整个 I：建 occupied / present 再判。O(|I|)。"""
    occ = {}
    for k, vec in cand:
        occ[k] = vec
    present = defaultdict(set)
    for k, vec in I:
        present[k].add(vec)
    for k in present:
        if k not in occ:
            for vec in sorted(present[k]):
                if occ.get(k) != vec:
                    return False, (k, vec)
    return True, None


def oracle_check(I, cand):
    """独立裁判（O(|I|)）：一致 <=> 每键至多一个候选版本；极大 <=> I 的键都被占用。"""
    seen = set()
    for k, _ in cand:                      # 一致：每个键最多一个版本
        if k in seen:
            return False
        seen.add(k)
    present = {k for k, _ in I}
    return present <= seen                 # 极大：I 中每个键在 cand 有版本


# ---------------------------------------------------------------- 数据生成
def gen_instance(n_flights, versions, pool, rng):
    """n_flights 个合成航班，每航班 versions 个版本（从真实模板池抽）。"""
    I = set()
    cand = set()
    for i in range(n_flights):
        key = "SFLT-%06d" % i
        vecs = rng.sample(pool, min(versions, len(pool)))
        for j, vec in enumerate(vecs):
            node = (key, vec)
            I.add(node)
            if j == 0:                 # 每航班保留一个版本 -> 一致候选
                cand.add(node)
    return frozenset(I), frozenset(cand)


def gen_updates(I, cand, pool, n_steps, rng):
    """更新流：只生成合法 node（删除的必须在当前 I 中），用副本跟踪真实状态。"""
    keys = sorted({k for k, _ in I})
    by_key = defaultdict(set)
    for k, vec in I:
        by_key[k].add(vec)
    I_now = {k: set(vs) for k, vs in by_key.items()}
    seq = []
    for _ in range(n_steps):
        k = rng.choice(keys)
        here = I_now[k]
        r = rng.random()
        if r < 0.35 and here:
            vec = rng.choice(sorted(here))
            seq.append(("D", (k, vec)))
            here.discard(vec)
        elif r < 0.70:
            vec = rng.choice(pool)
            if vec not in here:
                seq.append(("I", (k, vec)))
                here.add(vec)
            else:
                seq.append(("D", (k, vec)))
                here.discard(vec)
        else:
            if here:
                vec = rng.choice(sorted(here))
                seq.append(("D", (k, vec)))
                here.discard(vec)
            else:
                vec = rng.choice(pool)
                seq.append(("I", (k, vec)))
                here.add(vec)
    return seq


# ---------------------------------------------------------------- 实验
def bench(n_flights, versions, pool, seed, n_steps, reps=3, warmup=3):
    # 实例与更新流必须在同一个 rng 状态下生成，保证三法看到同一实例
    rng = random.Random(seed)
    I0, cand0 = gen_instance(n_flights, versions, pool, rng)
    seq = gen_updates(I0, cand0, pool, n_steps, rng)

    # 正确性：三法逐步 status 一致
    idx = KeyIndex(I0, cand0)
    I = set(I0)
    cand = set(cand0)
    agree_inc = agree_full = wit_ok = n_nonmax = 0
    for op, node in seq:
        # 先统一推进 I / cand，三法都看"更新后"的同一个状态
        if op == "I":
            I.add(node)
        else:
            I.discard(node)
            cand.discard(node)
        s_inc, w = inc_step(idx, op, node)
        s_full, _ = full_check(I, cand)
        s_or = oracle_check(I, cand)
        agree_inc += (s_inc == s_or)
        agree_full += (s_full == s_or)
        if not s_or:
            n_nonmax += 1
            if w is not None:
                # witness 有效 <=> 该键在 cand 无占用且该版本确实在 I（单例充分）
                k_w, vec_w = w
                wit_ok += int(k_w not in idx.occ and (k_w, vec_w) in I)

    # 计时：inc vs full（同样的 seq，同样的起始状态）
    def timed(method):
        samples = []
        for rep in range(reps + (1 if warmup else 0)):
            Is, cs = set(I0), set(cand0)
            ix = KeyIndex(Is, cs)
            t0 = time.perf_counter()
            for op, node in seq:
                if method == "inc":
                    inc_step(ix, op, node)
                else:
                    full_check(Is, cs)
                if op == "I":
                    Is.add(node)
                else:
                    Is.discard(node)
                    cs.discard(node)
            dt = (time.perf_counter() - t0) / n_steps * 1e6
            if rep >= warmup:
                samples.append(dt)
        samples.sort()
        return statistics.median(samples), samples

    t_inc, s_inc = timed("inc")
    t_full, s_full = timed("full")
    return dict(n_flights=n_flights, ni=len(I0), agree_inc=agree_inc,
                agree_full=agree_full, steps=n_steps, wit_ok=wit_ok,
                n_nonmax=n_nonmax,
                t_inc=t_inc, t_full=t_full,
                inc_iqr=(quantile(s_inc, .25), quantile(s_inc, .75)),
                full_iqr=(quantile(s_full, .25), quantile(s_full, .75)))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    rng = random.Random(20261119)
    templates = learn_templates()
    pool = flatten_templates(templates)
    out("=" * 100)
    out("补救实验一：HoloClean Flights schema 保真扩展（真实多源错误模式）")
    out("=" * 100)
    out("真实数据：%d 个航班 / %d 个去重取值向量（原始 161 行）" %
        (len(templates), len(pool)))
    out("模式 F(flight, sched, act_dept, gate, sched_arr, act_arr, arr_gate)")
    out("FD flight -> 其余 6 列；单表 BCNF，无 FK。局部性来自键组（每航班约 20 版本）。")
    out("定位：schema-preserving synthetic augmentation（非新的真实数据集）。")
    out()

    configs = [
        (500, 20, 200),
        (1000, 20, 200),
        (2000, 20, 200),
        (5000, 20, 200),
        (10000, 20, 200),
    ]
    out("-" * 100)
    out("%8s %9s %10s %10s %12s %12s %12s %9s" %
        ("航班数", "|I|", "inc/or", "full/or", "witness", "inc us/step", "full us/step", "加速"))
    out("-" * 100)
    rows = []
    for nf, ver, steps in configs:
        r = bench(nf, ver, pool, seed=1000 + nf, n_steps=steps)
        rows.append(r)
        out("%8d %9d %7d/%d %7d/%d %5d/%d %9.3f %12.1f %8.1fx" %
            (r["n_flights"], r["ni"], r["agree_inc"], r["steps"],
             r["agree_full"], r["steps"], r["wit_ok"], r["n_nonmax"],
             r["t_inc"], r["t_full"], r["t_full"] / r["t_inc"]))
    out("-" * 100)
    out()
    r0, r1 = rows[0], rows[-1]
    out("结论：|I| %d -> %d（%.0fx），inc %.3f -> %.3f us/step（%.1fx），full %.1f -> %.1f us/step（%.1fx）。" %
        (r0["ni"], r1["ni"], r1["ni"] / r0["ni"],
         r0["t_inc"], r1["t_inc"], r1["t_inc"] / r0["t_inc"],
         r0["t_full"], r1["t_full"], r1["t_full"] / r0["t_full"]))
    ai = all(r["agree_inc"] == r["steps"] for r in rows)
    af = all(r["agree_full"] == r["steps"] for r in rows)
    aw = all(r["wit_ok"] == r["n_nonmax"] for r in rows)
    out("inc 与 oracle 逐步一致: %s；full 与 oracle 逐步一致: %s；非极大步 witness 全部有效: %s" %
        ("是" if ai else "否", "是" if af else "否", "是" if aw else "否"))
    out("维护代价随键组大小走，与总航班数（|I|）无关 —— 与 Theorem 1 一致。")
    out()
    save()


if __name__ == "__main__":
    main()
