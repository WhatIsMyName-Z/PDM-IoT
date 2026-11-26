-- โครงสร้างฐานข้อมูล: backend รันไฟล์นี้ทุกครั้งที่เริ่ม 

-- ทะเบียนเครื่องจักร: 1 แถวต่อ 1 เครื่อง เก็บสถานะล่าสุด
CREATE TABLE IF NOT EXISTS machines (
    machine_id   TEXT PRIMARY KEY,          -- เช่น M01
    line         TEXT NOT NULL,             -- สายการผลิต เช่น line1
    status       TEXT NOT NULL DEFAULT 'unknown',  -- online / offline 
    state        TEXT,                      -- running / stopped (สถานะการทำงานของเครื่อง)
    interval_s   REAL,                      -- รอบการส่งข้อมูลปัจจุบัน
    last_seen    TIMESTAMPTZ,
    last_seq     INTEGER,                   -- ใช้ตรวจว่ามีข้อความหายระหว่างทางหรือไม่
    msgs_received BIGINT NOT NULL DEFAULT 0,
    msgs_missing  BIGINT NOT NULL DEFAULT 0
);

-- ข้อมูลเซนเซอร์แบบอนุกรมเวลา
CREATE TABLE IF NOT EXISTS telemetry (
    id          BIGSERIAL PRIMARY KEY,
    machine_id  TEXT NOT NULL REFERENCES machines(machine_id),
    ts          TIMESTAMPTZ NOT NULL,       -- เวลาที่อุปกรณ์วัดค่า 
    vib_rms     REAL,                       -- ความสั่นสะเทือน (mm/s RMS)
    temp_c      REAL,                       -- อุณหภูมิตัวเครื่อง (°C)
    current_a   REAL,                       -- กระแสมอเตอร์ (A)
    rpm         REAL,                       -- รอบหมุน
    op_hours    REAL,                       -- ชั่วโมงการทำงานสะสมของเครื่อง (ใช้คำนวณ RUL)
    state       TEXT,
    seq         INTEGER,
    buffered    BOOLEAN NOT NULL DEFAULT FALSE  -- TRUE = ข้อมูลที่ส่งย้อนหลังหลังเน็ตกลับมา
);

CREATE INDEX IF NOT EXISTS idx_telemetry_machine_ts ON telemetry (machine_id, ts DESC);

-- การแจ้งเตือน
CREATE TABLE IF NOT EXISTS alerts (
    id           BIGSERIAL PRIMARY KEY,
    machine_id   TEXT NOT NULL REFERENCES machines(machine_id),
    ts           TIMESTAMPTZ NOT NULL DEFAULT now(),
    level        TEXT NOT NULL,             -- warning / critical
    kind         TEXT NOT NULL,             -- threshold / zscore / shift / rul / offline / sensor_fault / data_gap / fault
    metric       TEXT,
    value        REAL,
    message      TEXT NOT NULL,
    acknowledged BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts (ts DESC);

-- คำสั่งที่ส่งลงไปยังอุปกรณ์ 
CREATE TABLE IF NOT EXISTS commands (
    id          BIGSERIAL PRIMARY KEY,
    machine_id  TEXT NOT NULL,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    cmd         TEXT NOT NULL,
    value       REAL
);
