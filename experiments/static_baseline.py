# -*- coding: utf-8 -*-
"""
DASFAA 2027 补充实验 B：真正的全量静态检查器 / SQL 基线

要回答的审稿意见
    "你的 baseline 是自己写的朴素重建；SQLite 那组只跑了一个 GROUP BY，
     不是 repair checking；63.9x 是原型比原型。"

同一个更新序列、同一个状态，三种方法逐步给出 status：
    (1) full-static  每步从零重跑静态子集修复判定（FD 冲突 + 极大性），Python
    (2) sql-full     每步在 SQLite 用 NOT EXISTS 表达完整判定（一致性 OR 非极大性）
    (3) incremental  本文方法：维护键计数 + 键占用，O(1)/步

场景：单表 R(K,V)，FD K->V，集合语义。I 是脏实例（部分键有两个不同 V），
      cand 每个键留一条 → 一致且极大。更新：删候选元组 / 插入被排除元组。

诚实口径
    - SQLite 是 C 引擎，绝对耗时不直接比；比的是随 |I| 的伸缩趋势。
    - 比的是 checking 本身（更新统一施加），不是端到端清洗系统。
    - 报中位数 + IQR，有预热、固定种子、逐法状态从初态重建。

运行：python experiments/static_baseline.py
无第三方依赖（sqlite3 为标准库）。
"""
from __future__ import annotations

import sqlite3
import statistics
import sys
import time

OUT = []


def out(s=""):
    print(s)
    OUT.append(str(s))


def save():
    path = "experiments/results_static_baseline.txt"
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


# ---------------------------------------------------------------- 状态与更新
def build_state(n_keys, seed=20261118):
    """脏实例：每键一条主元组；25% 的键再插一条冲突值。cand 每键留主元组。"""
    I, cand = set(), set()
    for k in range(n_keys):
        t = (k, k % 7)
        I.add(t)
        cand.add(t)
        if (k * 7919) % 100 < 25:
            I.add((k, 11 + k % 5))
    return I, cand


def gen_updates(n_keys, n_steps, seed=20261119):
    """只生成序列，不施加；三种方法各自从初态重放。"""
    rng_k = [(i * 104729 + 17) % n_keys for i in range(n_steps)]
    seq = []
    for i, k in enumerate(rng_k):
        if i % 3 == 0:
            seq.append(("D", (k, k % 7)))          # 删候选元组
        else:
            seq.append(("I", (k, 30 + i % 13)))    # 插被排除元组
    return seq


def apply_sets(I, cand, op, t):
    I.discard(t) if op == "D" else I.add(t)
    if op == "D":
        cand.discard(t)


# ---------------------------------------------------------------- 三种判定
def check_full_static(I, cand):
    bykey = {}
    for k, v in cand:
        if k in bykey and bykey[k] != v:
            return False
        bykey[k] = v
    for k, v in I:
        if (k, v) in cand:
            continue
        if k not in bykey or bykey[k] == v:
            return False
    return True


_SQL = """
SELECT NOT EXISTS (
  SELECT 1 FROM cand_t GROUP BY K HAVING COUNT(DISTINCT V) > 1
) AND NOT EXISTS (
  SELECT 1 FROM all_t a
  WHERE NOT EXISTS (SELECT 1 FROM cand_t c WHERE c.K = a.K AND c.V = a.V)
    AND NOT EXISTS (SELECT 1 FROM cand_t c WHERE c.K = a.K AND c.V <> a.V)
)
"""


def make_sql_conn(I0, cand0):
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("CREATE TABLE all_t(K INTEGER, V INTEGER)")
    conn.execute("CREATE TABLE cand_t(K INTEGER, V INTEGER)")
    conn.execute("CREATE INDEX ia ON all_t(K)")
    conn.execute("CREATE INDEX ic ON cand_t(K)")
    conn.executemany("INSERT INTO all_t VALUES(?,?)", I0)
    conn.executemany("INSERT INTO cand_t VALUES(?,?)", cand0)
    conn.commit()
    return conn


def check_sql(conn, op, t):
    k, v = t
    if op == "D":
        conn.execute("DELETE FROM cand_t WHERE K=? AND V=?", (k, v))
        conn.execute("DELETE FROM all_t WHERE K=? AND V=?", (k, v))
    else:
        conn.execute("INSERT INTO all_t VALUES(?,?)", (k, v))
    return bool(conn.execute(_SQL).fetchone()[0])


class Incremental:
    """I 的键计数 + cand 的键占用；missing 非空即非修复。"""
    def __init__(self, I, cand):
        self.cand_v = dict(cand)
        self.i_count = {}
        for k, _ in I:
            self.i_count[k] = self.i_count.get(k, 0) + 1
        self.missing = {k for k in self.i_count if k not in self.cand_v}
        self.valid = not self.missing

    def update(self, op, t):
        k, v = t
        if op == "D":
            c = self.i_count.get(k, 1) - 1
            if c == 0:
                self.i_count.pop(k, None)
            else:
                self.i_count[k] = c
            if self.cand_v.get(k) == v:
                del self.cand_v[k]
            if k in self.i_count and k not in self.cand_v:
                self.missing.add(k)
            elif k not in self.i_count:
                self.missing.discard(k)
        else:
            self.i_count[k] = self.i_count.get(k, 0) + 1
            if k not in self.cand_v:
                self.missing.add(k)
        self.valid = not self.missing
        return self.valid


# ---------------------------------------------------------------- 计时
def timed_run(method, I0, cand0, seq, warmup=5):
    """从初态重放整条序列，返回 (总时间, 每步 status 列表)。"""
    if method == "sql":
        conn = make_sql_conn(I0, cand0)
    elif method == "inc":
        inc = Incremental(I0, cand0)
    else:
        I, cand = set(I0), set(cand0)

    def step(op, t):
        if method == "static":
            apply_sets(I, cand, op, t)
            return check_full_static(I, cand)
        if method == "sql":
            return check_sql(conn, op, t)
        return inc.update(op, t)

    for op, t in seq[:warmup]:          # 预热，不计时
        step(op, t)
    if method == "sql":
        conn.close()
        conn = make_sql_conn(I0, cand0)
    elif method == "inc":
        inc = Incremental(I0, cand0)
    else:
        I, cand = set(I0), set(cand0)

    statuses = []
    t0 = time.perf_counter()
    for op, t in seq:
        statuses.append(step(op, t))
    elapsed = time.perf_counter() - t0
    if method == "sql":
        conn.close()
    return elapsed, statuses


def bench(method, n_keys, n_steps, reps=5):
    samples = []
    for r in range(reps):
        I, cand = build_state(n_keys, seed=20261118 + r)
        seq = gen_updates(n_keys, n_steps, seed=20261119 + r)
        elapsed, _ = timed_run(method, I, cand, seq)
        samples.append(elapsed / n_steps * 1e6)
    s = sorted(samples)
    return statistics.median(s), quantile(s, .25), quantile(s, .75)


def verify(n_keys=400, n_steps=180):
    """逐步核对三法与 full-static 参考的一致性（小规模，单独跑，不计时）。"""
    I, cand = build_state(n_keys)
    seq = gen_updates(n_keys, n_steps)
    ref = timed_run("static", I, cand, seq, warmup=0)[1]
    out("-" * 96)
    out("B0  正确性核对：三法逐步 status 是否一致（n_keys=%d, steps=%d）" % (n_keys, n_steps))
    out("-" * 96)
    for m in ("static", "sql", "inc"):
        st = timed_run(m, I, cand, seq, warmup=0)[1]
        same = sum(1 for a, b in zip(st, ref) if a == b)
        out("%12s   agreement = %d/%d" % (m, same, n_steps))
    out()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    out("=" * 96)
    out("DASFAA 2027 补充实验 B：全量静态检查器 / SQL 基线 vs 增量维护（2026-11-18）")
    out("=" * 96)
    out("场景：R(K,V), FD K->V；I 脏，cand 每键留一条。同一更新序列、各自从初态重放。")
    out("口径：比 checking 成本与伸缩趋势，非端到端清洗系统；不宣称绝对快过 C 引擎。")
    out("计时：预热 5 步、重复 5 次，中位数与 IQR(us/step)。")
    out()
    verify()
    out("%10s %10s %25s %25s %25s" %
        ("n_keys", "|I|", "full-static med [IQR]", "sql-full med [IQR]", "incremental med [IQR]"))
    rows = []
    n_steps = 120
    for n_keys in (800, 4000, 16000, 40000):
        I, _ = build_state(n_keys)
        ni = len(I)
        fs = bench("static", n_keys, n_steps)
        sq = bench("sql", n_keys, n_steps)
        inc = bench("inc", n_keys, n_steps)
        rows.append((ni, fs, sq, inc))
        out("%10d %10d %10.1f [%6.1f,%6.1f] %10.1f [%6.1f,%6.1f] %10.3f [%5.3f,%5.3f]" %
            (n_keys, ni, fs[0], fs[1], fs[2], sq[0], sq[1], sq[2],
             inc[0], inc[1], inc[2]))
    out()
    a, b = rows[0], rows[-1]
    out("  |I| %d -> %d（%.0f 倍）：" % (a[0], b[0], b[0] / a[0]))
    out("    full-static %.1f -> %.1f us/step（%.1f 倍）" % (a[1][0], b[1][0], b[1][0] / a[1][0]))
    out("    sql-full    %.1f -> %.1f us/step（%.1f 倍）" % (a[2][0], b[2][0], b[2][0] / a[2][0]))
    out("    incremental %.3f -> %.3f us/step（%.1f 倍）" % (a[3][0], b[3][0], b[3][0] / a[3][0]))
    out()
    out("  结论：真正的全量判定（Python 或 SQL）随 |I| 增长；增量维护保持平坦。")
    save()


if __name__ == "__main__":
    main()
