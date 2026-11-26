"""
แบบจำลองเครื่องจักรประเภทมอเตอร์และปั๊ม สำหรับสร้างข้อมูลเซนเซอร์ที่ใกล้เคียงกับเครื่องจริง

โมดูลนี้ไม่เกี่ยวข้องกับ MQTT และบรรจุเฉพาะแบบจำลองทางกายภาพของเครื่อง
จึงสามารถทดสอบแยกต่างหากได้ง่าย โดยดูตัวอย่างได้ที่ tests/test_machine_model.py

แนวคิดหลัก:
- เครื่องที่อยู่ในสภาพปกติมีค่าพื้นฐานเฉพาะตัว ร่วมกับสัญญาณรบกวนเล็กน้อย
  และรอบการเปลี่ยนแปลงของโหลดรายวัน โดยช่วงกลางวันมีโหลดสูงกว่าช่วงกลางคืน
- เครื่องบางเครื่องถูกกำหนดให้เกิดความเสียหายแบบค่อยเป็นค่อยไป โดยเริ่มต้นที่เวลาซึ่งสุ่มขึ้น
  ค่าจากเซนเซอร์จะเสื่อมลงตามลำดับจนเครื่องขัดข้อง ซึ่งเป็นสถานการณ์ที่ระบบ Predictive Maintenance
  จำเป็นต้องตรวจจับให้ได้ก่อนเกิดความเสียหายจริง
- แบบจำลองยังรวมความผิดพลาดที่เกิดจากเซนเซอร์เอง เช่น ค่ากระโดดและค่าค้าง
  เพื่อให้ระบบต้องแยกแยะระหว่างความผิดปกติของเครื่องกับความผิดปกติของเซนเซอร์
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

# เกณฑ์ความสั่นสะเทือน มีหน่วยเป็น mm/s RMS โดยอ้างอิงแนวคิดการแบ่งโซนตามมาตรฐาน ISO 10816
# สำหรับเครื่องจักรขนาดกลาง โซน A และ B หมายถึงสภาพดี โซน C หมายถึงควรเฝ้าระวัง
# และโซน D หมายถึงระดับที่เป็นอันตรายต่อเครื่อง
VIB_FAILURE = 7.1     # ค่าความสั่นสะเทือนที่ถือว่าเครื่องขัดข้องในแบบจำลอง
TEMP_FAILURE = 95.0   # อุณหภูมิที่ถือว่าเครื่องขัดข้องในแบบจำลอง มีหน่วยเป็นองศาเซลเซียส

# รูปแบบความเสียหายที่พบบ่อยในมอเตอร์และปั๊มจริง ได้แก่ ลูกปืนสึก ความร้อนสูงเกิน และความไม่สมดุล
FAULT_MODES = ("bearing", "overheat", "imbalance")


@dataclass
class Reading:
    """ค่าที่อ่านได้จากเซนเซอร์หนึ่งครั้ง ซึ่งตรงกับข้อความ MQTT หนึ่งข้อความ"""
    vib_rms: float
    temp_c: float
    current_a: float
    rpm: float
    op_hours: float
    state: str


@dataclass
class Machine:
    machine_id: str
    line: str
    rng: random.Random
    fault_probability: float = 0.6   # ความน่าจะเป็นที่เครื่องจะเกิดความเสียหายในรอบอายุการใช้งานหนึ่งรอบ

    # --- ค่าพื้นฐานเฉพาะเครื่อง สุ่มเพียงครั้งเดียวขณะสร้างอ็อบเจกต์ เพื่อให้แต่ละเครื่องมีลักษณะแตกต่างกัน ---
    base_vib: float = field(init=False)
    base_current: float = field(init=False)
    base_rpm: float = field(init=False)
    ambient_c: float = field(init=False)

    # --- สถานะที่เปลี่ยนแปลงตามเวลา ---
    state: str = field(init=False, default="running")   # สถานะที่เป็นไปได้ คือ running, stopped และ fault
    op_hours: float = field(init=False, default=0.0)     # ชั่วโมงการทำงานสะสม
    sim_hours: float = field(init=False, default=0.0)    # เวลาในโลกจำลอง ใช้คำนวณรอบโหลดรายวัน
    temp_c: float = field(init=False)                    # อุณหภูมิมีความหน่วง จึงต้องเก็บค่าก่อนหน้าไว้

    fault_mode: str | None = field(init=False, default=None)
    fault_onset_h: float = field(init=False, default=0.0)   # ชั่วโมงการทำงานสะสมที่ความเสียหายเริ่มต้น
    fault_ttf_h: float = field(init=False, default=0.0)     # ระยะเวลาเป็นชั่วโมงตั้งแต่เริ่มเสื่อมจนเครื่องขัดข้อง

    # สถานะความผิดพลาดของเซนเซอร์ที่กำลังเกิดขึ้น
    _stuck_temp_left: int = field(init=False, default=0)
    _stuck_temp_value: float = field(init=False, default=0.0)

    def __post_init__(self) -> None:
        r = self.rng
        self.base_vib = r.uniform(0.8, 1.4)
        self.base_current = r.uniform(11.0, 13.0)
        self.base_rpm = r.uniform(1475, 1490)      # มอเตอร์ 4 ขั้ว ที่ความถี่ไฟฟ้า 50 Hz
        self.ambient_c = r.uniform(30, 34)         # อุณหภูมิแวดล้อมของโรงงานในประเทศไทย
        self.temp_c = self.ambient_c + 18
        self._plan_next_fault()

    # ------------------------------------------------------------------
    # การวางแผนความเสียหาย
    # ------------------------------------------------------------------
    def _plan_next_fault(self) -> None:
        """สุ่มรูปแบบความเสียหายของรอบอายุการใช้งานนี้ รวมถึงเวลาที่เริ่มเสื่อมและความเร็วในการเสื่อม"""
        r = self.rng
        if r.random() < self.fault_probability:
            self.fault_mode = r.choice(FAULT_MODES)
            self.fault_onset_h = self.op_hours + r.uniform(5, 60)
            self.fault_ttf_h = r.uniform(60, 160)
        else:
            self.fault_mode = None

    def degradation(self) -> float:
        """ความคืบหน้าของความเสียหาย โดย 0.0 หมายถึงยังปกติ และ 1.0 หมายถึงเครื่องขัดข้อง"""
        if self.fault_mode is None or self.op_hours < self.fault_onset_h:
            return 0.0
        return min(1.0, (self.op_hours - self.fault_onset_h) / self.fault_ttf_h)

    # ------------------------------------------------------------------
    # คำสั่งจากระบบ ซึ่งฝั่ง backend ส่งมาทาง MQTT
    # ------------------------------------------------------------------
    def apply_command(self, cmd: str) -> bool:
        if cmd == "stop" and self.state == "running":
            self.state = "stopped"
            return True
        if cmd == "start" and self.state == "stopped":
            self.state = "running"
            return True
        if cmd == "maintenance":
            # การซ่อมบำรุงจะเปลี่ยนชิ้นส่วนที่เสื่อมสภาพ ทำให้ความเสียหายหมดไป
            # จากนั้นจึงวางแผนความเสียหายของรอบอายุการใช้งานใหม่
            self.state = "running"
            self._plan_next_fault()
            return True
        return False

    # ------------------------------------------------------------------
    # อ่านค่าเซนเซอร์หนึ่งครั้ง เมื่อเวลาผ่านไป dt_hours ชั่วโมง
    # ------------------------------------------------------------------
    def step(self, dt_hours: float, sensor_fault_rate: float = 1.0) -> Reading:
        r = self.rng
        self.sim_hours += dt_hours
        running = self.state == "running"
        if running:
            self.op_hours += dt_hours   # นับชั่วโมงการทำงานเฉพาะขณะที่เครื่องหมุน

        d = self.degradation()

        # รอบโหลดรายวัน ช่วงกลางวันมีโหลดประมาณ 100% และช่วงกลางคืนลดลงเหลือประมาณ 70%
        load = 0.85 + 0.15 * math.sin(2 * math.pi * (self.sim_hours % 24 - 6) / 24)

        if running:
            vib = self.base_vib * (0.9 + 0.1 * load)
            current = self.base_current * load
            rpm = self.base_rpm
            temp_target = self.ambient_c + 18 * load

            # ผลของความเสียหายแต่ละรูปแบบที่มีต่อเซนเซอร์ โดยอิงลักษณะที่พบในเครื่องจริง
            if self.fault_mode == "bearing":
                # ลูกปืนสึก การสั่นสะเทือนเพิ่มขึ้นแบบเร่งตามเวลา และอุณหภูมิสูงขึ้นเล็กน้อย
                vib += 6.5 * d ** 2.2
                temp_target += 12 * d
                current += 0.5 * d
            elif self.fault_mode == "overheat":
                # ระบบระบายความร้อนหรือการหล่อลื่นบกพร่อง อุณหภูมิสูงขึ้นชัดเจนและกระแสไฟฟ้าเพิ่มขึ้น
                temp_target += 50 * d ** 1.5
                current += 2.0 * d
                vib += 0.8 * d
            elif self.fault_mode == "imbalance":
                # ใบพัดไม่สมดุล การสั่นสะเทือนเพิ่มขึ้นทันทีเมื่อเริ่มเสื่อม จากนั้นเพิ่มขึ้นเป็นเส้นตรง
                vib += (1.5 + 5.5 * d) if d > 0 else 0
                temp_target += 5 * d
            rpm -= 15 * d   # เครื่องที่เสื่อมสภาพจะมีความเร็วรอบลดลงเล็กน้อย

            vib += r.gauss(0, 0.08)
            current += r.gauss(0, 0.15)
            rpm += r.gauss(0, 2)
        else:
            # เมื่อเครื่องหยุดหรือขัดข้อง จะไม่มีการหมุนและไม่ใช้กระแสไฟฟ้า
            # อุณหภูมิจะลดลงอย่างต่อเนื่องจนเท่ากับอุณหภูมิแวดล้อม
            vib = abs(r.gauss(0.05, 0.02))
            current = 0.0
            rpm = 0.0
            temp_target = self.ambient_c

        # อุณหภูมิเปลี่ยนแปลงอย่างค่อยเป็นค่อยไปตามความหน่วงทางความร้อน ไม่เปลี่ยนทันที
        self.temp_c += 0.2 * (temp_target - self.temp_c)
        temp = self.temp_c + r.gauss(0, 0.3)

        # ตรวจสอบว่าเครื่องขัดข้องหรือไม่
        if running and (d >= 1.0 or vib >= VIB_FAILURE or self.temp_c >= TEMP_FAILURE):
            self.state = "fault"

        # --- ความผิดพลาดของเซนเซอร์ ซึ่งไม่ได้เกิดจากตัวเครื่อง ---
        if r.random() < 0.003 * sensor_fault_rate:
            vib *= r.uniform(3, 5)              # ค่ากระโดดชั่วขณะ เช่น เกิดจากแรงกระแทกภายนอก
        if self._stuck_temp_left == 0 and r.random() < 0.001 * sensor_fault_rate:
            self._stuck_temp_left = r.randint(8, 15)   # เซนเซอร์ค้าง ส่งค่าเดิมซ้ำหลายรอบ
            self._stuck_temp_value = temp
        if self._stuck_temp_left > 0:
            self._stuck_temp_left -= 1
            temp = self._stuck_temp_value

        return Reading(
            vib_rms=round(max(vib, 0.0), 3),
            temp_c=round(temp, 2),
            current_a=round(max(current, 0.0), 2),
            rpm=round(max(rpm, 0.0), 1),
            op_hours=round(self.op_hours, 3),
            state=self.state,
        )
