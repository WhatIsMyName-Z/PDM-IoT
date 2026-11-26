"""
ตัวจำลองอุปกรณ์ IoT หลายเครื่อง โดยแต่ละเครื่องทำงานเป็น MQTT client อิสระ

การทำงานของแต่ละอุปกรณ์ซึ่งสอดคล้องกับเฟิร์มแวร์ ESP32 ใน firmware/wokwi/main.py:
  1. เชื่อมต่อ broker และตั้งค่า Last Will เพื่อประกาศสถานะ offline เมื่อขาดการเชื่อมต่อ
  2. ประกาศสถานะ online แบบ retained และสมัครรับหัวข้อคำสั่ง
  3. ส่ง telemetry ทุก interval_s วินาที พร้อมเลขลำดับ seq
  4. เมื่อเครือข่ายขัดข้อง จะเก็บข้อมูลในบัฟเฟอร์และส่งย้อนหลังเมื่อกลับมาเชื่อมต่อ

โครงสร้างหัวข้อ topic:
  factory/<line>/<machine_id>/telemetry   อุปกรณ์ไปยังระบบ, QoS 1
  factory/<line>/<machine_id>/status      อุปกรณ์ไปยังระบบ, QoS 1, retained
  factory/<line>/<machine_id>/cmd         ระบบไปยังอุปกรณ์, QoS 1
  factory/<line>/<machine_id>/ack         อุปกรณ์ไปยังระบบ เพื่อยืนยันการรับคำสั่ง
"""
from __future__ import annotations

import json
import logging
import os
import random
import time

import paho.mqtt.client as mqtt
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.reasoncodes import ReasonCode

from machine_model import Machine

log = logging.getLogger("simulator")

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MACHINE_COUNT = int(os.getenv("MACHINE_COUNT", "8"))
SIM_HOURS_PER_SECOND = float(os.getenv("SIM_HOURS_PER_SECOND", "0.5"))
DEFAULT_INTERVAL_S = float(os.getenv("DEFAULT_INTERVAL_S", "2"))
FAULT_RATE = float(os.getenv("FAULT_RATE", "1.0"))   # ตัวคูณความถี่ของปัญหาเซนเซอร์และเครือข่าย
SEED = os.getenv("SEED")                              # กำหนดค่าเพื่อให้ผลการจำลองซ้ำได้
BUFFER_MAX = 500                                      # จำกัดขนาดบัฟเฟอร์ตามหน่วยความจำของอุปกรณ์จริง


class SimulatedDevice:
    def __init__(self, machine: Machine, rng: random.Random):
        self.m = machine
        self.rng = rng
        self.base = f"factory/{machine.line}/{machine.machine_id}"
        self.interval_s = DEFAULT_INTERVAL_S
        self.next_due = time.monotonic() + rng.uniform(0, self.interval_s)  # กระจายเวลาส่งของแต่ละเครื่อง
        self.seq = 0
        self.buffer: list[dict] = []
        self.outage_until = 0.0   # เวลาที่เครือข่ายจำลองจะกลับมาเชื่อมต่อ

        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"sim-{machine.machine_id}",
            protocol=mqtt.MQTTv5,
        )
        # Last Will: broker จะเผยแพร่ข้อความนี้แทนเมื่ออุปกรณ์ขาดการติดต่อเกินเวลา keepalive
        self.client.will_set(f"{self.base}/status", "offline", qos=1, retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        # เมื่อขาดการเชื่อมต่อ paho จะเชื่อมต่อใหม่โดยเพิ่มเวลารอแบบ exponential backoff สูงสุด 30 วินาที
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)

    # ---------------- MQTT callbacks ----------------
    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.warning("%s connect failed: %s", self.m.machine_id, reason_code)
            return
        client.publish(f"{self.base}/status", "online", qos=1, retain=True)
        client.subscribe(f"{self.base}/cmd", qos=1)
        self._flush_buffer()

    def _on_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload)
            cmd = data["cmd"]
        except (ValueError, KeyError):
            log.warning("%s: bad command payload %r", self.m.machine_id, msg.payload)
            return
        ok = True
        if cmd == "set_interval":
            # จำกัดช่วงค่า เพื่อป้องกันคำสั่งที่ผิดพลาดทำให้ส่งข้อมูลถี่เกินไป
            self.interval_s = min(max(float(data.get("value", DEFAULT_INTERVAL_S)), 0.5), 60)
        else:
            ok = self.m.apply_command(cmd)
        log.info("%s: cmd=%s ok=%s", self.m.machine_id, cmd, ok)
        client.publish(f"{self.base}/ack", json.dumps({"cmd": cmd, "ok": ok, "ts": _now_ms()}), qos=1)

    # ---------------- การส่งข้อมูล ----------------
    def start(self):
        self.client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=10)
        self.client.loop_start()   # paho สร้าง thread สำหรับรับและส่งข้อมูล

    def _publish(self, payload: dict) -> bool:
        if not self.client.is_connected():
            return False
        info = self.client.publish(f"{self.base}/telemetry", json.dumps(payload), qos=1)
        return info.rc == mqtt.MQTT_ERR_SUCCESS

    def _flush_buffer(self):
        if not self.buffer:
            return
        log.info("%s: flushing %d buffered readings", self.m.machine_id, len(self.buffer))
        while self.buffer and self._publish(self.buffer[0]):
            self.buffer.pop(0)

    def tick(self, now: float):
        if now < self.next_due:
            return
        self.next_due = now + self.interval_s

        reading = self.m.step(self.interval_s * SIM_HOURS_PER_SECOND, FAULT_RATE)
        self.seq += 1
        payload = {
            "ts": _now_ms(),
            "seq": self.seq,
            "vib_rms": reading.vib_rms,
            "temp_c": reading.temp_c,
            "current_a": reading.current_a,
            "rpm": reading.rpm,
            "op_hours": reading.op_hours,
            "state": reading.state,
            "interval_s": self.interval_s,
        }

        # จำลองแพ็กเก็ตสูญหาย โดย backend จะตรวจพบจากเลข seq ที่ขาดหาย
        if self.rng.random() < 0.005 * FAULT_RATE:
            return

        # จำลองเครือข่ายขัดข้อง โดยตัดการเชื่อมต่อพร้อมขอให้ broker ส่ง Last Will ด้วย MQTT v5 reason code 0x04
        if now >= self.outage_until and self.client.is_connected() and self.rng.random() < 0.0005 * FAULT_RATE:
            self.outage_until = now + self.rng.uniform(15, 30)
            log.info("%s: simulated network outage", self.m.machine_id)
            self.client.disconnect(reasoncode=ReasonCode(PacketTypes.DISCONNECT, "Disconnect with will message"))
        if self.outage_until and now >= self.outage_until:
            # เครือข่ายกลับมา จึงเริ่ม network loop ใหม่ เพราะ loop เดิมหยุดไปพร้อมการ disconnect
            self.outage_until = 0.0
            self.client.loop_stop()
            self.client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=10)
            self.client.loop_start()

        if self.outage_until or not self._publish(payload):
            payload["buffered"] = True
            self.buffer.append(payload)
            if len(self.buffer) > BUFFER_MAX:
                self.buffer.pop(0)   # บัฟเฟอร์เต็ม จึงลบข้อมูลที่เก่าที่สุดออก


def _now_ms() -> int:
    return int(time.time() * 1000)


def build_devices(count: int, rng: random.Random) -> list[SimulatedDevice]:
    devices = []
    for i in range(count):
        line = "line1" if i < (count + 1) // 2 else "line2"
        machine = Machine(machine_id=f"M{i + 1:02d}", line=line, rng=random.Random(rng.random()))
        devices.append(SimulatedDevice(machine, rng=random.Random(rng.random())))
    return devices


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    rng = random.Random(int(SEED)) if SEED else random.Random()
    devices = build_devices(MACHINE_COUNT, rng)
    for dev in devices:
        log.info("%s on %s: planned fault=%s", dev.m.machine_id, dev.m.line, dev.m.fault_mode)
        dev.start()
    while True:
        now = time.monotonic()
        for dev in devices:
            dev.tick(now)
        time.sleep(0.05)


if __name__ == "__main__":
    main()
