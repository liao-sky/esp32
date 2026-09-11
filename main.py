from machine import UART, Pin, I2C
from ssd1306 import SSD1306_I2C
import time

PAIR_ID = "86291"
DEVICE_NAME = "A"

LORA_TX_PIN = 8
LORA_RX_PIN = 9
LORA_AUX_PIN = 15
LORA_M0_PIN = 21
LORA_M1_PIN = 22


BUTTON_PIN = 4
MOTOR_PIN = 5
LED_PIN = 6

# OLED I2C
OLED_SDA = 18
OLED_SCL = 19


button = Pin(BUTTON_PIN, Pin.IN, Pin.PULL_UP)
motor = Pin(MOTOR_PIN, Pin.OUT)
led = Pin(LED_PIN, Pin.OUT)
motor.value(0)
led.value(0)

i2c = I2C(0, sda=Pin(OLED_SDA), scl=Pin(OLED_SCL), freq=400000)
oled = SSD1306_I2C(128, 64, i2c)

# E22模块 UART + 模式控制
uart = UART(1, baudrate=9600, tx=LORA_TX_PIN, rx=LORA_RX_PIN)
m0 = Pin(LORA_M0_PIN, Pin.OUT)
m1 = Pin(LORA_M1_PIN, Pin.OUT)
aux = Pin(LORA_AUX_PIN, Pin.IN, Pin.PULL_UP)

# 正常模式 (M0=0, M1=0)
m0.value(0)
m1.value(0)

def wait_aux_high():
    while aux.value() == 0:
        time.sleep_ms(10)

def update_display(status, extra=""):
    oled.fill(0)
    oled.text("LoRa Love Tag", 0, 0)
    oled.text("ID: {}".format(PAIR_ID), 0, 16)
    oled.text("Mode: {}".format(DEVICE_NAME), 0, 32)
    oled.text(status, 0, 48)
    if extra:
        oled.text(extra, 0, 56)
    oled.show()

update_display("Ready", "Press to send")

def send_lora_signal():
    packet = PAIR_ID + ",TOUCH"
    wait_aux_high()
    uart.write(packet)
    print("[发送] ", packet)
    led.value(1)
    time.sleep(0.2)
    led.value(0)
    update_display("Sending...", packet)

def receive_lora_signal():
    if uart.any():
        time.sleep_ms(20)
        raw = uart.read()
        if raw:
            try:
                msg = raw.decode('utf-8').strip()
                print("[接收] ", msg)
                if ',' in msg:
                    id_part, cmd = msg.split(',', 1)
                    if id_part == PAIR_ID and cmd == "TOUCH":
                        print("[触发] 配对ID匹配！")
                        motor.value(1)
                        led.value(1)
                        update_display("Received!", "From partner")
                        time.sleep(0.5)
                        motor.value(0)
                        led.value(0)
                        update_display("Ready", "Press to send")
                    else:
                        print("[忽略] 配对ID不匹配")
                        update_display("Ignored", "ID mismatch")
                        time.sleep(1)
                        update_display("Ready", "Press to send")
            except Exception as e:
                print("[错误] 解析失败:", e)

print("系统启动完成，配对ID:", PAIR_ID)

class MultiClickDetector:
    def __init__(self, pin, click_timeout=500, long_press_time=800):
        self.pin = pin
        self.click_timeout = click_timeout
        self.long_press_time = long_press_time
        self.last_state = pin.value()
        self.last_press_time = 0
        self.last_release_time = 0
        self.click_count = 0
        self.press_start_time = 0
        self.is_pressed = False
        self.event_ready = False
        self.event_type = None

    def update(self):
        current_state = self.pin.value()
        now = time.ticks_ms()

        if self.last_state == 1 and current_state == 0:
            self.press_start_time = now
            self.is_pressed = True
            if time.ticks_diff(now, self.last_release_time) < self.click_timeout:
                self.click_count += 1
            else:
                self.click_count = 1

        elif self.last_state == 0 and current_state == 1:
            self.is_pressed = False
            self.last_release_time = now
            press_duration = time.ticks_diff(now, self.press_start_time)
            if press_duration >= self.long_press_time:
                self.event_ready = True
                self.event_type = 'long'
                self.click_count = 0
            else:
                pass

        if not self.is_pressed and self.click_count > 0:
            if time.ticks_diff(now, self.last_release_time) >= self.click_timeout:
                if self.click_count == 1:
                    self.event_type = 'click'
                elif self.click_count == 2:
                    self.event_type = 'double'
                elif self.click_count >= 3:
                    self.event_type = 'triple'
                else:
                    self.event_type = None
                self.event_ready = True
                self.click_count = 0

        self.last_state = current_state

    def get_event(self):
        if self.event_ready:
            self.event_ready = False
            evt = self.event_type
            self.event_type = None
            return evt
        return None
