"""
รับข้อความ MQTT จากอุปกรณ์ทุกเครื่องจากนั้นทำการตรวจสอบ วิเคราะห์ บันทึก DB และกระจายไปหน้า dashboard
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

import aiomqtt

from . import config
from .analytics import MachineAnalyzer
from .db import Database
from .ws import Hub

log = logging.getLogger("ingest")

REQUIRED = ("vib_rms", "temp_c", "current_a", "rpm")
MAX_AGE_S = 7 * 24 * 3600   # ยอมรับข้อมูลย้อนหลังได้ไม่เกิน 7 วัน


class Ingestor:
    def __init__(self, db: Database, hub: Hub):
        self.db = db
        self.hub = hub
        self.client: aiomqtt.Client | None = None
        self.analyzers: dict[str, MachineAnalyzer] = {}
        self.lines: dict[str, str] = {}       # machine_id -> line (ใช้ประกอบ topic ตอนส่งคำสั่ง)
        self.last_seq: dict[str, int] = {}
        self.connected = False

    # ------------------------------------------------------------------
    async def run(self) -> None:
        """ต่อ broker และวนรับข้อความตลอดไป ถ้าหลุดจะรอแล้วต่อใหม่เอง"""
        while True:
            try:
                async with aiomqtt.Client(config.MQTT_HOST, config.MQTT_PORT, identifier="pdm-backend") as client:
                    self.client = client
                    self.connected = True
                    log.info("connected to MQTT %s:%s", config.MQTT_HOST, config.MQTT_PORT)
                    for t in ("telemetry", "status", "ack"):
                        await client.subscribe(f"factory/+/+/{t}", qos=1)
                    async for msg in client.messages:
                        try:
                            await self.handle(str(msg.topic), msg.payload)
                        except Exception: 
                            log.exception("failed to handle message on %s", msg.topic)
            except aiomqtt.MqttError as e:
                self.connected = False
                self.client = None
                log.warning("MQTT disconnected (%s); retrying in 3s", e)
                await asyncio.sleep(3)

    async def handle(self, topic: str, payload: bytes) -> None:
        parts = topic.split("/")
        if len(parts) != 4 or parts[0] != "factory":
            return
        _, line, machine_id, kind = parts
        if machine_id not in self.lines:
            await self.db.upsert_machine(machine_id, line)
            self.lines[machine_id] = line
            self.analyzers[machine_id] = MachineAnalyzer(machine_id)

        if kind == "telemetry":
            await self._telemetry(machine_id, payload)
        elif kind == "status":
            await self._status(machine_id, payload.decode(errors="replace").strip())
        elif kind == "ack":
            data = json.loads(payload)
            if data.get("cmd") == "maintenance" and data.get("ok"):
                self.analyzers[machine_id].reset()
            await self.hub.broadcast({"type": "ack", "machine_id": machine_id, **data})

    # ------------------------------------------------------------------
    async def _telemetry(self, machine_id: str, payload: bytes) -> None:
        #ตรวจความถูกต้อง
        try:
            r = json.loads(payload)
            for k in REQUIRED:
                r[k] = float(r[k])
        except (ValueError, KeyError, TypeError):
            log.warning("%s: invalid telemetry %r", machine_id, payload[:200])
            return
        r.setdefault("state", "running")
        r.setdefault("op_hours", 0.0)

        #เวลา: ใช้เวลาจากอุปกรณ์ถ้าสมเหตุสมผล
        #ถ้าอุปกรณ์ไม่มีนาฬิกา ใช้เวลาที่ server ได้รับแทน
        now = time.time()
        ts_s = r.get("ts", 0) / 1000
        if not (now - MAX_AGE_S < ts_s < now + 60):
            ts_s = now
        ts = datetime.fromtimestamp(ts_s, tz=timezone.utc)

        #ตรวจข้อความหายจากเลขลำดับ (seq)
        missing = 0
        seq = r.get("seq")
        if isinstance(seq, int):
            prev = self.last_seq.get(machine_id)
            if prev is not None and seq > prev + 1:
                missing = seq - prev - 1
            if prev is None or seq > prev:
                self.last_seq[machine_id] = seq
            elif seq < prev and not r.get("buffered"):
                # seq ลดลง: อุปกรณ์รีบูต (seq เริ่มนับใหม่จาก 1) ไม่ถือว่าข้อมูลหาย เริ่มนับใหม่ตาม
                self.last_seq[machine_id] = seq

        #วิเคราะห์
        analysis = self.analyzers[machine_id].update(r)

        #บันทึก + กระจาย
        await self.db.insert_telemetry(machine_id, ts, r, missing)
        await self.hub.broadcast({
            "type": "telemetry", "machine_id": machine_id, "ts": ts.isoformat(),
            "vib_rms": r["vib_rms"], "temp_c": r["temp_c"], "current_a": r["current_a"], "rpm": r["rpm"],
            "op_hours": r["op_hours"], "state": r["state"], "buffered": bool(r.get("buffered")),
            "health": analysis.health, "rul_h": analysis.rul_h,
        })
        if missing >= 5:
            await self._alert(machine_id, "warning", "data_gap", None, missing, f"ข้อมูลหาย {missing} ข้อความ")
        for a in analysis.alerts:
            await self._alert(machine_id, a.level, a.kind, a.metric, a.value, a.message)
            # closed-loop control (ถ้าเปิดไว้): ความสั่นวิกฤต -> สั่งหยุดเครื่องอัตโนมัติ
            if config.AUTO_STOP_ON_CRITICAL and a.level == "critical" and a.metric == "vib_rms" and r["state"] == "running":
                await self.send_command(machine_id, "stop", None, source="auto")

    async def _status(self, machine_id: str, status: str) -> None:
        if status not in ("online", "offline"):
            return
        await self.db.set_status(machine_id, status)
        await self.hub.broadcast({"type": "status", "machine_id": machine_id, "status": status})
        if status == "offline":
            await self._alert(machine_id, "warning", "offline", None, None, "อุปกรณ์ขาดการเชื่อมต่อ (Last Will)")

    async def _alert(self, machine_id, level, kind, metric, value, message) -> None:
        row = await self.db.insert_alert(machine_id, level, kind, metric, value, message)
        await self.hub.broadcast({"type": "alert", **row})

    # ------------------------------------------------------------------
    async def send_command(self, machine_id: str, cmd: str, value, source: str = "user") -> None:
        if self.client is None:
            raise RuntimeError("MQTT not connected")
        line = self.lines[machine_id]
        body = {"cmd": cmd, "source": source}
        if value is not None:
            body["value"] = value
        await self.client.publish(f"factory/{line}/{machine_id}/cmd", json.dumps(body), qos=1)
        await self.db.insert_command(machine_id, cmd, value)
        log.info("command %s -> %s (%s)", cmd, machine_id, source)

    async def watchdog(self) -> None:
        """ตรวจอุปกรณ์ที่ยัง 'online' แต่เงียบไป เช่น เฟิร์มแวร์ค้าง (Last Will จับกรณีนี้ไม่ได้ เพราะ TCP ยังต่ออยู่)"""
        while True:
            await asyncio.sleep(5)
            try:
                for m in await self.db.stale_machines(config.STALE_MIN_S):
                    mid = m["machine_id"]
                    await self.db.set_status(mid, "stale")
                    await self.hub.broadcast({"type": "status", "machine_id": mid, "status": "stale"})
                    await self._alert(mid, "warning", "offline", None, None, "อุปกรณ์ยังเชื่อมต่ออยู่แต่ไม่ส่งข้อมูล")
            except Exception:
                log.exception("watchdog error")
