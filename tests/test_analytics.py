"""ทดสอบตรรกะการวิเคราะห์ โดยป้อนข้อมูลที่เรารู้คำตอบอยู่แล้ว"""
import random

from app.analytics import (
    ALERT_COOLDOWN_S, MachineAnalyzer, estimate_rul, health_score, linear_fit, robust_zscore,
)


def reading(vib=1.0, temp=50.0, current=12.0, op_hours=0.0, state="running"):
    return {"vib_rms": vib, "temp_c": temp, "current_a": current, "rpm": 1480, "op_hours": op_hours, "state": state}


def make_analyzer():
    clock = [0.0]
    return MachineAnalyzer("T", clock=lambda: clock[0]), clock


def feed_normal(a, clock, n=40, start_h=0.0, rng=None):
    rng = rng or random.Random(0)
    for i in range(n):
        clock[0] += 2
        a.update(reading(vib=1.0 + rng.gauss(0, 0.05), temp=50 + rng.gauss(0, 0.3), op_hours=start_h + i))


def kinds(analysis):
    return {(x.kind, x.level) for x in analysis.alerts}


#ฟังก์ชันคณิตศาสตร์
def test_linear_fit_exact_line():
    slope, intercept, r2 = linear_fit([0, 1, 2, 3], [1, 3, 5, 7])
    assert abs(slope - 2) < 1e-9 and abs(intercept - 1) < 1e-9 and abs(r2 - 1) < 1e-9


def test_robust_zscore_ignores_existing_outlier():
    values = [1.0, 1.1, 0.9, 1.0, 1.05, 0.95] * 5 + [50.0] 
    assert robust_zscore(5.0, values) > 6 


def test_rul_none_when_flat():
    hours = list(range(30))
    assert estimate_rul(hours, [1.0] * 30, 7.1, log_scale=True) is None


def test_rul_reasonable_for_linear_trend():
    hours = list(range(30))
    temps = [60 + h for h in hours]  
    rul = estimate_rul(hours, temps, 95.0, log_scale=False)
    assert abs(rul - 6) < 0.01


def test_health_score_bounds():
    assert health_score(1.0, 40) == 100
    assert health_score(7.1, 40) == 0
    assert 0 < health_score(3.0, 60) < 100


# ---------------- กฎการแจ้งเตือน ----------------
def test_normal_machine_no_alerts():
    a, clock = make_analyzer()
    rng = random.Random(1)
    alerts = []
    for i in range(200):
        clock[0] += 2
        alerts += a.update(reading(vib=1.0 + rng.gauss(0, 0.05), temp=50 + rng.gauss(0, 0.3), op_hours=i)).alerts
    assert alerts == []


def test_single_spike_is_zscore_not_critical():
    a, clock = make_analyzer()
    feed_normal(a, clock)
    res = a.update(reading(vib=8.0, op_hours=41))
    assert ("zscore", "warning") in kinds(res)
    assert not any(x.level == "critical" for x in res.alerts)   # median กัน spike


def test_sustained_high_vibration_is_critical():
    a, clock = make_analyzer()
    feed_normal(a, clock)
    found = set()
    for i in range(6):
        clock[0] += 2
        found |= kinds(a.update(reading(vib=5.0, op_hours=41 + i)))
    assert ("threshold", "critical") in found


def test_level_shift_resets_history():
    a, clock = make_analyzer()
    feed_normal(a, clock)
    found = set()
    for i in range(6):
        clock[0] += 2
        found |= kinds(a.update(reading(vib=2.6, op_hours=41 + i)))
    assert ("shift", "warning") in found
    assert len(a.hist["vib_rms"]) < 15 


def test_stuck_sensor_detected():
    a, clock = make_analyzer()
    feed_normal(a, clock)
    found = set()
    for i in range(8):
        clock[0] += 2
        found |= kinds(a.update(reading(temp=51.23, op_hours=41 + i)))
    assert ("sensor_fault", "warning") in found


def test_cooldown_and_escalation():
    a, clock = make_analyzer()
    feed_normal(a, clock)
    first = []
    for i in range(10):
        clock[0] += 1
        first += [x for x in a.update(reading(vib=3.0, op_hours=41 + i)).alerts if x.kind == "threshold"]
    assert len(first) == 1                 
    escalated = []
    for i in range(5):
        clock[0] += 1
        escalated += [x for x in a.update(reading(vib=5.0, op_hours=51 + i)).alerts if x.kind == "threshold"]
    assert [x.level for x in escalated] == ["critical"]  
    clock[0] += ALERT_COOLDOWN_S
    assert any(x.kind == "threshold" for x in a.update(reading(vib=5.0, op_hours=60)).alerts)


def test_stopped_machine_not_added_to_history():
    a, clock = make_analyzer()
    feed_normal(a, clock, n=10)
    a.update(reading(vib=0.05, temp=33, state="stopped"))
    assert len(a.hist["vib_rms"]) == 10


def test_rul_reported_for_degrading_machine():
    a, clock = make_analyzer()
    feed_normal(a, clock)
    rul = None
    for i in range(40):
        clock[0] += 2
        rul = a.update(reading(vib=1.7 * 1.04 ** i, op_hours=41 + i)).rul_h
    assert rul is not None and rul < 30
