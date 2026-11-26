"""WebSocket hub: ส่งข้อมูลใหม่ไปทุกหน้า dashboard ที่เปิดอยู่ทันที (push) แทนให้หน้าเว็บมาถามซ้ำๆ (polling)"""
from __future__ import annotations

import json

from fastapi import WebSocket


class Hub:
    def __init__(self):
        self.clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def broadcast(self, message: dict) -> None:
        data = json.dumps(message, ensure_ascii=False, default=str)
        dead = []
        for ws in self.clients:
            try:
                await ws.send_text(data)
            except Exception:   # แท็บที่ปิดไปแล้ว
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)
