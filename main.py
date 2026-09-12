"""
离线双人情感互动信物 - 完整版
硬件: ESP32-S3 + E22-400M22S (SX1268) + OLED + 按键 + 马达 + LED
功能: 时间显示、时间同步、情感互动、时间设定
"""

import _thread
import time
from machine import I2C, Pin
from ssd1306 import SSD1306_I2C
from sx1268 import SX1268
from _sx126x import ERR_NONE, ERROR

# ===== 配置 =====
PAIR_ID = "86291"
DEVICE_NAME = "A"

PIN_BUTTON = 4
PIN_MOTOR = 5
PIN_LED = 6

PIN_LORA_BUSY = 7
PIN_LORA_NRST = 8
PIN_LORA_NSS = 9
PIN_LORA_MISO = 10
PIN_LORA_MOSI = 11
PIN_LORA_SCK = 12
PIN_LORA_RXEN = 15
PIN_LORA_TXEN = 16
PIN_LORA_DIO1 = 17

PIN_OLED_SDA = 18
PIN_OLED_SCL = 19

# ===== 硬件初始化 =====
button = Pin(PIN_BUTTON, Pin.IN, Pin.PULL_UP)
motor = Pin(PIN_MOTOR, Pin.OUT, value=0)
led = Pin(PIN_LED, Pin.OUT, value=0)

i2c = I2C(0, sda=Pin(PIN_OLED_SDA), scl=Pin(PIN_OLED_SCL), freq=400000)
oled = SSD1306_I2C(128, 64, i2c)

txen = Pin(PIN_LORA_TXEN, Pin.OUT, value=0)
rxen = Pin(PIN_LORA_RXEN, Pin.OUT, value=1)

# ===== LoRa 初始化 =====
print("[LoRa] 初始化...")
lora = SX1268(
    spi_bus=1, clk=PIN_LORA_SCK, mosi=PIN_LORA_MOSI, miso=PIN_LORA_MISO,
    cs=PIN_LORA_NSS, irq=PIN_LORA_DIO1, rst=PIN_LORA_NRST, gpio=PIN_LORA_BUSY,
)
lora_state = lora.begin(
    freq=470.0, bw=125.0, sf=11, cr=5,
    syncWord=0x12, power=14, currentLimit=60.0,
    preambleLength=8, tcxoVoltage=1.6, blocking=True,
)
print("[LoRa] 状态:", ERROR.get(lora_state, lora_state))

# ===== 线程安全队列 =====
class Queue:
    def __init__(self):
        self.lock = _thread.allocate_lock()
        self.items = []
    def push(self, item):
        self.lock.acquire()
        self.items.append(item)
        self.lock.release()
    def pop(self):
        self.lock.acquire()
        item = self.items.pop(0) if self.items else None
        self.lock.release()
        return item

tx_queue = Queue()      # 主线程 → LoRa线程
rx_queue = Queue()      # LoRa线程 → 主线程

# ===== 软时钟 =====
class SoftClock:
    def __init__(self, h=12, m=0, s=0):
        self._base = ((h * 3600 + m * 60 + s) * 1000, time.ticks_ms())
        self.lock = _thread.allocate_lock()

    def _now_ms(self):
        self.lock.acquire()
        try:
            base_ms, base_ticks = self._base
        finally:
            self.lock.release()
        elapsed = time.ticks_diff(time.ticks_ms(), base_ticks)
        return (base_ms + elapsed) % 86400000

    def get_hms(self):
        total = self._now_ms() // 1000
        return total // 3600, (total % 3600) // 60, total % 60

    def get_ms(self):
        return self._now_ms()

    def set_ms(self, total_ms):
        self.lock.acquire()
        self._base = (int(total_ms) % 86400000, time.ticks_ms())
        self.lock.release()

    def add_field(self, field):
        h, m, s = self.get_hms()
        if field == 'hour': h = (h + 1) % 24
        elif field == 'minute': m = (m + 1) % 60
        elif field == 'second': s = (s + 1) % 60
        self.set_ms((h * 3600 + m * 60 + s) * 1000)

    def format(self):
        h, m, s = self.get_hms()
        return "{:02d}:{:02d}:{:02d}".format(h, m, s)

clock = SoftClock(12, 0, 0)

# ===== 多击检测器 =====
class MultiClickDetector:
    def __init__(self, pin, click_timeout=500, long_press_time=800, debounce_ms=20):
        self.pin = pin
        self.click_timeout = click_timeout
        self.long_press_time = long_press_time
        self.debounce_ms = debounce_ms
        self.last_state = pin.value()
        self.last_change_time = time.ticks_ms()
        self.last_release_time = 0
        self.click_count = 0
        self.press_start_time = 0
        self.is_pressed = False
        self.long_fired = False
        self.event_ready = False
        self.event_type = None
        self.single_click_mode = False

    def update(self):
        cur = self.pin.value()
        now = time.ticks_ms()

        if cur != self.last_state:
            if time.ticks_diff(now, self.last_change_time) >= self.debounce_ms:
                self.last_change_time = now
                if self.last_state == 1 and cur == 0:
                    self.press_start_time = now
                    self.is_pressed = True
                    self.long_fired = False
                    self.click_count = 1
                elif self.last_state == 0 and cur == 1:
                    self.is_pressed = False
                    self.last_release_time = now
                    if self.single_click_mode and not self.long_fired:
                        self.event_ready = True
                        self.event_type = 1
                        self.click_count = 0
                self.last_state = cur

        if self.is_pressed and not self.long_fired and not self.event_ready:
            if time.ticks_diff(now, self.press_start_time) >= self.long_press_time:
                self.event_ready = True
                self.event_type = 'long'
                self.long_fired = True
                self.click_count = 0

        if not self.single_click_mode and not self.is_pressed \
                and self.click_count > 0 and not self.long_fired:
            if time.ticks_diff(now, self.last_release_time) >= self.click_timeout:
                self.event_type = self.click_count
                self.event_ready = True
                self.click_count = 0

    def get_event(self):
        if self.event_ready:
            self.event_ready = False
            evt = self.event_type
            self.event_type = None
            return evt
        return None

click_detector = MultiClickDetector(button)

# ===== 状态 =====
STATE_NORMAL = 0
STATE_SET_HOUR = 1
STATE_SET_MIN = 2
STATE_SET_SEC = 3
STATE_SYNCING = 4
state = STATE_NORMAL

sync = {'active': False, 'phase': 0, 't0': 0, 'delay_ms': 0, 'start': 0, 'done_until': 0}
temp_until = 0
need_render = True

# ===== LoRa 收发 =====
def lora_send(packet):
    txen.value(1)
    rxen.value(0)
    time.sleep_ms(2)
    n, err = lora.send(packet.encode())
    txen.value(0)
    rxen.value(1)
    time.sleep_ms(2)
    if err != ERR_NONE:
        print("[TX ERR]", ERROR.get(err, err))

def lora_thread():
    print("[LoRa线程] 启动")
    while True:
        # 优先处理发送队列
        msg = tx_queue.pop()
        if msg is not None:
            lora_send(msg)
            continue

        # 接收
        txen.value(0)
        rxen.value(1)
        time.sleep_ms(2)
        data, err = lora.recv(timeout_en=True, timeout_ms=100)
        if err == ERR_NONE and data:
            try:
                text = data.decode('utf-8').strip()
            except:
                continue
            if ',' not in text:
                continue
            parts = text.split(',')
            if len(parts) < 2 or parts[0] != PAIR_ID:
                continue
            cmd = parts[1]
            payload = parts[2] if len(parts) > 2 else None
            print("[RX]", text)

            if cmd == "PING":
                lora_send("{},PONG".format(PAIR_ID))
            elif cmd == "PONG":
                rx_queue.push(('PONG', time.ticks_ms()))
            elif cmd == "TOUCH":
                lora_send("{},TOUCH_ACK".format(PAIR_ID))
                rx_queue.push(('TOUCH', None))
            elif cmd == "TOUCH_ACK":
                rx_queue.push(('TOUCH_ACK', None))
            elif cmd == "TIME_REQ":
                lora_send("{},TIME_RESP,{}".format(PAIR_ID, clock.get_ms()))
            elif cmd == "TIME_RESP":
                rx_queue.push(('TIME_RESP', payload))

# ===== 渲染 =====
def render():
    h, m, s = clock.get_hms()
    oled.fill(0)
    oled.text("LoRa Love Tag", 0, 0)
    oled.text("ID:{} M:{}".format(PAIR_ID, DEVICE_NAME), 0, 12)

    if sync['active']:
        oled.text("Time Sync", 0, 24)
        if sync['phase'] == 0:
            oled.text("Pinging...", 0, 36)
        elif sync['phase'] == 1:
            oled.text("Get time...", 0, 36)
        else:
            oled.text("Delay:{}ms".format(sync['delay_ms']), 0, 36)

    elif temp_until and time.ticks_diff(time.ticks_ms(), temp_until) < 0:
        oled.text("Received!", 0, 24)
        oled.text("From partner", 0, 36)

    elif state == STATE_NORMAL:
        oled.text("Ready", 0, 24)
        oled.text("Click:Send", 0, 36)

    elif state == STATE_SET_HOUR:
        oled.text("Set Hour:{:02d}".format(h), 0, 24)
        oled.text("Click+1 Long:OK", 0, 36)

    elif state == STATE_SET_MIN:
        oled.text("Set Minute:{:02d}".format(m), 0, 24)
        oled.text("Click+1 Long:OK", 0, 36)

    elif state == STATE_SET_SEC:
        oled.text("Set Second:{:02d}".format(s), 0, 24)
        oled.text("Click+1 Long:OK", 0, 36)

    oled.text(clock.format(), 32, 52)
    oled.show()

# ===== 输出辅助 =====
led_off_at = 0
motor_off_at = 0

def flash_led(ms=200):
    global led_off_at
    now = time.ticks_ms()
    led.value(1)
    if time.ticks_diff(now + ms, led_off_at) > 0:
        led_off_at = now + ms

def vibrate(ms=500):
    global motor_off_at, led_off_at
    now = time.ticks_ms()
    motor.value(1)
    led.value(1)
    motor_off_at = now + ms
    if time.ticks_diff(now + ms, led_off_at) > 0:
        led_off_at = now + ms

def update_outputs():
    global led_off_at, motor_off_at
    now = time.ticks_ms()
    if led_off_at and time.ticks_diff(now, led_off_at) >= 0:
        led.value(0)
        led_off_at = 0
    if motor_off_at and time.ticks_diff(now, motor_off_at) >= 0:
        motor.value(0)
        motor_off_at = 0

# ===== 同步流程 =====
def start_sync():
    global state, need_render
    state = STATE_SYNCING
    sync['active'] = True
    sync['phase'] = 0
    now = time.ticks_ms()
    sync['start'] = now
    sync['t0'] = now
    tx_queue.push("{},PING".format(PAIR_ID))
    need_render = True

def on_pong(t_recv):
    if not sync['active'] or sync['phase'] != 0:
        return
    rtt = time.ticks_diff(t_recv, sync['t0'])
    sync['delay_ms'] = rtt // 2
    sync['phase'] = 1
    tx_queue.push("{},TIME_REQ".format(PAIR_ID))
    need_render = True

def on_time_resp(payload):
    global state, need_render
    if not sync['active'] or sync['phase'] != 1:
        return
    try:
        remote_ms = int(payload)
        clock.set_ms(remote_ms + sync['delay_ms'])
        sync['phase'] = 2
        sync['done_until'] = time.ticks_ms() + 800
        print("[同步] 完成, 延时{}ms".format(sync['delay_ms']))
    except Exception as e:
        print("[同步] 解析失败:", e)
        sync['active'] = False
        state = STATE_NORMAL
    need_render = True

# ===== 事件处理 =====
def handle_button_event(evt):
    global state, need_render
    if evt is None:
        return

    # 长按
    if evt == 'long':
        if state == STATE_NORMAL:
            print("[长按] 进入设定")
            state = STATE_SET_HOUR
            click_detector.single_click_mode = True
        elif state == STATE_SET_HOUR:
            state = STATE_SET_MIN
        elif state == STATE_SET_MIN:
            state = STATE_SET_SEC
        elif state == STATE_SET_SEC:
            state = STATE_NORMAL
            click_detector.single_click_mode = False
        need_render = True
        return

    # 双击（仅常态）
    if evt == 2:
        if state == STATE_NORMAL:
            print("[双击] 发起时间同步")
            start_sync()
        return

    # 单击
    if evt == 1:
        if state == STATE_NORMAL:
            print("[单击] 发送情感信号")
            tx_queue.push("{},TOUCH".format(PAIR_ID))
            flash_led(200)
        elif state == STATE_SET_HOUR:
            clock.add_field('hour')
            need_render = True
        elif state == STATE_SET_MIN:
            clock.add_field('minute')
            need_render = True
        elif state == STATE_SET_SEC:
            clock.add_field('second')
            need_render = True

def handle_rx_event(evt):
    global need_render, temp_until, state
    typ, data = evt
    if typ == 'TOUCH':
        print("[事件] 收到情感信号")
        vibrate(500)
        if state == STATE_NORMAL:
            temp_until = time.ticks_ms() + 800
            need_render = True
    elif typ == 'TOUCH_ACK':
        flash_led(150)
    elif typ == 'PONG':
        on_pong(data)
    elif typ == 'TIME_RESP':
        on_time_resp(data)

# ===== 启动 LoRa 线程 =====
_thread.start_new_thread(lora_thread, ())

# ===== 主循环 =====
render()
print("[主线程] 启动, 时间:", clock.format())

last_sec = -1

while True:
    now_ms = time.ticks_ms()

    # 1. 按键扫描
    click_detector.update()
    evt = click_detector.get_event()
    if evt is not None:
        handle_button_event(evt)

    # 2. 消费接收事件
    while True:
        rx_evt = rx_queue.pop()
        if rx_evt is None:
            break
        handle_rx_event(rx_evt)

    # 3. 非阻塞输出更新
    update_outputs()

    # 4. 同步流程管理
    if sync['active']:
        if sync['phase'] == 2:
            if time.ticks_diff(now_ms, sync['done_until']) >= 0:
                sync['active'] = False
                state = STATE_NORMAL
                need_render = True
        elif time.ticks_diff(now_ms, sync['start']) > 5000:
            print("[同步] 超时")
            sync['active'] = False
            state = STATE_NORMAL
            need_render = True

    # 5. 秒变化触发重画
    sec = now_ms // 1000
    if sec != last_sec:
        last_sec = sec
        if state == STATE_NORMAL and not sync['active']:
            need_render = True

    # 6. 临时提示到期
    if temp_until and time.ticks_diff(now_ms, temp_until) >= 0:
        temp_until = 0
        need_render = True

    # 7. 统一渲染
    if need_render:
        need_render = False
        render()

    time.sleep_ms(20)