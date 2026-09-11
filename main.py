"""
离线双人情感互动信物 - 多线程完整版（局部刷新）
硬件: ESP32-S3 + E22-400M22S (LoRa) + OLED + 按键 + 马达 + LED
功能: 时间显示、长按设时、单击发送情感信号、双击时间同步
"""

import _thread
import time
from machine import UART, Pin, I2C
from ssd1306 import SSD1306_I2C

# ===== 配置 =====
PAIR_ID = "86291"
DEVICE_NAME = "A"

LORA_TX = 8
LORA_RX = 9
LORA_M0 = 21
LORA_M1 = 17
LORA_AUX = 15

BUTTON_PIN = 4
MOTOR_PIN = 5
LED_PIN = 7
OLED_SDA = 18
OLED_SCL = 20

# ===== 硬件初始化 =====
button = Pin(BUTTON_PIN, Pin.IN, Pin.PULL_UP)
motor = Pin(MOTOR_PIN, Pin.OUT)
led = Pin(LED_PIN, Pin.OUT)
motor.value(0)
led.value(0)

i2c = I2C(0, sda=Pin(OLED_SDA), scl=Pin(OLED_SCL), freq=400000)
oled = SSD1306_I2C(128, 64, i2c)

uart = UART(1, baudrate=9600, tx=Pin(LORA_TX), rx=Pin(LORA_RX))
m0 = Pin(LORA_M0, Pin.OUT)
m1 = Pin(LORA_M1, Pin.OUT)
aux = Pin(LORA_AUX, Pin.IN, Pin.PULL_UP)
m0.value(0)
m1.value(0)

uart_lock = _thread.allocate_lock()

def wait_aux_high():
    while aux.value() == 0:
        time.sleep_ms(5)

def lora_send(packet):
    uart_lock.acquire()
    try:
        wait_aux_high()
        uart.write((packet + "\n").encode('utf-8'))
    finally:
        uart_lock.release()
    print("[TX]", packet)

# ===== 事件队列 =====
class EventQueue:
    def __init__(self):
        self.lock = _thread.allocate_lock()
        self.queue = []
    def push(self, evt):
        self.lock.acquire()
        self.queue.append(evt)
        self.lock.release()
    def pop(self):
        self.lock.acquire()
        evt = self.queue.pop(0) if self.queue else None
        self.lock.release()
        return evt

event_queue = EventQueue()

# ===== 软时钟（毫秒精度 + 原子读写）=====
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

    def get_seconds(self):
        return self._now_ms() // 1000

    def get_ms(self):
        return self._now_ms()

    def set_ms(self, total_ms):
        self.lock.acquire()
        self._base = (int(total_ms) % 86400000, time.ticks_ms())
        self.lock.release()

    def set_seconds(self, total):
        self.set_ms(int(total) * 1000)

    def set_field(self, field, val):
        h, m, s = self.get_hms()
        if field == 'hour': h = val % 24
        elif field == 'minute': m = val % 60
        elif field == 'second': s = val % 60
        self.set_seconds(h * 3600 + m * 60 + s)

    def get_field(self, field):
        h, m, s = self.get_hms()
        return {'hour': h, 'minute': m, 'second': s}[field]

    def format(self):
        h, m, s = self.get_hms()
        return "{:02d}:{:02d}:{:02d}".format(h, m, s)

clock = SoftClock(12, 0, 0)

# ===== 多击检测器 =====
class MultiClickDetector:
    def __init__(self, pin, click_timeout=400, long_press_time=800, debounce_ms=20):
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
        self.long_triggered = False
        self.event_ready = False
        self.event_type = None

    def update(self):
        cur = self.pin.value()
        now = time.ticks_ms()

        if cur != self.last_state:
            if time.ticks_diff(now, self.last_change_time) < self.debounce_ms:
                return
            self.last_change_time = now

            if self.last_state == 1 and cur == 0:
                self.press_start_time = now
                self.is_pressed = True
                self.long_triggered = False
                if time.ticks_diff(now, self.last_release_time) < self.click_timeout:
                    self.click_count += 1
                else:
                    self.click_count = 1
            elif self.last_state == 0 and cur == 1:
                self.is_pressed = False
                self.last_release_time = now
            self.last_state = cur

        if self.is_pressed and not self.long_triggered and not self.event_ready:
            if time.ticks_diff(now, self.press_start_time) >= self.long_press_time:
                self.event_ready = True
                self.event_type = 'long'
                self.long_triggered = True
                self.click_count = 0

        if not self.is_pressed and self.click_count > 0 and not self.long_triggered:
            if time.ticks_diff(now, self.last_release_time) >= self.click_timeout:
                if self.click_count == 1:
                    self.event_type = 'click'
                elif self.click_count == 2:
                    self.event_type = 'double'
                elif self.click_count >= 3:
                    self.event_type = 'triple'
                self.event_ready = True
                self.click_count = 0

    def get_event(self):
        if self.event_ready:
            self.event_ready = False
            evt = self.event_type
            self.event_type = None
            return evt
        return None

click_detector = MultiClickDetector(button, click_timeout=400, long_press_time=800)

# ===== 状态定义 =====
STATE_NORMAL = 0
STATE_SET_HOUR = 1
STATE_SET_MIN = 2
STATE_SET_SEC = 3
STATE_SYNCING = 4
state = STATE_NORMAL

sync_state = {
    'active': False, 'phase': 0, 't0': 0,
    'delay_ms': 0, 'start': 0, 'done_until': 0,
}

# ===== 显示布局（分层刷新）=====
TITLE_Y = 0
ID_Y = 14
STATUS_Y1 = 28
STATUS_Y2 = 40
TIME_Y = 52
TIME_X = 32   # 8字符 * 8px = 64px，居中(128-64)/2=32

# 显示缓存：记录当前屏幕已显示的内容，避免重复刷新
_disp = {'time': '', 's1': '', 's2': ''}

def init_display():
    """初始化：一次性画好静态层 + 时间行"""
    oled.fill(0)
    oled.text("LoRa Love Tag", 0, TITLE_Y)
    oled.text("ID:{} M:{}".format(PAIR_ID, DEVICE_NAME), 0, ID_Y)
    t = clock.format()
    oled.text(t, TIME_X, TIME_Y)
    oled.show()
    _disp['time'] = t
    _disp['s1'] = ''
    _disp['s2'] = ''

def update_status(s1=None, s2=None):
    """只更新状态区两行，内容变化才刷新"""
    changed = False
    if s1 is not None and s1 != _disp['s1']:
        _disp['s1'] = s1
        changed = True
    if s2 is not None and s2 != _disp['s2']:
        _disp['s2'] = s2
        changed = True
    if not changed:
        return
    oled.fill_rect(0, STATUS_Y1, 128, 20, 0)
    if _disp['s1']:
        oled.text(_disp['s1'], 0, STATUS_Y1)
    if _disp['s2']:
        oled.text(_disp['s2'], 0, STATUS_Y2)
    oled.show()

def update_time():
    """只更新时间行：固定8字符直接覆盖，不清屏，无闪烁"""
    t = clock.format()
    if t == _disp['time']:
        return
    _disp['time'] = t
    oled.text(t, TIME_X, TIME_Y)
    oled.show()

# ===== 辅助 =====
def flash_led(ms=200):
    led.value(1)
    time.sleep_ms(ms)
    led.value(0)

def vibrate(ms=500):
    motor.value(1)
    led.value(1)
    time.sleep_ms(ms)
    motor.value(0)
    led.value(0)

# ===== LoRa 线程 =====
rx_buffer = b""

def process_message(msg):
    if ',' not in msg:
        return
    pair, rest = msg.split(',', 1)
    if pair != PAIR_ID:
        return
    parts = rest.split(',', 1)
    cmd = parts[0]
    data = parts[1] if len(parts) > 1 else None

    if cmd == "PING":
        lora_send("{},PONG".format(PAIR_ID))
    elif cmd == "TIME_REQ":
        lora_send("{},TIME_RESP,{}".format(PAIR_ID, clock.get_ms()))
    elif cmd == "TOUCH":
        lora_send("{},TOUCH_ACK".format(PAIR_ID))
        event_queue.push(('TOUCH', None))
    elif cmd == "TOUCH_ACK":
        event_queue.push(('TOUCH_ACK', None))
    elif cmd == "PONG":
        event_queue.push(('PONG', time.ticks_ms()))
    elif cmd == "TIME_RESP":
        event_queue.push(('TIME_RESP', data))

def lora_thread():
    global rx_buffer
    print("[LoRa线程] 启动")
    while True:
        if uart.any():
            uart_lock.acquire()
            try:
                chunk = uart.read()
            finally:
                uart_lock.release()
            if chunk:
                rx_buffer += chunk
                while b"\n" in rx_buffer:
                    line, rx_buffer = rx_buffer.split(b"\n", 1)
                    try:
                        msg = line.decode('utf-8').strip()
                        if msg:
                            print("[RX]", msg)
                            process_message(msg)
                    except Exception as e:
                        print("[RX错误]", e)
        time.sleep_ms(10)

# ===== 主线程事件处理 =====
temp_msg = {'s1': '', 's2': '', 'until': 0}

def show_temp(s1, s2, ms=500):
    """临时状态提示，超时后自动恢复常态"""
    update_status(s1, s2)
    temp_msg['s1'] = s1
    temp_msg['s2'] = s2
    temp_msg['until'] = time.ticks_ms() + ms

def start_sync():
    global state
    state = STATE_SYNCING
    sync_state['active'] = True
    sync_state['phase'] = 0
    now = time.ticks_ms()
    sync_state['start'] = now
    sync_state['t0'] = now
    lora_send("{},PING".format(PAIR_ID))
    update_status("Time Sync", "Pinging...")

def on_pong(t1_from_thread):
    if not sync_state['active'] or sync_state['phase'] != 0:
        return
    rtt = time.ticks_diff(t1_from_thread, sync_state['t0'])
    sync_state['delay_ms'] = rtt // 2
    print("[同步] RTT={}ms, 单程={}ms".format(rtt, sync_state['delay_ms']))
    sync_state['phase'] = 1
    lora_send("{},TIME_REQ".format(PAIR_ID))
    update_status("Time Sync", "Get time...")

def on_time_resp(data):
    if not sync_state['active'] or sync_state['phase'] != 1:
        return
    try:
        remote_ms = int(data)
        total_ms = remote_ms + sync_state['delay_ms']
        clock.set_ms(total_ms)
        print("[同步] 完成, 延时{}ms".format(sync_state['delay_ms']))
        update_status("Sync OK!", "Delay:{}ms".format(sync_state['delay_ms']))
        sync_state['phase'] = 2
        sync_state['done_until'] = time.ticks_ms() + 800
    except Exception as e:
        print("[同步] 解析失败:", e)
        sync_state['active'] = False
        state = STATE_NORMAL

def handle_lora_event(evt):
    typ, data = evt
    if typ == 'TOUCH':
        print("[事件] 收到情感信号")
        vibrate(500)
        # 临时提示（仅当不在同步/设定时）
        if state == STATE_NORMAL:
            show_temp("Received!", "From partner", 600)
    elif typ == 'TOUCH_ACK':
        print("[事件] 对方已确认")
        flash_led(150)
    elif typ == 'PONG':
        on_pong(data)
    elif typ == 'TIME_RESP':
        on_time_resp(data)

def handle_button_event(event):
    global state
    if event is None:
        return

    if state == STATE_NORMAL:
        if event == 'click':
            print("[按键] 单击发送")
            lora_send("{},TOUCH".format(PAIR_ID))
            flash_led(200)
            show_temp("Sent!", "TOUCH", 400)
        elif event == 'double':
            print("[按键] 双击同步时间")
            start_sync()
        elif event == 'long':
            print("[按键] 长按设时")
            state = STATE_SET_HOUR
            update_status("Set Hour:{:02d}".format(clock.get_field('hour')),
                          "Click+1 Dbl:OK")

    elif state in (STATE_SET_HOUR, STATE_SET_MIN, STATE_SET_SEC):
        field = {STATE_SET_HOUR: 'hour',
                 STATE_SET_MIN: 'minute',
                 STATE_SET_SEC: 'second'}[state]
        label = {STATE_SET_HOUR: 'Hour',
                 STATE_SET_MIN: 'Minute',
                 STATE_SET_SEC: 'Second'}[state]
        if event == 'click':
            clock.set_field(field, clock.get_field(field) + 1)
            update_status("Set {}:{:02d}".format(label, clock.get_field(field)),
                          "Click+1 Dbl:OK")
        elif event == 'double':
            if state == STATE_SET_HOUR:
                state = STATE_SET_MIN
                update_status("Set Minute:{:02d}".format(clock.get_field('minute')),
                              "Click+1 Dbl:OK")
            elif state == STATE_SET_MIN:
                state = STATE_SET_SEC
                update_status("Set Second:{:02d}".format(clock.get_field('second')),
                              "Click+1 Dbl:OK")
            else:
                state = STATE_NORMAL
        elif event == 'long':
            state = STATE_NORMAL

# ===== 启动 LoRa 线程 =====
_thread.start_new_thread(lora_thread, ())

# ===== 主循环 =====
init_display()
print("[主线程] 启动, 时间:", clock.format())

while True:
    # 1. 按键扫描
    click_detector.update()
    evt = click_detector.get_event()
    if evt:
        handle_button_event(evt)

    # 2. 消费 LoRa 事件
    while True:
        lora_evt = event_queue.pop()
        if lora_evt is None:
            break
        handle_lora_event(lora_evt)

    # 3. 同步流程管理
    if sync_state['active']:
        if sync_state['phase'] == 2:
            if time.ticks_diff(time.ticks_ms(), sync_state['done_until']) >= 0:
                sync_state['active'] = False
                state = STATE_NORMAL
        else:
            if time.ticks_diff(time.ticks_ms(), sync_state['start']) > 5000:
                print("[同步] 超时")
                sync_state['active'] = False
                state = STATE_NORMAL

    # 4. 根据状态刷新状态区（仅内容变化时触发 show）
    now = time.ticks_ms()
    if state == STATE_NORMAL:
        if time.ticks_diff(now, temp_msg['until']) >= 0:
            update_status("Ready", "1:Send 2:Sync L:Set")
    elif state == STATE_SYNCING:
        pass  # 同步过程由on_pong/on_time_resp控制显示
    elif state in (STATE_SET_HOUR, STATE_SET_MIN, STATE_SET_SEC):
        pass  # 设定模式由按键事件控制显示

    # 5. 时间行始终每秒刷新（固定宽度覆盖，无闪烁）
    update_time()

    time.sleep_ms(20)