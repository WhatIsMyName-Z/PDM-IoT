# =====================================================================
# เฟิร์มแวร์ ESP32 (MicroPython) — "เซนเซอร์ติดเครื่องจักร" 1 ตัว
# รันบน Wokwi (https://wokwi.com) หรือบอร์ด ESP32 จริงได้เกือบไม่ต้องแก้
#
# ฮาร์ดแวร์ใน Wokwi:
#   DHT22          GPIO15  -> อุณหภูมิตัวเครื่อง
#   Potentiometer  GPIO34  -> "ระดับการสั่น" (หมุนเพื่อจำลองเครื่องเริ่มเสีย)
#   LED เขียว      GPIO4   -> เครื่องกำลังทำงาน
#   LED แดง        GPIO5   -> เครื่องหยุด / มีคำสั่งหยุดจากระบบ
#
# สิ่งที่โค้ดนี้แสดง (ใช้คุยตอนสัมภาษณ์ได้):
#   1. Edge processing : สุ่มตัวอย่างการสั่น 20 ครั้ง/วินาที แล้วคำนวณ RMS บนบอร์ด
#                        ส่งแค่ค่าเดียวต่อรอบ แทนการส่งข้อมูลดิบทั้งหมด
#   2. Last Will       : ถ้าบอร์ดดับ/เน็ตหลุด broker จะประกาศ "offline" แทนเรา
#   3. Reconnect + backoff : ต่อใหม่โดยรอนานขึ้นเรื่อยๆ ไม่ถล่ม broker
#   4. Store-and-forward   : เน็ตหลุดก็เก็บข้อมูลไว้ แล้วส่งย้อนหลัง
#   5. รับคำสั่งสองทาง     : stop / start / maintenance / set_interval
#   6. Watchdog            : ถ้าโปรแกรมค้าง บอร์ดจะรีเซ็ตตัวเอง
# =====================================================================
import json
import math
import random
import time

import dht
import network
from machine import ADC, Pin
from umqtt.simple import MQTTClient

# ------------------------- ตั้งค่า -------------------------
WIFI_SSID = "Wokwi-GUEST"          # WiFi จำลองของ Wokwi (ไม่มีรหัส)
WIFI_PASS = ""
BROKER = "broker.hivemq.com"       # public broker (Wokwi มองไม่เห็น localhost ของเรา)
TOPIC_PREFIX = "pdm-demo-CHANGE-ME/"   # ต้องตรงกับ remote prefix ใน mosquitto/conf.d/bridge.conf
LINE = "line2"
MACHINE_ID = "W01"                 # W = อุปกรณ์จาก Wokwi แยกจากตัวจำลอง M01..M08
OP_HOURS_PER_SECOND = 0.5          # เร่งเวลาให้เท่ากับตัวจำลอง (ใช้คำนวณ RUL)
BUFFER_MAX = 100                   # RAM ของ ESP32 มีจำกัด เก็บย้อนหลังได้ไม่เกินนี้
USE_WATCHDOG = True
# Wokwi: เซนเซอร์จำลองคืนค่าคงที่เป๊ะตามแถบเลื่อน ซึ่งของจริงไม่มีทางนิ่งขนาดนั้น
# ระบบฝั่ง backend จะเข้าใจว่า "เซนเซอร์ค้าง" จึงเติม noise เล็กน้อยเฉพาะตอนรันบน Wokwi
# บนบอร์ดจริงให้ตั้งเป็น False
WOKWI_MODE = True

BASE = TOPIC_PREFIX + "factory/" + LINE + "/" + MACHINE_ID
T_TELEMETRY = BASE + "/telemetry"
T_STATUS = BASE + "/status"
T_CMD = BASE + "/cmd"
T_ACK = BASE + "/ack"

# ------------------------- ฮาร์ดแวร์ -------------------------
sensor_temp = dht.DHT22(Pin(15))
knob = ADC(Pin(34))
knob.atten(ADC.ATTN_11DB)          # อ่านได้เต็มช่วง 0-3.3V
led_run = Pin(4, Pin.OUT)
led_stop = Pin(5, Pin.OUT)

# ------------------------- สถานะ -------------------------
state = "running"
interval_s = 2.0
seq = 0
op_hours = 0.0
buffer = []
client = None
last_temp = 45.0
wdt = None


def now_ms():
    """เวลาแบบ Unix epoch (ms) หรือ 0 ถ้ายังไม่มีเวลาที่ถูกต้อง (ให้ server ใส่เวลาแทน)"""
    t = time.time()
    if time.gmtime(0)[0] == 2000:  # MicroPython บางเวอร์ชันนับ epoch จากปี 2000
        t += 946684800
    return int(t * 1000) if t > 1700000000 else 0


def feed():
    if wdt:
        wdt.feed()


# ------------------------- เครือข่าย -------------------------
def wifi_connect():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        print("WiFi: connecting...")
        wlan.connect(WIFI_SSID, WIFI_PASS)
        for _ in range(100):
            if wlan.isconnected():
                break
            time.sleep(0.1)
    print("WiFi:", wlan.ifconfig() if wlan.isconnected() else "FAILED")
    try:
        import ntptime
        ntptime.settime()          # ตั้งนาฬิกาจากอินเทอร์เน็ต ข้อมูลย้อนหลังจะได้มีเวลาถูกต้อง
    except Exception as e:
        print("NTP failed:", e)


def on_message(topic, msg):
    """รับคำสั่งจากระบบ"""
    global state, interval_s
    try:
        data = json.loads(msg)
        cmd = data["cmd"]
    except Exception:
        return
    ok = True
    if cmd == "stop":
        state = "stopped"
    elif cmd == "start":
        state = "running"
    elif cmd == "maintenance":
        state = "running"
    elif cmd == "set_interval":
        interval_s = min(max(float(data.get("value", 2)), 0.5), 60)
    else:
        ok = False
    print("CMD:", cmd, "ok" if ok else "ignored")
    update_leds()
    client.publish(T_ACK, json.dumps({"cmd": cmd, "ok": ok, "ts": now_ms()}), qos=1)


def mqtt_connect():
    """ต่อ MQTT โดยรอนานขึ้นเรื่อยๆ ถ้าล้มเหลว: 1, 2, 4, 8 ... สูงสุด 30 วินาที"""
    global client
    delay = 1
    while True:
        feed()
        try:
            wifi_connect()
            c = MQTTClient("esp32-" + MACHINE_ID + "-" + str(random.getrandbits(16)), BROKER, keepalive=30)
            c.set_last_will(T_STATUS, b"offline", retain=True, qos=1)
            c.set_callback(on_message)
            c.connect()
            c.publish(T_STATUS, b"online", retain=True, qos=1)
            c.subscribe(T_CMD, qos=1)
            client = c
            print("MQTT: connected to", BROKER)
            flush_buffer()
            return
        except Exception as e:
            print("MQTT connect failed:", e, "- retry in", delay, "s")
            time.sleep(delay)
            delay = min(delay * 2, 30)


def flush_buffer():
    while buffer:
        client.publish(T_TELEMETRY, json.dumps(buffer[0]), qos=1)
        buffer.pop(0)
        feed()


# ------------------------- เซนเซอร์ -------------------------
def update_leds():
    led_run.value(1 if state == "running" else 0)
    led_stop.value(0 if state == "running" else 1)


def measure_vibration_rms(duration_s):
    """
    Edge processing: เก็บตัวอย่างการสั่นตลอดช่วง duration_s แล้วคำนวณ RMS บนบอร์ด

    ของจริงจะอ่านจาก accelerometer (เช่น ADXL345) Wokwi ไม่มีการสั่นจริง
    เราจึงสร้างสัญญาณสั่นที่ 25 Hz (= รอบหมุนมอเตอร์ 1500 RPM) โดยใช้ปุ่มหมุนกำหนดความแรง
    RMS ของคลื่น sine = แอมพลิจูด / √2
    """
    level = knob.read() / 4095                 # 0.0 - 1.0
    amplitude = (0.8 + level * 9.0) * math.sqrt(2)   # ปุ่มซ้ายสุด ≈ 0.8 mm/s, ขวาสุด ≈ 9.8 mm/s RMS
    n = max(int(duration_s * 20), 1)
    sum_sq = 0.0
    for i in range(n):
        t = time.ticks_ms() / 1000
        x = amplitude * math.sin(2 * math.pi * 25 * t) + random.uniform(-0.2, 0.2)
        sum_sq += x * x
        client.check_msg()                     # ระหว่างรอ ก็คอยเช็คคำสั่งที่เข้ามาด้วย
        feed()
        time.sleep(1 / 20)
    return math.sqrt(sum_sq / n), n


def read_temperature():
    global last_temp
    try:
        sensor_temp.measure()
        last_temp = sensor_temp.temperature()
        if WOKWI_MODE:
            last_temp += random.uniform(-0.3, 0.3)
    except OSError:
        pass                                   # DHT อ่านพลาดได้เป็นครั้งคราว ใช้ค่าล่าสุดแทน
    return last_temp


# ------------------------- ลูปหลัก -------------------------
def main():
    global seq, op_hours, wdt
    update_leds()
    mqtt_connect()
    if USE_WATCHDOG:
        try:
            from machine import WDT
            wdt = WDT(timeout=60000)           # ไม่ feed เกิน 60 วิ = บอร์ดรีเซ็ตตัวเอง
        except Exception as e:
            print("WDT unavailable:", e)

    while True:
        payload = None
        try:
            if state == "running":
                vib, samples = measure_vibration_rms(interval_s)
                op_hours += interval_s * OP_HOURS_PER_SECOND
                rpm = 1485 - vib * 2 + random.uniform(-2, 2)
                current = 11.5 + vib * 0.3 + random.uniform(-0.15, 0.15)
            else:
                for _ in range(int(interval_s * 10)):
                    client.check_msg()
                    feed()
                    time.sleep(0.1)
                vib, rpm, current, samples = 0.05, 0.0, 0.0, 0

            seq += 1
            payload = {
                "ts": now_ms(), "seq": seq, "vib_rms": round(vib, 3), "temp_c": round(read_temperature(), 2),
                "current_a": round(current, 2), "rpm": round(rpm, 1), "op_hours": round(op_hours, 3),
                "state": state, "interval_s": interval_s, "samples": samples,
            }
            client.publish(T_TELEMETRY, json.dumps(payload), qos=1)
            print("TX", seq, "vib=%.2f temp=%.1f" % (payload["vib_rms"], payload["temp_c"]))
        except OSError as e:
            # เน็ตหรือ broker หลุด: เก็บข้อมูลรอบนี้ไว้ก่อน แล้วต่อใหม่
            print("Connection lost:", e)
            if payload is not None:            # หลุดตอนกำลังส่ง: ข้อมูลรอบนี้ยังไม่ถึง เก็บไว้ส่งทีหลัง
                payload["buffered"] = True
                buffer.append(payload)
                if len(buffer) > BUFFER_MAX:
                    buffer.pop(0)
            mqtt_connect()


main()
