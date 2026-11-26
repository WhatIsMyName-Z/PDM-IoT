from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path

import asyncpg

log = logging.getLogger("db")
SCHEMA = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")


class Database:
    def __init__(self, url: str):
        self.url = url
        self.pool: asyncpg.Pool | None = None

    async def connect(self, retries: int = 30) -> None:
        for attempt in range(retries):
            try:
                self.pool = await asyncpg.create_pool(self.url, min_size=1, max_size=5)
                break
            except (OSError, asyncpg.PostgresError) as e:
                log.warning("DB not ready (%s), retry %d", e, attempt + 1)
                await asyncio.sleep(2)
        else:
            raise RuntimeError("cannot connect to database")
        async with self.pool.acquire() as con:
            await con.execute(SCHEMA)

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()


    async def upsert_machine(self, machine_id: str, line: str) -> None:
        await self.pool.execute(
            "INSERT INTO machines (machine_id, line) VALUES ($1, $2) ON CONFLICT (machine_id) DO NOTHING",
            machine_id, line,
        )

    async def insert_telemetry(self, machine_id: str, ts: datetime, r: dict, missing: int) -> None:
        # ทำ 2 คำสั่งใน transaction เดียว: ถ้าอันใดพัง จะไม่มีข้อมูลครึ่งๆ กลางๆ ค้างใน DB
        async with self.pool.acquire() as con, con.transaction():
            await con.execute(
                """INSERT INTO telemetry (machine_id, ts, vib_rms, temp_c, current_a, rpm, op_hours, state, seq, buffered)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
                machine_id, ts, r["vib_rms"], r["temp_c"], r["current_a"], r["rpm"],
                r.get("op_hours"), r.get("state"), r.get("seq"), bool(r.get("buffered", False)),
            )
            await con.execute(
                """UPDATE machines SET last_seen = GREATEST(COALESCE(last_seen, $2), $2),
                          state = $3, interval_s = $4,
                          last_seq = GREATEST(COALESCE(last_seq, 0), COALESCE($5, 0)),
                          msgs_received = msgs_received + 1, msgs_missing = msgs_missing + $6,
                          status = CASE WHEN status IN ('stale', 'unknown') THEN 'online' ELSE status END
                   WHERE machine_id = $1""",
                machine_id, ts, r.get("state"), r.get("interval_s"), r.get("seq"), missing,
            )

    async def set_status(self, machine_id: str, status: str) -> None:
        await self.pool.execute("UPDATE machines SET status = $2 WHERE machine_id = $1", machine_id, status)

    async def insert_alert(self, machine_id: str, level: str, kind: str, metric, value, message: str) -> dict:
        row = await self.pool.fetchrow(
            """INSERT INTO alerts (machine_id, level, kind, metric, value, message)
               VALUES ($1,$2,$3,$4,$5,$6) RETURNING *""",
            machine_id, level, kind, metric, value, message,
        )
        return _row(row)

    async def insert_command(self, machine_id: str, cmd: str, value) -> None:
        await self.pool.execute("INSERT INTO commands (machine_id, cmd, value) VALUES ($1,$2,$3)", machine_id, cmd, value)

    async def ack_alert(self, alert_id: int) -> bool:
        res = await self.pool.execute("UPDATE alerts SET acknowledged = TRUE WHERE id = $1", alert_id)
        return res.endswith("1")

    # ---------------- อ่าน ----------------
    async def get_machine(self, machine_id: str) -> dict | None:
        row = await self.pool.fetchrow("SELECT * FROM machines WHERE machine_id = $1", machine_id)
        return _row(row) if row else None

    async def list_machines(self) -> list[dict]:
        rows = await self.pool.fetch(
            """SELECT m.*, t.vib_rms, t.temp_c, t.current_a, t.rpm, t.op_hours
               FROM machines m
               LEFT JOIN LATERAL (
                   SELECT * FROM telemetry WHERE machine_id = m.machine_id ORDER BY ts DESC LIMIT 1
               ) t ON TRUE
               ORDER BY m.machine_id"""
        )
        return [_row(r) for r in rows]

    async def stale_machines(self, min_s: float) -> list[dict]:
        rows = await self.pool.fetch(
            """SELECT machine_id FROM machines
               WHERE status = 'online' AND last_seen IS NOT NULL
                 AND last_seen < now() - make_interval(secs => GREATEST($1, 3 * COALESCE(interval_s, 2)))""",
            min_s,
        )
        return [_row(r) for r in rows]

    async def telemetry(self, machine_id: str, minutes: int) -> list[dict]:
        rows = await self.pool.fetch(
            """SELECT ts, vib_rms, temp_c, current_a, rpm, op_hours, state, buffered FROM telemetry
               WHERE machine_id = $1 AND ts > now() - make_interval(mins => $2)
               ORDER BY ts""",
            machine_id, minutes,
        )
        return [_row(r) for r in rows]

    async def alerts(self, limit: int, machine_id: str | None = None) -> list[dict]:
        if machine_id:
            rows = await self.pool.fetch(
                "SELECT * FROM alerts WHERE machine_id = $1 ORDER BY ts DESC LIMIT $2", machine_id, limit)
        else:
            rows = await self.pool.fetch("SELECT * FROM alerts ORDER BY ts DESC LIMIT $1", limit)
        return [_row(r) for r in rows]

    async def stats(self) -> dict:
        row = await self.pool.fetchrow(
            """SELECT (SELECT count(*) FROM telemetry) AS telemetry_rows,
                      (SELECT count(*) FROM alerts) AS alerts,
                      (SELECT COALESCE(sum(msgs_received),0) FROM machines) AS received,
                      (SELECT COALESCE(sum(msgs_missing),0) FROM machines) AS missing"""
        )
        return _row(row)


def _row(r) -> dict:
    """แปลงแถวจาก DB เป็น dict ที่ส่งเป็น JSON ได้ (datetime -> ISO string)"""
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in dict(r).items()}
