# วิธีต่อ ESP32 จำลองบน Wokwi เข้ากับระบบ

Wokwi รันอยู่บนอินเทอร์เน็ต มองไม่เห็น `localhost` ของเครื่องเรา จึงต้องใช้ public broker เป็นตัวกลาง:

```
ESP32 (Wokwi) ──> broker.hivemq.com <──bridge──> Mosquitto (เครื่องเรา) ──> backend
```

## ขั้นตอน

**1. ตั้งชื่อ prefix เฉพาะของเรา**
public broker ใครก็ใช้ได้ ต้องมีชื่อที่ไม่ชนกับคนอื่น เช่น `pdm-jack-7f3k/`

**2. เปิด bridge ในเครื่องเรา**
- คัดลอกไฟล์ `mosquitto/conf.d/bridge.conf.example` เป็น `mosquitto/conf.d/bridge.conf`
- แก้บรรทัดสุดท้าย เปลี่ยน `pdm-demo-CHANGE-ME/` เป็น prefix ของเรา (ต้องมี `/` ปิดท้าย)
- รัน `docker compose restart mosquitto`
- ตรวจด้วย `docker compose logs mosquitto` ควรเห็นข้อความว่าเชื่อมต่อ `broker.hivemq.com` สำเร็จ

**3. สร้างโปรเจกต์บน Wokwi**
- เข้า https://wokwi.com → New Project → เลือก **MicroPython on ESP32**
- การเริ่มจาก template แบบนี้ทำให้ได้เวอร์ชัน MicroPython ที่ถูกต้องมาด้วย

**4. วางโค้ด**
- แท็บ `main.py`: วางเนื้อหาจาก `firmware/wokwi/main.py`
- แก้ `TOPIC_PREFIX` ให้ตรงกับข้อ 1 (ต้องเหมือนกันเป๊ะ)

**5. วางวงจร**
- แท็บ `diagram.json`: วางเนื้อหาจาก `firmware/wokwi/diagram.json`
- ถ้าวงจรไม่ขึ้นหรือ error ให้คงบรรทัด `"env"` เดิมของ template ไว้ แล้วคัดลอกมาแค่ส่วน `parts` กับ `connections`
- ถ้าสายไฟยังผิดอยู่ ต่อเองตามตารางนี้:

| อุปกรณ์ | ขา | ต่อกับ ESP32 |
|---|---|---|
| DHT22 | VCC / GND / SDA | 3V3 / GND / GPIO15 |
| Potentiometer | VCC / GND / SIG | 3V3 / GND / GPIO34 |
| LED เขียว (ผ่าน R 220Ω) | A / C | GPIO4 / GND |
| LED แดง (ผ่าน R 220Ω) | A / C | GPIO5 / GND |

**6. กด ▶ Run**
- Serial monitor ควรขึ้น `WiFi: ...` → `MQTT: connected` → `TX 1 vib=...`
- ใน dashboard จะมีการ์ด **W01** เพิ่มขึ้นมา

## ลองเล่น (ใช้ถ่ายวิดีโอเดโมได้)

1. **หมุนปุ่ม potentiometer ไปทางขวาช้าๆ** เพื่อให้ความสั่นเพิ่มขึ้น จะเห็น warning แล้วตามด้วย critical และ RUL ลดลง
2. **คลิกที่เซนเซอร์ DHT22** แล้วเลื่อนอุณหภูมิขึ้น
3. **กด "หยุดเครื่อง"** บน dashboard: LED เขียวดับ LED แดงติด และหน้าเว็บขึ้นข้อความว่าอุปกรณ์ยืนยันคำสั่งแล้ว
4. **กด Stop ของ Wokwi** (หยุดจำลอง): ภายในประมาณ 45 วินาที การ์ด W01 จะเป็นจุดแดง offline จาก Last Will

## ถ้าใช้บอร์ด ESP32 จริง

- เปลี่ยน `WIFI_SSID` / `WIFI_PASS` เป็น WiFi บ้าน
- เปลี่ยน `BROKER` เป็น IP เครื่องคอมที่รัน docker (เช่น `192.168.1.50`) และตั้ง `TOPIC_PREFIX = ""` (ไม่ต้องใช้ bridge)
- ตั้ง `WOKWI_MODE = False`
- อัปโหลดด้วย Thonny หรือ `mpremote cp main.py :main.py`
