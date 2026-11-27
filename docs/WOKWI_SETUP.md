# การเชื่อมต่อ ESP32 จำลองบน Wokwi เข้ากับระบบ

Wokwi ทำงานบนอินเทอร์เน็ตและไม่สามารถเข้าถึง `localhost` ของเครื่องผู้ใช้ได้ จึงต้องใช้ public broker เป็นตัวกลาง

```
ESP32 บน Wokwi ──> broker.hivemq.com <──bridge──> Mosquitto ในเครื่อง ──> backend
```

## ขั้นตอน

**1. กำหนด prefix เฉพาะ**
public broker เปิดให้ทุกคนใช้งาน จึงต้องกำหนดชื่อที่ไม่ซ้ำกับผู้อื่น เช่น `pdm-demo-7f3k/`

**2. เปิดใช้งาน bridge ในเครื่อง**
- คัดลอกไฟล์ `mosquitto/conf.d/bridge.conf.example` เป็น `mosquitto/conf.d/bridge.conf`
- แก้ไขบรรทัดสุดท้าย โดยเปลี่ยน `pdm-demo-CHANGE-ME/` เป็น prefix ที่กำหนดไว้ และต้องลงท้ายด้วย `/`
- รันคำสั่ง `docker compose restart mosquitto`
- ตรวจสอบด้วยคำสั่ง `docker compose logs mosquitto` ซึ่งควรแสดงข้อความว่าเชื่อมต่อกับ `broker.hivemq.com` สำเร็จ

**3. สร้างโปรเจกต์บน Wokwi**
- เข้าสู่ https://wokwi.com → New Project → เลือก **MicroPython on ESP32**
- การเริ่มต้นจาก template นี้ทำให้ได้ MicroPython เวอร์ชันที่ถูกต้อง

**4. วางโค้ด**
- ในแท็บ `main.py` ให้วางเนื้อหาจาก `firmware/wokwi/main.py`
- แก้ไข `TOPIC_PREFIX` ให้ตรงกับ prefix ในข้อ 1 ทุกตัวอักษร

**5. วางวงจร**
- ในแท็บ `diagram.json` ให้วางเนื้อหาจาก `firmware/wokwi/diagram.json`
- หากวงจรไม่แสดงผลหรือเกิดข้อผิดพลาด ให้คงบรรทัด `"env"` เดิมของ template ไว้ แล้วคัดลอกเฉพาะส่วน `parts` และ `connections`
- หากการต่อสายยังไม่ถูกต้อง ให้ต่อด้วยตนเองตามตารางต่อไปนี้

| อุปกรณ์ | ขา | ต่อกับ ESP32 |
|---|---|---|
| DHT22 | VCC / GND / SDA | 3V3 / GND / GPIO15 |
| Potentiometer | VCC / GND / SIG | 3V3 / GND / GPIO34 |
| LED สีเขียว ต่อผ่านตัวต้านทาน 220Ω | A / C | GPIO4 / GND |
| LED สีแดง ต่อผ่านตัวต้านทาน 220Ω | A / C | GPIO5 / GND |

**6. กด ▶ Run**
- Serial monitor ควรแสดง `WiFi: ...` → `MQTT: connected` → `TX 1 vib=...` ตามลำดับ
- dashboard จะแสดงการ์ดของเครื่อง **W01** เพิ่มขึ้น

## การทดลองใช้งาน

ขั้นตอนต่อไปนี้ใช้สาธิตการทำงานของระบบหรือบันทึกวิดีโอได้

1. **หมุน potentiometer ไปทางขวาอย่างช้าๆ** เพื่อเพิ่มค่าความสั่นสะเทือน ระบบจะแสดง warning ตามด้วย critical และค่า RUL จะลดลง
2. **คลิกที่เซนเซอร์ DHT22** แล้วปรับอุณหภูมิให้สูงขึ้น
3. **กด "หยุดเครื่อง" บน dashboard** LED สีเขียวจะดับ LED สีแดงจะติด และหน้าเว็บจะแสดงข้อความว่าอุปกรณ์ยืนยันคำสั่งแล้ว
4. **กด Stop บน Wokwi เพื่อหยุดการจำลอง** ภายในประมาณ 45 วินาที การ์ด W01 จะแสดงสถานะ offline สีแดงจาก Last Will

## การใช้งานกับบอร์ด ESP32 จริง

- เปลี่ยน `WIFI_SSID` และ `WIFI_PASS` เป็นข้อมูลของเครือข่าย WiFi ที่ใช้งาน
- เปลี่ยน `BROKER` เป็น IP ของเครื่องที่รัน Docker เช่น `192.168.1.50` และกำหนด `TOPIC_PREFIX = ""` เนื่องจากไม่ต้องใช้ bridge
- กำหนด `WOKWI_MODE = False`
- อัปโหลดโค้ดด้วย Thonny หรือคำสั่ง `mpremote cp main.py :main.py`
