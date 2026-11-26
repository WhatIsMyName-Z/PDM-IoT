"""
ประเมินผลระบบตรวจจับแบบออฟไลน์: "ระบบเตือนก่อนเครื่องพังได้กี่ชั่วโมง และเตือนผิดบ่อยแค่ไหน"

วิธีการ:
  - จำลองเครื่องที่ "จะพังแน่ๆ" หลายร้อยรอบ (ทุกรูปแบบความเสียหาย) แล้วดูว่าแจ้งเตือนครั้งแรกมาก่อนพังกี่ชั่วโมง (lead time)
  - จำลองเครื่องที่ "ปกติดี" แล้วนับว่าเกิด warning/critical ผิดๆ กี่ครั้งต่อ 1,000 ชั่วโมงการทำงาน (false alarm)
  - ใช้ seed คงที่ จึงได้ผลเหมือนเดิมทุกครั้งที่รัน (reproducible)

รัน:  python analysis/evaluate_detection.py
"""
from __future__ import annotations

import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "simulator"), str(ROOT / "backend")]

from machine_model import Machine          # noqa: E402
from app.analytics import MachineAnalyzer  # noqa: E402

RUNS_PER_MODE = 100
HEALTHY_RUNS = 50
HEALTHY_HOURS = 500
DT_H = 1.0          # 1 ข้อความ = 1 ชั่วโมงการทำงาน (เหมือนค่าเริ่มต้นใน docker-compose)
SECONDS_PER_MSG = 2

# alert ที่ "ไม่ได้บอกว่าเครื่องกำลังเสีย" ไม่นับเป็น early warning (เช่น เซนเซอร์ค้าง, spike)
PREDICTIVE_KINDS = {"threshold", "rul", "shift"}


def run_until_failure(mode: str, seed: int) -> dict:
    m = Machine("M", "l", random.Random(seed), fault_probability=1.0)
    m.fault_mode = mode
    clock = [0.0]
    a = MachineAnalyzer("M", clock=lambda: clock[0])
    first: dict[str, float] = {}
    while m.state == "running":
        r = m.step(DT_H)
        clock[0] += SECONDS_PER_MSG
        for al in a.update(r.__dict__).alerts:
            if al.kind in PREDICTIVE_KINDS:
                first.setdefault(al.level, m.op_hours)
    return {lvl: m.op_hours - h for lvl, h in first.items()}


def run_healthy(seed: int) -> dict[str, int]:
    m = Machine("M", "l", random.Random(seed), fault_probability=0.0)
    clock = [0.0]
    a = MachineAnalyzer("M", clock=lambda: clock[0])
    counts: dict[str, int] = defaultdict(int)
    while m.op_hours < HEALTHY_HOURS:
        r = m.step(DT_H)
        clock[0] += SECONDS_PER_MSG
        for al in a.update(r.__dict__).alerts:
            counts[f"{al.kind}/{al.level}"] += 1
    return counts


def main() -> None:
    print(f"== Lead time: แจ้งเตือนครั้งแรกมาก่อนเครื่องพังกี่ชั่วโมงการทำงาน ({RUNS_PER_MODE} รอบ/รูปแบบ) ==")
    total, warned = 0, 0
    for base_seed, mode in ((1000, "bearing"), (2000, "overheat"), (3000, "imbalance")):
        leads = defaultdict(list)
        for i in range(RUNS_PER_MODE):
            res = run_until_failure(mode, seed=base_seed + i)
            total += 1
            warned += "warning" in res or "critical" in res
            for lvl, v in res.items():
                leads[lvl].append(v)
        for lvl in ("warning", "critical"):
            v = leads[lvl]
            if v:
                print(f"  {mode:10s} {lvl:8s}  median {statistics.median(v):5.1f} h   "
                      f"min {min(v):5.1f} h   ({len(v)}/{RUNS_PER_MODE} รอบ)")
    print(f"  => เตือนล่วงหน้าได้ {warned}/{total} ครั้ง ({100 * warned / total:.0f}%)\n")

    print(f"== False alarm: เครื่องปกติ {HEALTHY_RUNS} เครื่อง x {HEALTHY_HOURS} ชม. ==")
    agg: dict[str, int] = defaultdict(int)
    for i in range(HEALTHY_RUNS):
        for k, v in run_healthy(seed=50_000 + i).items():
            agg[k] += v
    per_1000h = 1000 / (HEALTHY_RUNS * HEALTHY_HOURS)
    if not agg:
        print("  ไม่มี alert เลย")
    for k, v in sorted(agg.items()):
        print(f"  {k:24s} {v * per_1000h:6.2f} ครั้ง / 1,000 ชม.")
    predictive_false = sum(v for k, v in agg.items() if k.split("/")[0] in PREDICTIVE_KINDS)
    print(f"  => แจ้งว่า 'เครื่องกำลังเสีย' ผิดๆ {predictive_false * per_1000h:.2f} ครั้ง / 1,000 ชม.")
    print("     (zscore/sensor_fault ในเครื่องปกติ = ระบบจับความผิดปกติของ 'เซนเซอร์' ได้ถูกต้อง ไม่ใช่เตือนผิด)")


if __name__ == "__main__":
    main()
