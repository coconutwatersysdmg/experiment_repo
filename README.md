# Experiment code — DASFAA 2027 submission

Replication package for *Structure-Guided Incremental Subset Repair Checking under Updates*.
Only the code that produces the numbers reported in the paper is included. Figures, paper
sources and unrelated prototype scripts are excluded.

## How to run

No third-party dependencies. From this directory:

```
python experiments/exp_witness.py
python experiments/affected_set_distribution.py
python experiments/static_baseline.py
python experiments/stream_and_fanout.py
python experiments/tpc_h_multitable.py
python experiments/diagnosis_gap_hard.py
python experiments/audit_repair.py
python experiments/flights_scaled.py
```

Each script overwrites its own `experiments/results_*.txt`. `fk_closure.py` is the shared
engine imported by most scripts; it is not run directly. `data/flight.csv` is the public
HoloClean Flights instance used by `audit_repair.py` and `flights_scaled.py`.

The committed `results_*.txt` are the exact numbers reported in the paper. A re-run may
differ slightly for two reasons, neither affecting the claims: (i) wall-clock timings depend
on the machine; (ii) Python randomizes `set`/`frozenset` iteration order across processes,
so random-instance counts can vary by one or two cases. For bit-identical random results,
fix the hash seed, e.g. `PYTHONHASHSEED=0 python experiments/diagnosis_gap_hard.py`. The
paper reports medians and agreement rates, which are stable under these variations.

## Script → paper mapping

| Paper location | Script | Output |
|---|---|---|
| §VI Q1, Table 1 (oracle agreement) | `exp_witness.py` | `results_witness.txt` |
| §VI Q2, Table 2 / Fig. 2 (affected set) | `affected_set_distribution.py` | `results_affected.txt` |
| §VI Q3, Table 3 / Fig. 3 (static + SQL baselines) | `static_baseline.py` | `results_static_baseline.txt` |
| §VI Q3, Table 4 (long stream) | `stream_and_fanout.py` | `results_stream_fanout.txt` |
| §VI Q4, Table 5 (TPC-H-style four tables) | `tpc_h_multitable.py` | `results_tpch.txt` |
| §VI Q5, Fig. 4 (diagnostic gap) | `diagnosis_gap_hard.py` | `results_gap_hard.txt` |
| §VII Limitations (public-data audit, 161 tuples) | `audit_repair.py` | `results_audit.txt` |
| §VI / §VII (Flights schema-preserving augmentation, up to \|I\|=2×10⁵) | `flights_scaled.py` | `results_flights_scaled.txt` |

`fk_closure.py` also implements the exact oracle and singleton/group checking used by the
structural-boundary propositions in §III.

## Anonymity note

Before this repository is made public or linked from the paper, rewrite git history to
remove author names and e-mail addresses, and use an anonymous remote identity.
