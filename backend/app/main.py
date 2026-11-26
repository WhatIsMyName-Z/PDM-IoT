"""
FastAPI:
- มีตัวรับ MQTT (ingest) และ watchdog
- เปิด REST API ให้ dashboard
- เปิด WebSocket /ws สำหรับข้อมูลแบบ real-time
- เสิร์ฟหน้า dashboard (ไฟล์ static)

เอกสาร API : http://localhost:8000/docs 
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config
from .db import Database
from .ingest import Ingestor
from .ws import Hub

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
STATIC = Path(__file__).parent / "static"

db = Database(config.DATABASE_URL)
hub = Hub()
ingestor = Ingestor(db, hub)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    tasks = [asyncio.create_task(ingestor.run()), asyncio.create_task(ingestor.watchdog())]
    yield
    for t in tasks:
        t.cancel()
    await db.close()


app = FastAPI(title="Predictive Maintenance IoT API", lifespan=lifespan)


class Command(BaseModel):
    cmd: str = Field(examples=["stop"])
    value: float | None = Field(default=None, description="ใช้กับ set_interval (วินาที)")


@app.get("/health")
async def health():
    return {"ok": True, "mqtt_connected": ingestor.connected}


@app.get("/api/machines")
async def list_machines():
    machines = await db.list_machines()
    # health/RUL คำนวณในหน่วยความจำ ไม่ได้เก็บใน DB จึงใส่เข้าไปตรงนี้
    for m in machines:
        a = ingestor.analyzers.get(m["machine_id"])
        m["health"] = a.last_health if a else None
        m["rul_h"] = a.last_rul_h if a else None
    return machines


@app.get("/api/machines/{machine_id}/telemetry")
async def machine_telemetry(machine_id: str, minutes: int = Query(10, ge=1, le=24 * 60)):
    return await db.telemetry(machine_id, minutes)


@app.post("/api/machines/{machine_id}/command", status_code=202)
async def machine_command(machine_id: str, body: Command):
    # 202 Accepted: เราส่งคำสั่งออกไปแล้ว แต่อุปกรณ์จะยืนยันทีหลังผ่าน topic ack
    if body.cmd not in config.ALLOWED_COMMANDS:
        raise HTTPException(400, f"unknown command; allowed: {sorted(config.ALLOWED_COMMANDS)}")
    if body.cmd == "set_interval" and not (body.value and 0.5 <= body.value <= 60):
        raise HTTPException(400, "set_interval needs value between 0.5 and 60 seconds")
    if machine_id not in ingestor.lines:
        raise HTTPException(404, "unknown machine")
    try:
        await ingestor.send_command(machine_id, body.cmd, body.value)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    return {"sent": True, "machine_id": machine_id, "cmd": body.cmd}


@app.get("/api/alerts")
async def list_alerts(limit: int = Query(50, ge=1, le=500), machine_id: str | None = None):
    return await db.alerts(limit, machine_id)


@app.post("/api/alerts/{alert_id}/ack")
async def ack_alert(alert_id: int):
    if not await db.ack_alert(alert_id):
        raise HTTPException(404, "alert not found")
    return {"acknowledged": True}


@app.get("/api/stats")
async def stats():
    return await db.stats()


@app.websocket("/ws")
async def websocket(ws: WebSocket):
    await hub.connect(ws)
    try:
        while True:
            await ws.receive_text()  
    except WebSocketDisconnect:
        hub.disconnect(ws)


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
