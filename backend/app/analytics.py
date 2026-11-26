"""
ตรวจจับความผิดปกติ + ประเมินอายุที่เหลือ (RUL) (เป็นส่วน Data Analytics)

ใช้ 4 เทคนิคในการตรวจจับและประเมิน เนื่องจากแต่ละแบบจับปัญหาคนละแบบ:

  1. Threshold (เกณฑ์ตายตัว)   จับค่าสูงเกินมาตรฐาน     ใช้ median ของ 5 ค่าล่าสุด กัน spike หลอก
  2. Robust z-score (MAD)     จับค่ากระโดดผิดปกติ     เทียบกับพฤติกรรมปกติของเครื่องนั้นเอง
  3. Stuck sensor            จับเซนเซอร์ค้าง         ค่าเดิมติดกันหลายครั้ง
  4. RUL (trend regression)   จับเครื่องที่เสื่อมแบบช้าๆ       เป็นส่วนที่รองรับปัญหาที่ 3 วิธีแรกอาจตรวจไม่พบ
"""
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from statistics import median

# เกณฑ์ 
VIB_WARNING = 2.8     # mm/s  เข้าโซน C: ควรวางแผนซ่อม
VIB_CRITICAL = 4.5    # mm/s  เข้าโซน D: เสี่ยงเสียหาย
VIB_FAILURE = 7.1     # mm/s  ถือว่าพัง (เป้าหมายที่ใช้คำนวณ RUL)
TEMP_WARNING = 75.0
TEMP_CRITICAL = 85.0
TEMP_FAILURE = 95.0

WINDOW = 60             # จำค่าล่าสุดกี่ค่าต่อเซนเซอร์
MIN_POINTS_ZSCORE = 30  # ต้องมีข้อมูลพอก่อนถึงจะเชื่อ z-score
ZSCORE_LIMIT = 6.0      # robust z เกินนี้ถือว่าผิดปกติ
STUCK_COUNT = 8         # ค่าเดิมติดกันกี่ครั้งถึงถือว่าค้าง
RUL_POINTS = 40         # ใช้กี่จุดล่าสุดในการหาแนวโน้ม
RUL_MIN_R2 = 0.6        # แนวโน้มต้องชัดพอ (R² สูง) ถึงจะรายงาน RUL
RUL_WARNING_H = 30.0
RUL_CRITICAL_H = 10.0
ALERT_COOLDOWN_S = 60   # ไม่แจ้งเรื่องเดิมซ้ำถี่เกินไป (alert fatigue)

LEVELS = {"info": 0, "warning": 1, "critical": 2}


@dataclass
class Alert:
    level: str
    kind: str
    metric: str | None
    value: float | None
    message: str


@dataclass
class Analysis:
    alerts: list[Alert]
    health: float            # 0-100 
    rul_h: float | None      # ชั่วโมงการทำงานที่เหลือโดยประมาณ 


def robust_zscore(x: float, values: list[float]) -> float:
    """
    ใช้ median และ MAD แทน mean และ std เพราะ mean/std ถูกดึงด้วยค่าผิดปกติง่าย แต่ median/MAD ทนต่อค่าผิดปกติมากกว่า
    """
    med = median(values)
    mad = median(abs(v - med) for v in values)
    if mad < 1e-9:
        return 0.0
    return 0.6745 * (x - med) / mad


def linear_fit(xs: list[float], ys: list[float]) -> tuple[float, float, float]:
    """Least squares: คืนค่า (slope, intercept, R²)"""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx < 1e-12:
        return 0.0, my, 0.0
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = my - slope * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    r2 = 1 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0
    return slope, intercept, r2


def estimate_rul(op_hours: list[float], values: list[float], limit: float, log_scale: bool) -> float | None:
    """
    ประเมินว่าอีกกี่ชั่วโมงค่าจะถึง limit โดยลากเส้นแนวโน้มต่อไปข้างหน้า

    """
    if len(values) < RUL_POINTS // 2:
        return None
    ys = [math.log(max(v, 1e-3)) for v in values] if log_scale else list(values)
    target = math.log(limit) if log_scale else limit
    slope, intercept, r2 = linear_fit(op_hours, ys)
    if slope <= 0 or r2 < RUL_MIN_R2:
        return None   
    now_h = op_hours[-1]
    current = slope * now_h + intercept
    return max(0.0, (target - current) / slope)


def health_score(vib: float, temp: float) -> float:
    """
    คะแนนสุขภาพ 0-100 แบบเข้าใจง่ายสำหรับหน้าจอเทียบว่าค่าปัจจุบันเข้าใกล้จุดพังแค่ไหน แล้วเอาด้านที่แย่ที่สุด
    """
    v = (vib - 1.0) / (VIB_FAILURE - 1.0)
    t = (temp - 55.0) / (TEMP_FAILURE - 55.0)
    worst = min(max(v, t, 0.0), 1.0)
    return round(100 * (1 - worst), 1)


@dataclass
class MachineAnalyzer:
    """เก็บประวัติสั้นๆ ของเครื่อง 1 เครื่อง แล้ววิเคราะห์ทุกครั้งที่มีข้อมูลใหม่"""
    machine_id: str
    clock: callable = time.time   # แยกออกมาเพื่อให้ test ควบคุมเวลาได้
    hist: dict[str, deque] = field(default_factory=lambda: {
        k: deque(maxlen=WINDOW) for k in ("vib_rms", "temp_c", "current_a", "op_hours")
    })
    _last_alert: dict[tuple, tuple[int, float]] = field(default_factory=dict)
    last_health: float = 100.0
    last_rul_h: float | None = None

    def reset(self) -> None:
        """เรียกหลังซ่อมบำรุง: ประวัติก่อนซ่อมไม่ใช่ตัวแทนของเครื่องหลังซ่อมแล้ว"""
        for d in self.hist.values():
            d.clear()
        self._last_alert.clear()
        self.last_rul_h = None

    def _emit(self, out: list[Alert], alert: Alert) -> None:
        """ส่ง alert ถ้าเป็นเรื่องใหม่ / รุนแรงขึ้น / หรือพ้นช่วง cooldown แล้ว"""
        key = (alert.kind, alert.metric)
        now = self.clock()
        level = LEVELS[alert.level]
        prev = self._last_alert.get(key)
        if prev is None or level > prev[0] or now - prev[1] >= ALERT_COOLDOWN_S:
            self._last_alert[key] = (level, now)
            out.append(alert)

    def update(self, r: dict) -> Analysis:
        out: list[Alert] = []
        vib, temp, current = r["vib_rms"], r["temp_c"], r["current_a"]

        if r.get("state") != "running":
            # เครื่องหยุด/พัง
            if r.get("state") == "fault":
                self._emit(out, Alert("critical", "fault", None, None, "เครื่องหยุดทำงานเพราะเกิดความเสียหาย"))
            return Analysis(out, self.last_health, self.last_rul_h)

        h = self.hist

        # z-score: เทียบกับประวัติก่อนใส่ค่าใหม่
        for metric, x in (("vib_rms", vib), ("temp_c", temp), ("current_a", current)):
            if len(h[metric]) >= MIN_POINTS_ZSCORE:
                z = robust_zscore(x, list(h[metric]))
                if abs(z) >= ZSCORE_LIMIT:
                    self._emit(out, Alert("warning", "zscore", metric, x,
                                          f"{metric} เปลี่ยนกะทันหัน (z={z:.1f}) อาจเป็น spike ของเซนเซอร์ ถ้าไม่เกิดซ้ำ"))

        for metric, x in (("vib_rms", vib), ("temp_c", temp), ("current_a", current), ("op_hours", r["op_hours"])):
            h[metric].append(x)

        # threshold: ใช้ median 5 ค่าล่าสุด spike ครั้งเดียวจึงไม่ทำให้แจ้งเตือนวิกฤตหลอก
        vib_s = median(list(h["vib_rms"])[-5:])
        temp_s = median(list(h["temp_c"])[-5:])
        if vib_s >= VIB_CRITICAL:
            self._emit(out, Alert("critical", "threshold", "vib_rms", vib_s, f"ความสั่น {vib_s:.2f} mm/s เกินระดับวิกฤต"))
        elif vib_s >= VIB_WARNING:
            self._emit(out, Alert("warning", "threshold", "vib_rms", vib_s, f"ความสั่น {vib_s:.2f} mm/s สูงกว่าปกติ"))
        if temp_s >= TEMP_CRITICAL:
            self._emit(out, Alert("critical", "threshold", "temp_c", temp_s, f"อุณหภูมิ {temp_s:.1f} °C เกินระดับวิกฤต"))
        elif temp_s >= TEMP_WARNING:
            self._emit(out, Alert("warning", "threshold", "temp_c", temp_s, f"อุณหภูมิ {temp_s:.1f} °C สูงกว่าปกติ"))

        # level shift: ค่ายกระดับค้าง ไม่ใช่ spike ครั้งเดียว (เช่น ใบพัดเสียสมดุลกะทันหัน)
        # ถ้าไม่จัดการจะทำนายว่าเครื่องจะพังเร็วเกินจริงมาก
        # วิธีแก้: ถือว่าเครื่องเข้าสู่สภาวะใหม่แล้วเริ่มเก็บประวัติใหม่จากจุดนี้ (change-point reset)
        older = list(h["vib_rms"])[:-5]
        if len(older) >= MIN_POINTS_ZSCORE and robust_zscore(vib_s, older) >= ZSCORE_LIMIT:
            self._emit(out, Alert("warning", "shift", "vib_rms", vib_s,
                                  f"ความสั่นยกระดับขึ้นค้างที่ {vib_s:.2f} mm/s (ไม่ใช่ spike) ควรตรวจเครื่อง"))
            for d in h.values():
                recent = list(d)[-5:]
                d.clear()
                d.extend(recent)

        #stuck sensor
        recent_t = list(h["temp_c"])[-STUCK_COUNT:]
        if len(recent_t) == STUCK_COUNT and len(set(recent_t)) == 1:
            self._emit(out, Alert("warning", "sensor_fault", "temp_c", temp,
                                  f"เซนเซอร์อุณหภูมิส่งค่าเดิม {STUCK_COUNT} ครั้งติด อาจค้าง"))

        # RUL: ดูทั้งความสั่นและอุณหภูมิ แล้วเชื่อด้านที่จะพังก่อน
        hours = list(h["op_hours"])[-RUL_POINTS:]
        ruls = [
            estimate_rul(hours, list(h["vib_rms"])[-RUL_POINTS:], VIB_FAILURE, log_scale=True),
            estimate_rul(hours, list(h["temp_c"])[-RUL_POINTS:], TEMP_FAILURE, log_scale=False),
        ]
        ruls = [x for x in ruls if x is not None]
        # รายงาน RUL เฉพาะเมื่อเครื่องเริ่มผิดปกติแล้วจริงๆ
        degrading = vib_s >= VIB_WARNING * 0.6 or temp_s >= TEMP_WARNING * 0.9
        self.last_rul_h = round(min(ruls), 1) if ruls and degrading else None
        if self.last_rul_h is not None:
            if self.last_rul_h <= RUL_CRITICAL_H:
                self._emit(out, Alert("critical", "rul", None, self.last_rul_h,
                                      f"คาดว่าจะเสียภายใน ~{self.last_rul_h:.0f} ชม.การทำงาน ควรหยุดซ่อม"))
            elif self.last_rul_h <= RUL_WARNING_H:
                self._emit(out, Alert("warning", "rul", None, self.last_rul_h,
                                      f"คาดว่าจะเสียภายใน ~{self.last_rul_h:.0f} ชม.การทำงาน ควรวางแผนซ่อม"))

        self.last_health = health_score(vib_s, temp_s)
        return Analysis(out, self.last_health, self.last_rul_h)
