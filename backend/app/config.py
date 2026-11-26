import os

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://pdm:pdm@localhost:5432/pdm")

# ปิดลูปอัตโนมัติไว้เป็นค่าเริ่มต้น: ในโรงงานจริง การสั่งหยุดเครื่องเองโดยไม่มีคนยืนยันอาจกระทบการผลิตทั้งไลน์
# เปิดเป็น true เพื่อเดโม closed-loop control (ระบบสั่งหยุดเครื่องเองเมื่อความสั่นถึงระดับวิกฤต)
AUTO_STOP_ON_CRITICAL = os.getenv("AUTO_STOP_ON_CRITICAL", "false").lower() == "true"

# ถ้าไม่ได้ข้อมูลเกินกี่วินาที ขณะที่สถานะยัง online ให้ถือว่าอุปกรณ์เงียบ(เช่น เฟิร์มแวร์ค้าง)
STALE_MIN_S = 15

ALLOWED_COMMANDS = {"stop", "start", "maintenance", "set_interval"}
