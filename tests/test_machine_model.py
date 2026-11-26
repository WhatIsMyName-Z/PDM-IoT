"""ทดสอบว่าตัวจำลองให้ข้อมูลที่สมเหตุสมผล"""
import random

import pytest

from machine_model import FAULT_MODES, Machine


def test_healthy_machine_stays_in_normal_range():
    m = Machine("M", "l", random.Random(1), fault_probability=0.0)
    for _ in range(500):
        r = m.step(1.0, sensor_fault_rate=0)
        assert 0.5 < r.vib_rms < 2.0
        assert 35 < r.temp_c < 60
        assert r.state == "running"


@pytest.mark.parametrize("mode", FAULT_MODES)
def test_every_fault_mode_eventually_fails(mode):
    m = Machine("M", "l", random.Random(2), fault_probability=1.0)
    m.fault_mode, m.fault_onset_h, m.fault_ttf_h = mode, 10, 100
    for _ in range(200):
        if m.step(1.0, 0).state == "fault":
            break
    assert m.state == "fault"
    assert m.op_hours <= 111


def test_stop_and_maintenance_commands():
    m = Machine("M", "l", random.Random(3), fault_probability=0.0)
    m.step(1.0)
    assert m.apply_command("stop") and m.state == "stopped"
    h = m.op_hours
    r = m.step(1.0)
    assert r.rpm == 0 and r.current_a == 0 and m.op_hours == h   # หยุดแล้วชั่วโมงทำงานไม่เพิ่ม
    m.state = "fault"
    assert not m.apply_command("start")         # เครื่องพังสั่ง start ไม่ได้ 
    assert m.apply_command("maintenance") and m.state == "running"


def test_same_seed_same_data():
    a = [Machine("M", "l", random.Random(9)).step(1.0) for _ in range(1)]
    b = [Machine("M", "l", random.Random(9)).step(1.0) for _ in range(1)]
    assert a == b
