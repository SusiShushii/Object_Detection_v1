"""
click.py - คลิกเมาส์ตามตำแหน่งที่ detect เจอ (Windows API ผ่าน ctypes)

ระหว่างทำงาน กด F12 ค้างไว้เพื่อหยุดได้ทันที (STOP_KEY)
"""

import ctypes
import math
import os
import random
import threading
import time
from ctypes import wintypes

import cv2
import numpy as np

from object_detection import (BASE_DIR, READY_SATURATION, detect, get_scale, item_name,
                              list_templates, load_template, log, saturation, to_gray, best_score)

# เจอไอเท็ม -> ขยับเมาส์ไป hover บนไอเท็มรอไว้ พอพร้อมกดได้ทันที
# จุดวางเมาส์สุ่มใหม่ทุกช่อง ภายในโซนนี้ (สัดส่วนของกรอบไอเท็ม: (x ต่ำสุด, x สูงสุด), (y ต่ำสุด, y สูงสุด))
# ครึ่งล่างของช่องเท่านั้น: วัดจริง ลูกศรเมาส์อยู่ครึ่งบน (y <= 50%) บังไอคอน -> detect เพี้ยน
#                         ครึ่งล่าง (y >= 60%) ถูกต้องทุกสถานะ (ทึบ / ปกติ / GET!)
HOVER_ZONE = ((0.10, 0.85), (0.60, 0.85))

# เช็กว่าหน้า Mission Result ยังเปิดอยู่ จากขอบกรอบบน/ล่าง (คะแนน 0-1)
# วัดจริง: หน้าจอเปิด >= 0.69 (เมาส์/tooltip/เปลี่ยนสี = 1.0)  หน้าจอปิด <= 0.05
SCREEN_MATCH = 0.5

# เช็กว่าไอเท็มยังอยู่ในช่องไหม ก่อนกด (คะแนน template 0-1)
# วัดจริง: ไอเท็มอยู่ >= 0.85 (มีเมาส์ hover + tooltip ก็เท่าเดิม)
#          ช่องว่าง <= 0.63 (carrot.png มีขอบช่องติดมา จึงสูงกว่าตัวอื่น)  ไอเท็มอื่น <= 0.52
# กันกรณีไอเท็มหายไป -> ช่องว่างมี saturation ~130 เกินเกณฑ์ -> เข้าใจผิดว่าขึ้นสี
ITEM_MATCH = 0.75

# โหมด random loot: ช่องเป็นกล่อง "?" ไอเท็มที่เปิดออกมาอาจไม่ขึ้นสีเลย -> กดทันทีที่เจอ
# ภาพกล่อง "?" อยู่ใน markers/ (ไม่ใช่ templates/ จะได้ไม่ถูกนับเป็นไอเท็มที่ต้องคลิก)
# วัดจริง: random loot = 1.000  โหมดปกติ <= 0.39
LOOT_BOX = os.path.join(BASE_DIR, "markers", "loot_box.png")
LOOT_MATCH = 0.75

# True  = กดช่องละครั้งเดียว แล้วถือว่าเก็บแล้ว ไม่ย้อนกลับมากดซ้ำ
# False = กดแล้วไปช่องถัดไปทันที แต่เช็กเบื้องหลัง: ไม่เห็นทึบแว้บ = อาจคลิกไม่เข้า
#         -> กลับมากดซ้ำ (สูงสุด MAX_CLICKS_PER_ITEM ครั้งต่อช่อง เว้นจังหวะสุ่มแบบคน ไม่รัว)
SINGLE_CLICK = False

# True  = กดทันทีที่เจอแม้ยังทึบ (ด่านที่กดตอนทึบได้ เช่น log 18:49)
#         ถ้า SINGLE_CLICK ด้วย = กดตอนทึบครั้งเดียวแล้วถือว่าเก็บ (ไม่กดซ้ำตอนพร้อม)
# False = ช่องทึบ รอจนพร้อมแล้วค่อยกด
CLICK_ON_FOUND = False

# เก็บภาพ loot ทุกด่าน (ไม่ว่าจะสั่งเก็บไอเท็มอะไร หรือไม่เจอไอเท็มเป้าหมายก็ตาม) ไว้ที่ loot/
# เห็นหัวข้อ "Mission Reward" -> บันทึก 2 ภาพต่อด่าน:
#   ..._1_dim.png    : ตอนไอเท็มยังทึบ (มีช่องที่พื้นหลังทึบ)
#   ..._2_normal.png : ตอนไอเท็มเป็นสีปกติหมดแล้ว (ไม่มีช่องทึบเหลือ)
#   ด่านที่เป็นสีปกติตั้งแต่แรก -> ได้แค่ภาพ normal
#   บันทึกเฉพาะตอนมีไอเท็มในกรอบ: กรอบว่าง (ของยังไม่ขึ้น / เก็บหมดแล้ว) ไม่บันทึก
# วัดจริง: หน้า Mission Result >= 0.90  ฉากเกม <= 0.38
HEADER_MARKER = os.path.join(BASE_DIR, "markers", "mission_reward.png")
HEADER_MATCH = 0.65
# หัวข้อ "Mission Reward" ต้องเห็นอยู่เสมอ ถึงจะหาไอเท็ม / ขยับเมาส์ / คลิก
# (กันคลิกมั่วเมื่อไม่ได้อยู่หน้า Mission Result เช่น ฉากเกมที่มีแครอทบนพื้นจับผิด หรือหน้าจอปิดไปแล้ว)
# หายติดกันกี่เฟรม = ถือว่าออกจากหน้านั้นแล้ว -> เลิกรอ ไม่คลิกต่อ
HEADER_LOST_FRAMES = 3
SAVE_LOOT = True
LOOT_DIR = os.path.join(BASE_DIR, "loot")
LOOT_SETTLE = 0.3          # เห็นหัวข้อแล้วรออย่างน้อยเท่านี้ก่อนบันทึก (วินาที)
LOOT_STABLE = 0.3          # จำนวนไอเท็มในกรอบต้องไม่เปลี่ยนนานเท่านี้ (= ขึ้นครบแล้ว) ก่อนบันทึกภาพแรก
# ช่องว่าง: เทียบกับภาพช่องว่าง วัดจริง ช่องว่าง >= 0.94  ช่องที่มีไอเท็ม (ทึบ/ปกติ/GET!/กล่อง ?) <= 0.65
EMPTY_SLOT = os.path.join(BASE_DIR, "markers", "empty_slot.png")
EMPTY_MATCH = 0.85
LOOT_CHECK_EVERY = 0.1     # เช็กพื้นหลังทั้งกรอบทุกกี่วินาที

# ตำแหน่งช่องในกรอบ mission_reward ที่จอ 2K (ก่อนคูณ scale): ช่องแรก + ระยะห่าง, 7 x 3 ช่อง
GRID_ORIGIN = (13, 35)
GRID_PITCH = (73, 72)
GRID_SIZE = (7, 3)
SLOT_WH = (62, 65)

# รอหลังเลื่อนเมาส์ไปที่ไอเท็ม ก่อนกด (วินาที) ให้เกมรับรู้ตำแหน่งเมาส์ก่อน (~1 เฟรมที่ 60fps)
# ใช้เฉพาะตอนต้องขยับไปกดตัวที่ไม่ได้ hover รอไว้
MOVE_DELAY = 0.016

# ขยับเมาส์แบบคน (ไม่วาป): เส้นโค้งนิดๆ เร็วตอนต้น ช้าลงตอนใกล้ถึง
# False = วาปไปทันที
SMOOTH_MOVE = True
MOVE_TIME = (0.04, 0.12)       # เวลาที่ใช้ขยับ (วินาที): (ระยะใกล้, ระยะไกล)
MOVE_CURVE = 0.18              # เส้นทางโค้งออกจากเส้นตรงได้สูงสุดกี่ % ของระยะ (สุ่มทุกครั้ง)
MOVE_OVERSHOOT = 0.3           # โอกาสเลยเป้าแล้วแก้กลับ (เฉพาะระยะ > 250px)

# ขยับไป hover ตอนช่องยังทึบ (ไม่รีบ) -> ความเร็วแบบคนจริงตามสถิติ (Fitts' law)
#   เวลาขยับ = a + b * log2(ระยะทาง / ขนาดช่อง + 1)   ค่า a, b สุ่มในช่วงที่พบในงานวิจัยการใช้เมาส์
#   ก่อนขยับ รอ "เวลาตอบสนอง" (เห็นแล้วเริ่มขยับ) แบบคน
#   ขยับ 2 จังหวะ: สะบัดไปเกือบถึง (90-97%) -> หยุดนิดนึง -> ขยับแก้เข้าเป้า
#   ถ้าไอเท็มพร้อมกดระหว่างทาง -> เร่งไปให้ถึงทันที (ไม่พลาดจังหวะ)
# ช่องที่พร้อมกดแล้ว (รีบ) ยังใช้ MOVE_TIME แบบเร็วเหมือนเดิม
HUMAN_HOVER = True
FITTS_A = (0.03, 0.07)         # วินาที
FITTS_B = (0.05, 0.08)         # วินาทีต่อ bit
REACTION_TIME = (0.08, 0.16)   # วินาที

# ระหว่างเลื่อนเมาส์ จับภาพเช็กสถานะทุกกี่ก้าว (จับทุกก้าวบน RDP ทำให้เมาส์ช้า/กระตุก)
STEP_CHECK_EVERY = 3
# เวลากดค้างตอนคลิก (วินาที) สุ่ม
CLICK_HOLD = (0.03, 0.06)
MOVE_TIME_FULL_DIST = 1500     # ระยะ (px) ที่ใช้เวลาเต็ม MOVE_TIME[1]

# บันทึกภาพ debug (ตอนเจอ / หาไม่เจอ / คลิก) ไว้ที่โฟลเดอร์ debug/
SAVE_DEBUG = True
DEBUG_DIR = os.path.join(BASE_DIR, "debug")

# ลำดับการเก็บ:
#   1) ตามลำดับความสำคัญ = ลำดับชื่อที่พิมพ์ตอนรัน เช่น  python main.py skybug_spike skybug_tail
#      -> เก็บ skybug_spike ให้หมดก่อน แล้วค่อยไป skybug_tail (ทุกครั้งที่เก็บเสร็จ 1 ช่อง จะเช็กจากอันดับ 1 ใหม่)
#   2) ไอเท็มชนิดเดียวกันหลายช่อง: เก็บช่องที่อยู่กลางกรอบก่อน รองลงมาฝั่งขวา
#      (ช่องที่อยู่ฝั่งซ้ายนับระยะเพิ่ม LEFT_PENALTY เท่า)
LEFT_PENALTY = 2.5

# เก็บทีละช่อง: กดแล้วกลับมาทึบแว้บนึง (หรือช่องว่าง) = เก็บได้แล้ว
# -> ไปหาไอเท็มชนิดเดียวกันช่องอื่น (ไม่กลับไปกดช่องเดิม) -> รอพร้อมแล้วกด -> วนจนหมด / หน้าจอปิด
MAX_CLICKS_PER_ITEM = 2        # กดช่องเดียวได้สูงสุดกี่ครั้ง (ครั้งที่ 2 = เผื่อครั้งแรกไม่เข้า) ครบแล้วถือว่าเก็บแล้ว
RETRY_AFTER = 0.6              # กดแล้วไม่ทึบแว้บนานเท่านี้ (วินาที) = คลิกไม่เข้า -> กดซ้ำ
RETRY_JITTER = (0.8, 1.4)      # สุ่มคูณ RETRY_AFTER ทุกครั้ง (กดซ้ำห่าง ~0.5-0.85 วิ ไม่เป็นจังหวะเป๊ะแบบมาโคร)
CLICK_GAP = 0.12               # เว้นระยะขั้นต่ำระหว่างคลิก (วินาที) ให้เห็นทึบแว้บของคลิกก่อน (จริง ~65ms)
REPICK_AFTER = 0.3             # ช่องที่รอเก็บไม่เห็นไอเท็มนานเกินนี้ (วินาที) -> ไปหาช่องอื่น
RESCAN_EVERY = 0.2             # ยังไม่เจอช่องถัดไป -> สแกนหาทุกกี่วินาที (จนหน้าจอปิด)
GONE_AFTER = None              # ไม่เห็นไอเท็มนานเกินนี้ (วินาที) -> เลิกรอ  None = ไม่ใช้

# เมาส์ hover บนไอเท็มที่พร้อมเก็บ -> เกมเปลี่ยนไอคอนเป็นป้าย "GET!" สีเขียว = สัญญาณกดได้
# วัดจริง: ป้าย GET! = 1.000  ไอเท็มปกติ/เทา/ช่องว่าง/กล่อง ? <= 0.54
GET_MARKER = os.path.join(BASE_DIR, "markers", "get.png")
GET_MATCH = 0.75

# พื้นหลังช่องของไอเท็ม: ทึบ = ยังเก็บไม่ได้, สว่างปกติ = พร้อมเก็บ (ใช้ร่วมกับป้าย GET! แบบ OR)
# วัดจริง (V ของ HSV): ทึบ ~64  ปกติ 92-99  ป้าย GET! ~128
BG_READY_V = 80

user32 = ctypes.windll.user32

# ให้พิกัดเมาส์ตรงกับพิกเซลจริง (เหมือนใน screen.py)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except (AttributeError, OSError):
    user32.SetProcessDPIAware()

# ---------------------------------------------------------------------------
# Windows API: SendInput
# ---------------------------------------------------------------------------

INPUT_MOUSE = 0
MOUSE_FLAGS = {
    "left": (0x0002, 0x0004),    # LEFTDOWN, LEFTUP
    "right": (0x0008, 0x0010),   # RIGHTDOWN, RIGHTUP
}
# ปุ่มหยุดโปรแกรม (กดค้าง): F12  (ไม่ใช้ ESC เพราะในเกมกด ESC บ่อย -> โปรแกรมหยุดเอง)
# เปลี่ยนได้ เช่น 0x23 = End, 0x13 = Pause  (ดูรหัสปุ่มได้จาก Virtual-Key Codes ของ Windows)
STOP_KEY = 0x7B
STOP_KEY_NAME = "F12"


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("_pad", ctypes.c_byte * 32)]

    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def _send_mouse(flag):
    inp = INPUT(type=INPUT_MOUSE)
    inp.mi.dwFlags = flag
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


# ---------------------------------------------------------------------------
# คลิก
# ---------------------------------------------------------------------------

class ClickAborted(Exception):
    """ผู้ใช้กดปุ่มหยุด (STOP_KEY) ค้าง"""


def esc_pressed():
    return bool(user32.GetAsyncKeyState(STOP_KEY) & 0x8000)


def click(x, y, button="left", hold=0.05, move_delay=0.05):
    """เลื่อนเมาส์ไปที่ (x, y) บนจอ แล้วคลิก

    hold       : เวลากดค้าง (วินาที) เกมบางเกมไม่รับคลิกที่เร็วเกินไป
    move_delay : รอหลังเลื่อนเมาส์ ให้เกมรับรู้ว่าเมาส์มาอยู่ตรงนี้แล้ว
    """
    if esc_pressed():
        raise ClickAborted(f"กด {STOP_KEY_NAME} -> หยุดคลิก")

    down, up = MOUSE_FLAGS[button]
    move_to(x, y)
    time.sleep(move_delay)
    _send_mouse(down)
    time.sleep(hold)
    _send_mouse(up)


def to_screen(item, region_box):
    """แปลงผล detect (พิกัดในภาพ region) เป็นจุดกึ่งกลางบนจอจริง

    item       : (x, y, w, h, score) จาก detect()
    region_box : (x1, y1, x2, y2) จาก screen.get_region() ตัวเดียวกับที่ใช้จับภาพ
    """
    x, y, w, h, _ = item
    rx, ry = region_box[:2]
    return rx + x + w // 2, ry + y + h // 2


def click_found(found, region_box, delay=0.3, dry_run=False, **click_kwargs):
    """คลิกทุกตำแหน่งที่ detect เจอ ทีละตัว

    delay   : เวลารอระหว่างแต่ละคลิก (วินาที)
    dry_run : True = แค่ log พิกัด ไม่คลิกจริง
    คืนจำนวนที่คลิกไปแล้ว
    """
    log.info(f"[click] {len(found)} ตำแหน่ง  region={region_box}  dry_run={dry_run}")

    clicked = 0
    for i, item in enumerate(found, 1):
        sx, sy = to_screen(item, region_box)
        log.info(f"    #{i}  screen=({sx}, {sy})  score={item[4]:.3f}")
        if not dry_run:
            try:
                click(sx, sy, **click_kwargs)
            except ClickAborted as e:
                log.info(f"  {e}  (คลิกไปแล้ว {clicked})")
                break
            time.sleep(delay)
        clicked += 1
    return clicked


def hover(x, y):
    """เลื่อนเมาส์ไปที่ (x, y) โดยไม่คลิก"""
    user32.SetCursorPos(int(x), int(y))


def get_cursor():
    """ตำแหน่งเมาส์ปัจจุบัน (x, y)"""
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def _spin(seconds):
    """รอแบบแม่นยำระดับ ms (time.sleep บน Windows ละเอียดแค่ ~15ms)"""
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        time.sleep(0)


def _path_point(p0, c1, c2, p1, e):
    """จุดบนเส้นโค้ง cubic bezier ที่สัดส่วน e (0-1)"""
    u = 1 - e
    return (u ** 3 * p0[0] + 3 * u * u * e * c1[0] + 3 * u * e * e * c2[0] + e ** 3 * p1[0],
            u ** 3 * p0[1] + 3 * u * u * e * c1[1] + 3 * u * e * e * c2[1] + e ** 3 * p1[1])


def _glide(x0, y0, x, y, duration, on_step=None, jitter=True):
    """ขยับเมาส์ตามเส้นโค้งสุ่ม 1 ช่วง (ไม่วาป)  คืน True ถ้า on_step สั่งหยุดกลางทาง"""
    dx, dy = x - x0, y - y0
    dist = math.hypot(dx, dy)
    nx, ny = -dy / dist, dx / dist                      # ทิศตั้งฉากกับเส้นตรง

    # จุดควบคุม 2 จุด: ตำแหน่งตามแนวทางสุ่ม + เบี่ยงออกด้านข้างสุ่ม
    # 35% ของครั้ง เบี่ยงคนละด้าน = เส้นทางรูปตัว S, ที่เหลือโค้งด้านเดียว
    side = random.choice((-1, 1))
    b1 = side * random.uniform(0.04, MOVE_CURVE) * dist
    b2 = (-b1 if random.random() < 0.35 else side * random.uniform(0.0, MOVE_CURVE) * dist)
    a1, a2 = random.uniform(0.15, 0.45), random.uniform(0.55, 0.9)
    c1 = (x0 + dx * a1 + nx * b1, y0 + dy * a1 + ny * b1)
    c2 = (x0 + dx * a2 + nx * b2, y0 + dy * a2 + ny * b2)

    # ความเร็วแบบมือคน: เร่งขึ้น -> พีค -> เบรก (minimum-jerk) ผสม ease-out ให้พีคมาเร็วกว่ากลางทาง
    # สัดส่วนผสม / ความแรงเบรก สุ่มทุกครั้ง
    ease_pow = random.uniform(2.2, 3.5)
    mix = random.uniform(0.25, 0.55)
    shake = min(1.5, dist / 300) if jitter else 0       # สั่นเล็กน้อยระหว่างทาง (ไม่สั่นตอนใกล้ถึง)
    start = time.perf_counter()
    while True:
        t = (time.perf_counter() - start) / duration
        if t >= 1:
            break
        e = mix * (1 - (1 - t) ** ease_pow) + (1 - mix) * (t ** 3 * (10 - 15 * t + 6 * t * t))
        px, py = _path_point((x0, y0), c1, c2, (x, y), e)
        k = shake * (1 - e)
        hover(round(px + random.uniform(-k, k)), round(py + random.uniform(-k, k)))
        if on_step:
            if on_step():
                return True
        else:
            _spin(random.uniform(0.003, 0.006))         # ก้าวละ 3-6ms ไม่สม่ำเสมอแบบเครื่อง
    hover(x, y)
    return False


def fitts_time(dist, width):
    """เวลาที่คนใช้ขยับเมาส์ไปชี้เป้า (วินาที) ตาม Fitts' law, a/b สุ่มในช่วงของคนจริง"""
    a, b = random.uniform(*FITTS_A), random.uniform(*FITTS_B)
    return a + b * math.log2(dist / max(width, 1) + 1)


def move_to(x, y, duration=None, on_step=None, relaxed=False, width=62):
    """ขยับเมาส์ไป (x, y) แบบคน ไม่วาป เส้นทางสุ่มใหม่ทุกครั้ง (เริ่มจากตำแหน่งเมาส์ปัจจุบัน)

    - เส้นโค้ง cubic bezier จุดควบคุมสุ่ม 2 จุด (บางครั้งเป็นรูปตัว S)
    - ความเร็วแบบมือคน: ค่อยๆ เร่ง -> พีค -> เบรก, เวลาขึ้นกับระยะทาง (MOVE_TIME) สุ่ม ±20%, ก้าวไม่สม่ำเสมอ
    - สั่นเล็กน้อยระหว่างทาง
    - ระยะไกล บางครั้งเลยเป้าไปนิดแล้วแก้กลับ (MOVE_OVERSHOOT)
    - on_step: ฟังก์ชันที่เรียกทุกก้าว (เช่น จับภาพเช็กสถานะระหว่างขยับ) คืน True = หยุดกลางทาง
    - relaxed: ขยับแบบคนไม่รีบ เวลาตาม Fitts' law (width = ขนาดเป้า px) ขยับ 2 จังหวะ
    คืน True ถ้าหยุดกลางทาง (on_step สั่ง)
    """
    if not SMOOTH_MOVE:
        hover(x, y)
        return

    x0, y0 = get_cursor()
    dx, dy = x - x0, y - y0
    dist = math.hypot(dx, dy)
    if dist < 2:
        hover(x, y)
        return

    if relaxed:
        mt = fitts_time(dist, width)
        # จังหวะ 1: สะบัดไปเกือบถึง (ส่วนใหญ่ขาดนิดๆ บางครั้งเลยนิดๆ) + คลาดด้านข้างเล็กน้อย
        reach = random.uniform(1.03, 1.08) if random.random() < MOVE_OVERSHOOT else random.uniform(0.90, 0.97)
        px = x0 + dx * reach + random.gauss(0, 3)
        py = y0 + dy * reach + random.gauss(0, 3)
        # แบ่งเวลา Fitts: สะบัด ~70% + หยุด ~5% + แก้ ~20% (รวมใกล้เคียง mt)
        if _glide(x0, y0, px, py, mt * random.uniform(0.65, 0.75), on_step):
            return True
        # หยุดนิดนึงก่อนแก้ (ยังเช็กสถานะระหว่างหยุด)
        end = time.perf_counter() + mt * random.uniform(0.04, 0.10)
        while time.perf_counter() < end:
            if on_step and on_step():
                return True
            _spin(0.005)
        # จังหวะ 2: ขยับแก้เข้าเป้า
        if math.hypot(x - px, y - py) >= 2:
            if _glide(px, py, x, y, max(0.05, mt * random.uniform(0.15, 0.25)), on_step, jitter=False):
                return True
        hover(x, y)
        return False

    if duration is None:
        lo, hi = MOVE_TIME
        duration = (lo + (hi - lo) * min(dist / MOVE_TIME_FULL_DIST, 1.0)) * random.uniform(0.8, 1.2)

    # ระยะไกล: บางครั้งเลยเป้าไป 3-8% แล้วแก้กลับ (คนสะบัดเมาส์มักเลยนิดๆ)
    if dist > 250 and random.random() < MOVE_OVERSHOOT:
        over = random.uniform(0.03, 0.08)
        ox = x + dx * over + random.uniform(-4, 4)
        oy = y + dy * over + random.uniform(-4, 4)
        if _glide(x0, y0, ox, oy, duration * 0.85, on_step):
            return True
        if math.hypot(x - ox, y - oy) >= 2:
            if _glide(ox, oy, x, y, random.uniform(0.03, 0.06), on_step, jitter=False):
                return True
        hover(x, y)
        return False

    return _glide(x0, y0, x, y, duration, on_step)


def _resolve_names(names):
    """ชื่อไอเท็ม -> รายชื่อ template ทั้งหมดของไอเท็มนั้น (None = ทุกไฟล์)"""
    if isinstance(names, str):
        names = [names]
    return [t for t in list_templates() if names is None or item_name(t) in names]


def _detect_all(img, names, scale):
    """detect ทุก template รวมผล ถ้าช่องเดียวกันเจอหลาย template เก็บตัวที่ score สูงกว่า
    คืน list ของ (template_name, item)"""
    targets = []
    for name in names:
        for item in detect(img, name, scale=scale, quiet=True):
            overlap = next((t for t in targets if abs(t[1][0] - item[0]) <= item[2] // 2
                            and abs(t[1][1] - item[1]) <= item[3] // 2), None)
            if overlap is None:
                targets.append((name, item))
            elif item[4] > overlap[1][4]:
                targets[targets.index(overlap)] = (name, item)
    return targets


def _save_debug(img, tag):
    """บันทึกภาพ region ไว้ดูทีหลัง เช่น debug/20261005_145812_123_found.png
    เขียนไฟล์ใน thread แยก ไม่ให้ถ่วงจังหวะกด"""
    if not SAVE_DEBUG:
        return None
    os.makedirs(DEBUG_DIR, exist_ok=True)
    ms = int(time.time() * 1000) % 1000
    path = os.path.join(DEBUG_DIR, f"{time.strftime('%Y%m%d_%H%M%S')}_{ms:03d}_{tag}.png")
    threading.Thread(target=cv2.imwrite, args=(path, img.copy()), daemon=True).start()
    return os.path.relpath(path, BASE_DIR)


def _sleep(seconds):
    """sleep ที่เช็กปุ่มหยุด (STOP_KEY) ระหว่างรอ"""
    end = time.perf_counter() + seconds
    while True:
        if esc_pressed():
            raise ClickAborted(f"กด {STOP_KEY_NAME} -> หยุด")
        left = end - time.perf_counter()
        if left <= 0:
            return
        time.sleep(min(left, 0.05))


def _screen_sig(img):
    """ลายเซ็นของหน้า Mission Result: แถบขอบบน + ขอบล่างของกรอบ (ขาวดำ)
    ส่วนนี้ไม่เปลี่ยนตอนไอเท็มเปลี่ยนสี / เมาส์ hover / tooltip ขึ้น แต่เปลี่ยนแน่ตอนหน้าจอปิด"""
    gray = to_gray(img)
    k = max(6, gray.shape[0] // 25)
    return gray[:k].copy(), gray[-k:].copy()


def _screen_open(img, sig, min_score=SCREEN_MATCH):
    """True ถ้าหน้า Mission Result ยังเปิดอยู่ (ขอบบนหรือขอบล่างอันใดอันหนึ่งยังตรงกับตอนเจอ)
    ใช้ 2 แถบ เผื่อเมาส์ของผู้ใช้บังแถบใดแถบหนึ่ง  คืน (เปิดอยู่ไหม, คะแนน)"""
    score = -1.0
    for now, ref in zip(_screen_sig(img), sig):
        v = float(cv2.matchTemplate(now, ref, cv2.TM_CCOEFF_NORMED).max())
        score = max(score, 1.0 if np.isnan(v) else v)
    return score >= min_score, score


def _hover_point(item, region_box):
    """จุดวางเมาส์บนไอเท็ม: สุ่มภายใน HOVER_ZONE (ครึ่งล่างของช่อง ไม่บังส่วนที่ใช้ detect / วัดสี)"""
    x, y, w, h, _ = item
    rx, ry = region_box[:2]
    (fx0, fx1), (fy0, fy1) = HOVER_ZONE
    return (rx + x + int(w * random.uniform(fx0, fx1)),
            ry + y + int(h * random.uniform(fy0, fy1)))


_header_cache = {}
_loot_state = {"visible": False, "since": 0.0, "dim": False, "normal": False, "last": 0.0, "stamp": "", "dim_count": 0,
               "occ": -1, "occ_since": 0.0}
_empty_cache = {}


def _header_score(img, scale):
    """คะแนนหัวข้อ "Mission Reward" ในภาพ (-1 ถ้าเทียบไม่ได้)"""
    key = round(scale, 3)
    if key not in _header_cache:
        marker = cv2.imread(HEADER_MARKER, cv2.IMREAD_COLOR)
        if marker is not None and scale != 1.0:
            marker = cv2.resize(marker, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        _header_cache[key] = None if marker is None else cv2.cvtColor(marker, cv2.COLOR_BGR2GRAY)
    marker = _header_cache[key]
    gray = to_gray(img)
    if marker is None or gray.shape[0] < marker.shape[0] or gray.shape[1] < marker.shape[1]:
        return -1.0
    v = float(cv2.matchTemplate(gray, marker, cv2.TM_CCOEFF_NORMED).max())
    return -1.0 if np.isnan(v) else v


def _is_empty_slot(img, x, y, w, h, scale, margin=6):
    """True ถ้าช่องนี้ว่าง (เหมือนภาพช่องว่าง)"""
    key = round(scale, 3)
    if key not in _empty_cache:
        ref = cv2.imread(EMPTY_SLOT, cv2.IMREAD_COLOR)
        if ref is not None and scale != 1.0:
            ref = cv2.resize(ref, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        _empty_cache[key] = None if ref is None else cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY)
    ref = _empty_cache[key]
    if ref is None:
        return False
    crop = to_gray(img[max(0, y - margin):y + h + margin, max(0, x - margin):x + w + margin])
    if crop.shape[0] < ref.shape[0] or crop.shape[1] < ref.shape[1]:
        return False
    v = float(cv2.matchTemplate(crop, ref, cv2.TM_CCOEFF_NORMED).max())
    return not np.isnan(v) and v >= EMPTY_MATCH


def _grid_state(img, scale):
    """นับช่องในกรอบ: (ช่องที่มีไอเท็ม, ช่องที่มีไอเท็มและพื้นหลังทึบ)"""
    (ox, oy), (px, py), (nc, nr), (sw, sh) = GRID_ORIGIN, GRID_PITCH, GRID_SIZE, SLOT_WH
    occupied = dim = 0
    for r in range(nr):
        for c in range(nc):
            x = int(round(5 + (ox + px * c) * scale))      # 5 = pad ของ get_region (ไม่คูณ scale)
            y = int(round(5 + (oy + py * r) * scale))
            w, h = int(sw * scale), int(sh * scale)
            if _is_empty_slot(img, x, y, w, h, scale):
                continue
            occupied += 1
            if _slot_bg_v(img, (x, y, w, h, 0)) < BG_READY_V:
                dim += 1
    return occupied, dim


def _save_loot(img, tag):
    """บันทึกภาพ loot ไว้ที่ loot/ (thread แยก ไม่ถ่วงจังหวะกด)"""
    os.makedirs(LOOT_DIR, exist_ok=True)
    path = os.path.join(LOOT_DIR, f"{_loot_state['stamp']}_{tag}.png")
    threading.Thread(target=cv2.imwrite, args=(path, img.copy()), daemon=True).start()
    return os.path.relpath(path, BASE_DIR)


def _loot_log(img, scale):
    """เรียกทุกครั้งที่จับภาพกรอบ: เห็นหัวข้อ Mission Reward -> บันทึกภาพตอนทึบ 1 ภาพ + ตอนสีปกติ 1 ภาพ"""
    st = _loot_state
    now = time.perf_counter()
    # บันทึกตอนทึบไปแล้ว รอภาพตอนสีปกติ -> เช็กทุกเฟรม (ช่วงสีปกติอาจสั้น ของถูกเก็บไปเร็ว)
    waiting_normal = st["visible"] and st["dim"] and not st["normal"]
    if now - st["last"] < LOOT_CHECK_EVERY and st["visible"] and not waiting_normal:
        return
    st["last"] = now

    score = _header_score(img, scale)
    if score < HEADER_MATCH:
        st["visible"] = False
        return
    if not st["visible"]:
        st.update(visible=True, since=now, dim=False, normal=False, stamp=time.strftime("%Y%m%d_%H%M%S"),
                  occ=-1, occ_since=now)
        log.info("-" * 60)
        log.info(f"[loot] เห็น Mission Reward (score={score:.3f})")
        return
    if not SAVE_LOOT or now - st["since"] < LOOT_SETTLE or st["normal"]:
        return

    occ, dim = _grid_state(img, scale)
    if occ != st["occ"]:                    # จำนวนไอเท็มเปลี่ยน (กำลังขึ้น / ถูกเก็บ) -> เริ่มนับความนิ่งใหม่
        st["occ"], st["occ_since"] = occ, now
    if occ == 0:
        return                              # ยังไม่มีไอเท็ม (กำลังโหลด) หรือเก็บหมดแล้ว -> ไม่บันทึก
    stable = now - st["occ_since"] >= LOOT_STABLE

    if not st["dim"]:
        # ภาพแรก: รอไอเท็มขึ้นครบ (จำนวนนิ่ง) ก่อน
        if not stable:
            return
        if dim > 0:
            st["dim"], st["dim_count"] = True, dim
            log.info(f"[loot] ภาพตอนทึบ ({occ} ไอเท็ม, ทึบ {dim})  -> {_save_loot(img, '1_dim')}")
        else:
            st["normal"] = True
            log.info(f"[loot] ภาพตอนสีปกติ ({occ} ไอเท็ม, ปกติตั้งแต่แรก)  -> {_save_loot(img, '2_normal')}")
    elif dim <= st["dim_count"] // 2:
        # บันทึกตอนทึบไปแล้ว -> ช่องทึบลดลงเหลือครึ่งหรือน้อยกว่า (= เริ่มเป็นสีปกติ) บันทึกทันที ก่อนถูกเก็บ
        # (ไม่รอให้ทึบเป็น 0 เพราะ tooltip / เมาส์ที่ hover อาจทำให้บางช่องดูทึบค้าง)
        st["normal"] = True
        log.info(f"[loot] ภาพตอนสีปกติ ({occ} ไอเท็ม, ทึบเหลือ {dim} จาก {st['dim_count']})  "
                 f"-> {_save_loot(img, '2_normal')}")


_loot_cache = {}


def _is_random_loot(img, scale):
    """True ถ้าในกรอบมีกล่อง "?" (โหมด random loot)  คืน (ใช่ไหม, คะแนน)"""
    key = round(scale, 3)
    if key not in _loot_cache:
        box = cv2.imread(LOOT_BOX, cv2.IMREAD_COLOR)
        if box is None:
            _loot_cache[key] = None
        else:
            if scale != 1.0:
                box = cv2.resize(box, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            top = box[:int(box.shape[0] * 0.68)]       # ส่วนบน เหมือน template ไอเท็ม
            _loot_cache[key] = cv2.cvtColor(top, cv2.COLOR_BGR2GRAY)
    marker = _loot_cache[key]
    if marker is None:
        return False, -1.0
    score = float(np.nan_to_num(cv2.matchTemplate(to_gray(img), marker, cv2.TM_CCOEFF_NORMED), nan=-1).max())
    return score >= LOOT_MATCH, score


def _slot_bg_v(img, item):
    """ความสว่างเฉลี่ย (V ของ HSV) ของพื้นหลังช่องไอเท็ม: วัดจุดเล็กๆ ที่มุมบนซ้าย/ขวา + ขอบกลางซ้าย/ขวา
    ซึ่งไอคอนไม่ค่อยบัง และเมาส์ที่ hover มุมขวาล่างไม่บัง"""
    x, y, w, h, _ = item
    slot = img[y:y + h, x:x + w]
    fx = lambda f: int(w * f)
    fy = lambda f: int(h * f)
    patches = [slot[fy(.06):fy(.15), fx(.06):fx(.16)], slot[fy(.06):fy(.15), fx(.84):fx(.94)],
               slot[fy(.46):fy(.55), fx(.03):fx(.10)], slot[fy(.46):fy(.55), fx(.90):fx(.97)]]
    patches = [p for p in patches if p.size]
    if not patches:
        return 0.0
    # ค่ากลาง (median) ของ 4 จุด: ถ้าลูกศรเมาส์ไปบังจุดใดจุดหนึ่ง ค่าไม่เพี้ยน
    return float(np.median([cv2.cvtColor(p, cv2.COLOR_BGR2HSV)[..., 2].mean() for p in patches]))


_get_cache = {}


def _get_score(img, target, scale, margin=8):
    """คะแนนป้าย "GET!" ในช่องของไอเท็ม (ขึ้นตอนเมาส์ hover บนไอเท็มที่พร้อมเก็บ)"""
    key = round(scale, 3)
    if key not in _get_cache:
        marker = cv2.imread(GET_MARKER, cv2.IMREAD_COLOR)
        if marker is not None:
            if scale != 1.0:
                marker = cv2.resize(marker, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            marker = cv2.cvtColor(marker[:int(marker.shape[0] * 0.68)], cv2.COLOR_BGR2GRAY)  # ส่วนบน ไม่เอาตัวเลข
        _get_cache[key] = marker
    marker = _get_cache[key]
    if marker is None:
        return -1.0
    _, (x, y, w, h, _) = target
    crop = to_gray(img[max(0, y - margin):y + h + margin, max(0, x - margin):x + w + margin])
    if crop.shape[0] < marker.shape[0] or crop.shape[1] < marker.shape[1]:
        return -1.0
    v = float(cv2.matchTemplate(crop, marker, cv2.TM_CCOEFF_NORMED).max())
    return -1.0 if np.isnan(v) else v


def _item_score(img, target, scale, margin=8):
    """คะแนนสูงสุดของไอเท็มในพื้นที่เล็กๆ รอบตำแหน่งเดิม (<1ms) เทียบกับทุกภาพของไอเท็มนั้น
    ใช้ส่วนบนของไอคอน (เหนือตัวเลข) -> เมาส์ที่ hover มุมขวาล่างไม่ทำให้คะแนนตก"""
    name, (x, y, w, h, _) = target
    x1, y1 = max(0, x - margin), max(0, y - margin)
    best = -1.0
    for t in _resolve_names(item_name(name)):
        th, tw = load_template(t, scale)[0].shape[:2]    # แต่ละภาพขนาดไม่เท่ากัน -> ตัดพื้นที่ให้พอ
        crop = img[y1:y + max(h, th) + margin, x1:x + max(w, tw) + margin]
        best = max(best, best_score(crop, t, scale))
    return best


# ลายเซ็นหน้าจอของรอบล่าสุด (wait_gone ใช้เช็กว่าหน้า Mission Result ปิดหรือยัง)
_round_sig = None


def wait_and_click(screen, names=None, region="mission_reward", timeout=None,
                   search_interval=0.03, watch_interval=0.005, check_interval=0.5,
                   min_sat=READY_SATURATION, dry_run=False, hold=0.05):
    """รอไอเท็มขึ้นสีปกติแล้วคลิก (1 รอบ = 1 หน้า Mission Result) แบ่งเป็น 2 ช่วง

    names   : ชื่อไอเท็มที่ต้องการคลิก เช่น "carrot" หรือ ["carrot", "skybug_spike"]
              ถ้า None = ทุกไฟล์ในโฟลเดอร์ templates/
              (ไอเท็มเดียวใช้ได้หลายภาพ: skybug_spike.png + skybug_spike-color.png)
    timeout : วินาที ถ้า None = รอไม่มีกำหนด
    ลำดับความสำคัญ = ลำดับใน names: เก็บอันดับ 1 ให้หมดก่อน แล้วค่อยไปอันดับถัดไป

    ช่วงที่ 1 ค้นหา : จับภาพ + detect ทุก template ทุก search_interval จนกว่าจะเจอ
    ช่วงที่ 2 เฝ้าดู : ขยับเมาส์ (แบบคน) ไป hover บนไอเท็มตั้งแต่ยังเทา
                      วัดแค่ saturation ที่ตำแหน่งเดิมทุก watch_interval (ไม่ detect ซ้ำ)
                      พอขึ้นสี -> เช็กว่าหน้าจอยังเปิด -> กดทันที (เมาส์อยู่บนไอเท็มแล้ว)
                      -> ขยับไป hover ตัวถัดไป  (คลิกครบแล้วเมาส์อยู่ตรงนั้น ไม่ดีดกลับ)
                      ทุก check_interval เช็กว่าหน้าจอยังเปิดอยู่ไหม ถ้าปิดแล้ว -> เลิกรอ

    หยุดเมื่อคลิกครบทุกตัว หรือหมดเวลา timeout
    กด F12 ค้าง -> raise ClickAborted
    คืนจำนวนที่คลิกไปแล้ว
    """
    global _round_sig

    if isinstance(names, str):
        names = [names]
    # ลำดับความสำคัญของไอเท็ม = ลำดับชื่อที่ส่งเข้ามา (None = ทุกไอเท็ม เรียงตามตัวอักษร)
    priority = list(dict.fromkeys(names)) if names else sorted({item_name(t) for t in list_templates()})
    names = _resolve_names(names)
    if not names:
        log.info("[wait_and_click] ไม่มี template ที่ตรงกับชื่อที่ระบุ")
        return 0

    scale = get_scale(screen)
    region_box = screen.get_region(region)
    masks = {name: load_template(name, scale)[1] for name in names}
    down, up = MOUSE_FLAGS["left"]

    start = time.perf_counter()
    elapsed_ms = lambda: (time.perf_counter() - start) * 1000
    timed_out = lambda: timeout is not None and elapsed_ms() > timeout * 1000

    # ---- ช่วงที่ 1: ค้นหา (ยังไม่ log จนกว่าจะเจอ จะได้ไม่รก log ตอนรอนานๆ) ----
    targets = []
    detect_ms = 0.0
    while not targets:
        if timed_out():
            log.info(f"[wait_and_click] หมดเวลา {timeout}s (ไม่เจอ {names})")
            return 0
        t0 = time.perf_counter()
        img = screen.grab(region_box)
        _loot_log(img, scale)
        # หาไอเท็มเฉพาะตอนที่เห็นหัวข้อ "Mission Reward" (= อยู่หน้า Mission Result จริง)
        # ไม่งั้นแครอทในฉากเกมอาจถูกจับเป็นไอเท็ม แล้วเมาส์ไปคลิกกลางฉาก
        if _header_score(img, scale) >= HEADER_MATCH:
            targets = _detect_all(img, names, scale)
        detect_ms = (time.perf_counter() - t0) * 1000
        if not targets:
            _sleep(search_interval)

    start = time.perf_counter()    # จับเวลาใหม่ตั้งแต่เจอ
    sig = _round_sig = _screen_sig(img)
    random_loot, loot_score = _is_random_loot(img, scale)
    log.info("=" * 60)
    sw, sh = screen.get_screen_rect()[2:]
    log.info(f"[wait_and_click] ลำดับ: {' > '.join(priority)}  จอ={sw}x{sh}  scale={scale:.3f} "
             f"(template x{scale:.2f})  region={region_box}  min_sat={min_sat}  dry_run={dry_run}")
    normal = ("ปกติ -> กดทันทีที่เจอ เก็บได้แล้วไปช่องถัดไป" if CLICK_ON_FOUND
              else "ปกติ -> กดตอนพร้อม เก็บได้แล้วไปช่องถัดไป")
    log.info(f"  โหมด: {'random loot -> กดทันทีที่เจอ ไม่รอขึ้นสี' if random_loot else normal}"
             f"  (กล่อง ? score={loot_score:.3f})")

    targets.sort(key=lambda t: (t[1][1], t[1][0]))    # เรียงตามตำแหน่ง บนลงล่าง ซ้ายไปขวา
    log.info(f"  {elapsed_ms():7.0f}ms  เจอ {len(targets)} ตัว  "
             f"(grab+detect {len(names)} template {detect_ms:.0f}ms)")

    # เลือกช่องแรก: ไอเท็มอันดับสูงสุดที่เจอ -> ช่องที่กลาง/ขวาที่สุด (ช่องอื่นเก็บต่อทีหลังทีละช่อง)
    # คะแนนความชอบ (ยิ่งน้อยยิ่งดี): ระยะจากกลางกรอบ โดยชิ้นที่อยู่ฝั่งซ้ายนับระยะแนวนอนเพิ่ม LEFT_PENALTY เท่า
    cx, cy = img.shape[1] / 2, img.shape[0] / 2

    def pref(item):
        dx = item[0] + item[2] / 2 - cx
        dy = item[1] + item[3] / 2 - cy
        return ((dx if dx >= 0 else -dx * LEFT_PENALTY) ** 2 + dy ** 2) ** 0.5

    rank = lambda t: (priority.index(item_name(t[0])) if item_name(t[0]) in priority else len(priority),
                      pref(t[1]))
    first = min(targets, key=rank)
    for i, (name, item) in enumerate(sorted(targets, key=rank), 1):
        note = "  -> เก็บช่องนี้ก่อน" if (name, item) == first else "  -> รอคิว"
        log.info(f"             #{i}  {item_name(name):<14} x={item[0]:4d} y={item[1]:4d} score={item[4]:.3f} "
                 f"sat={saturation(img, item, masks[name]):5.1f} pref={pref(item):5.1f}{note}")
    targets = [first]
    if targets:
        log.info(f"             debug: {_save_debug(img, 'found')}")

    # ---- ช่วงที่ 2: เก็บทีละช่อง เก็บได้แล้วย้ายไปช่องถัดไป ------------------------
    # สถานะของช่องที่กำลังเก็บ
    #   "first"      : เพิ่งเจอ -> กดทันที (CLICK_ON_FOUND / random loot) หรือรอพร้อมแล้วกด
    #   "wait_ready" : ช่องใหม่ที่ย้ายมา -> รอพร้อม (ป้าย GET! / พื้นหลังปกติ / สีปกติ) แล้วกด
    #   "clicked"    : กดแล้ว -> ถ้ากลับมาทึบแว้บนึง หรือช่องว่าง = เก็บได้แล้ว -> ไปหาช่องอื่น
    #                  ถ้ายังพร้อมกดอยู่นานเกิน RETRY_AFTER = คลิกไม่เข้า -> กดซ้ำ
    pending = list(targets)
    state = {t: "first" for t in pending}
    clicks = {t: 0 for t in pending}
    absent_since = {}
    header_miss = 0         # หัวข้อ Mission Reward หายติดกันกี่เฟรม
    last_click = {}
    shown_get = {}
    shown_bg = {}
    collected = []          # ช่องที่เก็บไปแล้ว (ไม่กลับไปกดซ้ำ)
    verify = {}             # ช่องที่เพิ่งกด -> เวลาที่กด (ยืนยันเบื้องหลังว่าเก็บได้ ระหว่างไปช่องถัดไป)
    last_any = [0.0]        # เวลาคลิกล่าสุด (ช่องไหนก็ได้)
    hover_pt = {}           # จุด hover ที่สุ่มไว้ของแต่ละช่อง
    searching = {}          # ชนิดไอเท็มที่ยังหาช่องถัดไปไม่เจอ -> สแกนซ้ำจนหน้าจอปิด
    last_rescan = 0.0
    loops, loop_ms_total = 0, 0.0
    clicked = 0
    last_check = time.perf_counter()
    hovering = None

    def same_slot(a, b):
        return abs(a[0] - b[0]) < b[2] // 2 and abs(a[1] - b[1]) < b[3] // 2

    def hover_on(target):
        """ขยับเมาส์ (แบบคน) ไปวางบนไอเท็ม รอแล้วกดได้ทันที"""
        nonlocal hovering
        if hovering is target:
            return
        hx, hy = hover_pt.setdefault(target, _hover_point(target[1], region_box))
        t0 = time.perf_counter()
        mode = "dry_run"
        if not dry_run:
            def slot_ready(frame):
                name_, item_ = target
                return (_slot_bg_v(frame, item_) >= BG_READY_V or saturation(frame, item_, masks[name_]) >= min_sat
                        or _get_score(frame, target, scale) >= GET_MATCH)

            # ระหว่างขยับเมาส์ ยังจับภาพเช็ก: ภาพ loot / ช่องที่รอยืนยัน / (โหมดคน) ไอเท็มพร้อมกดหรือยัง
            n_step = [0]
            lost = [0]          # หัวข้อ Mission Reward หายติดกันกี่เฟรมระหว่างขยับ

            def step(check_ready=False):
                n_step[0] += 1
                if n_step[0] % STEP_CHECK_EVERY:        # จับภาพแค่ทุก STEP_CHECK_EVERY ก้าว
                    _spin(random.uniform(0.003, 0.005))
                    return False
                frame = screen.grab(region_box)
                _loot_log(frame, scale)        # ภาพ loot ตอนสีปกติ อาจเกิดระหว่างขยับเมาส์
                if _header_score(frame, scale) < HEADER_MATCH:
                    lost[0] += 1
                    if lost[0] >= HEADER_LOST_FRAMES:
                        return True            # ออกจากหน้า Mission Result แล้ว -> หยุดขยับทันที
                else:
                    lost[0] = 0
                if verify:
                    check_verify(frame)
                return check_ready and slot_ready(frame)

            if not HUMAN_HOVER or slot_ready(screen.grab(region_box)):
                mode = "เร็ว (พร้อมกดแล้ว)"
                move_to(hx, hy, on_step=step)
            else:
                # ช่องยังทึบ ไม่รีบ: รอเวลาตอบสนองแบบคน แล้วขยับตาม Fitts' law
                mode = "แบบคน (ช่องยังทึบ)"
                became_ready = False
                end = time.perf_counter() + random.uniform(*REACTION_TIME)
                while time.perf_counter() < end:
                    if step(True):
                        became_ready = True
                        break
                    _spin(0.005)
                if became_ready or move_to(hx, hy, on_step=lambda: step(True), relaxed=True, width=target[1][2]):
                    if lost[0] < HEADER_LOST_FRAMES:
                        mode = "แบบคน -> ไอเท็มพร้อมกดระหว่างทาง เร่งให้ถึง"
                        move_to(hx, hy, on_step=step)
            if lost[0] >= HEADER_LOST_FRAMES:
                log.info(f"  {elapsed_ms():7.0f}ms  หัวข้อ Mission Reward หายระหว่างขยับเมาส์ -> หยุดขยับ ไม่คลิก")
                return
        hovering = target
        log.info(f"  {elapsed_ms():7.0f}ms  hover รอที่ {item_name(target[0])} x={target[1][0]} y={target[1][1]} "
                 f"screen=({hx}, {hy})  ขยับ{mode} {(time.perf_counter() - t0) * 1000:.0f}ms")

    def drop(target):
        nonlocal hovering
        pending.remove(target)
        absent_since.pop(target, None)
        if hovering is target:
            hovering = None

    def check_verify(img):
        """ช่องที่กดไปแล้ว (รอยืนยันเบื้องหลัง): ทึบแว้บ/ช่องว่าง = เก็บได้แล้ว
        ไม่ทึบเลยนานเกิน RETRY_AFTER = คลิกไม่เข้า -> กลับไปกดใหม่"""
        now_ = time.perf_counter()
        for t, (t_click, wait_) in list(verify.items()):
            name_, item_ = t
            # ทึบแว้บเกิดกับทั้งกรอบ -> นับเป็นการยืนยันได้เฉพาะคลิกล่าสุดเท่านั้น
            # ถ้ากดช่องถัดไปไปแล้วแต่ช่องนี้ยังไม่เคยทึบแว้บ = คลิกนี้ไม่เข้า
            newer_click = t_click < last_any[0]
            present_ = _get_score(img, t, scale) >= GET_MATCH or _item_score(img, t, scale) >= ITEM_MATCH
            ready_ = (_slot_bg_v(img, item_) >= BG_READY_V or saturation(img, item_, masks[name_]) >= min_sat)
            if not newer_click and (not present_ or not ready_):
                del verify[t]
                collected.append(item_)
                log.info(f"  {elapsed_ms():7.0f}ms  {name_} x={item_[0]} เก็บได้แล้ว "
                         f"({'ช่องว่าง' if not present_ else 'ทึบแว้บ'} หลังกด {(now_ - t_click) * 1000:.0f}ms)")
            elif newer_click or now_ - t_click >= wait_:
                del verify[t]
                if clicks[t] >= MAX_CLICKS_PER_ITEM:
                    log.info(f"  {elapsed_ms():7.0f}ms  {name_} x={item_[0]} กดครบ {MAX_CLICKS_PER_ITEM} ครั้งแล้ว -> ถือว่าเก็บแล้ว ไม่กดอีก")
                    collected.append(item_)
                    continue
                pending.insert(0, t)
                state[t] = "wait_ready"
                reason = "ไม่ทึบแว้บก่อนกดช่องถัดไป" if newer_click else f"ไม่ทึบเลย {wait_:.2f}s"
                log.info(f"  {elapsed_ms():7.0f}ms  {name_} x={item_[0]} {reason} (คลิกไม่เข้า) -> กลับไปกดใหม่")

    def find_next(key, img):
        """หาช่องถัดไปของไอเท็มชนิด key (ไม่เอาช่องที่เก็บไปแล้ว / กำลังเก็บ / รอยืนยัน) เลือกกลาง/ขวาก่อน"""
        busy = [t[1] for t in pending] + [t[1] for t in verify]
        cands = [t for t in _detect_all(img, names, scale)
                 if item_name(t[0]) == key
                 and not any(same_slot(t[1], c) for c in collected)
                 and not any(same_slot(t[1], b) for b in busy)]
        return min(cands, key=lambda t: pref(t[1])) if cands else None

    def pick_next(img):
        """ช่องถัดไป: ไล่ตามลำดับความสำคัญ อันดับ 1 ยังเหลือ -> เก็บอันดับ 1 ก่อนเสมอ"""
        for key in priority:
            new = find_next(key, img)
            if new is not None:
                return new
        return None

    def move_on(target, img, why):
        """ช่องนี้เสร็จแล้ว -> ไปช่องถัดไปตามลำดับความสำคัญ (ไม่เจอ = สแกนหาต่อจนหน้าจอปิด)"""
        drop(target)
        new = pick_next(img)
        if new is None:
            searching["any"] = True
            log.info(f"  {elapsed_ms():7.0f}ms  {item_name(target[0])} {why} -> ไม่เหลือไอเท็มที่ต้องเก็บ สแกนหาต่อ")
            return
        pending.append(new)
        state[new], clicks[new] = "wait_ready", 0
        targets.append(new)
        log.info(f"  {elapsed_ms():7.0f}ms  {item_name(target[0])} {why} -> ถัดไป: {item_name(new[0])} "
                 f"x={new[1][0]} y={new[1][1]} score={new[1][4]:.3f}")

    while pending or searching or verify:
        if esc_pressed():
            raise ClickAborted(f"กด {STOP_KEY_NAME} -> หยุด")
        if timed_out():
            log.info(f"  หมดเวลา {timeout}s (ยังรออยู่ {len(pending)} ตัว)")
            break

        if pending and header_miss == 0:        # ไม่ขยับเมาส์ถ้าเฟรมล่าสุดไม่เห็นหน้า Mission Result
            hover_on(pending[0])

        t0 = time.perf_counter()
        img = screen.grab(region_box)
        _loot_log(img, scale)

        # ต้องเห็นหัวข้อ "Mission Reward" ทุกเฟรม ไม่งั้นไม่คลิก / ไม่ขยับเมาส์
        on_screen = _header_score(img, scale) >= HEADER_MATCH
        header_miss = 0 if on_screen else header_miss + 1
        if header_miss >= HEADER_LOST_FRAMES:
            log.info(f"  {elapsed_ms():7.0f}ms  ไม่เห็นหัวข้อ Mission Reward ติดกัน {header_miss} เฟรม "
                     f"-> ออกจากหน้า Mission Result แล้ว เลิกรอ ไม่คลิก  debug: {_save_debug(img, 'left')}")
            break
        if not on_screen:
            time.sleep(watch_interval)
            continue

        check_verify(img)
        loops += 1
        now = time.perf_counter()

        # เช็กว่าหน้าจอยังเปิดอยู่ เป็นระยะ
        if now - last_check >= check_interval:
            last_check = now
            is_open, score = _screen_open(img, sig)
            if not is_open:
                log.info(f"  {elapsed_ms():7.0f}ms  หน้า Mission Result ปิดแล้ว (score={score:.3f}) "
                         f"-> เลิกรอ  debug: {_save_debug(img, 'closed')}")
                break

        # ไอเท็มที่ยังหาช่องถัดไปไม่เจอ -> สแกนซ้ำ
        if searching and now - last_rescan >= RESCAN_EVERY:
            last_rescan = now
            new = pick_next(img)
            if new is not None:
                searching.clear()
                pending.append(new)
                state[new], clicks[new] = "wait_ready", 0
                targets.append(new)
                log.info(f"  {elapsed_ms():7.0f}ms  เจอ {item_name(new[0])} ช่องใหม่ x={new[1][0]} y={new[1][1]} "
                         f"score={new[1][4]:.3f}")

        for target in list(pending):
            name, item = target
            sat = saturation(img, item, masks[name])
            score = _item_score(img, target, scale)
            get_score = _get_score(img, target, scale)
            showing_get = get_score >= GET_MATCH
            bg_v = _slot_bg_v(img, item)
            bg_ready = bg_v >= BG_READY_V
            # พร้อมกด = อันใดอันหนึ่ง: ป้าย "GET!" (ตอน hover) / พื้นหลังช่องสว่างปกติ / ไอคอนสีปกติ
            ready = showing_get or bg_ready or sat >= min_sat
            present = showing_get or score >= ITEM_MATCH
            if showing_get != shown_get.get(target, False):
                shown_get[target] = showing_get
                log.info(f"  {elapsed_ms():7.0f}ms  {name} {'ขึ้นป้าย GET!' if showing_get else 'ป้าย GET! หายไป'} "
                         f"(get={get_score:.3f})")
            if bg_ready != shown_bg.get(target):
                shown_bg[target] = bg_ready
                log.info(f"  {elapsed_ms():7.0f}ms  {name} พื้นหลังช่อง{'ปกติ' if bg_ready else 'ทึบ'} (bg={bg_v:5.1f})")

            # กดไปแล้ว: กลับมาทึบแว้บนึง / ช่องว่าง = เก็บได้แล้ว -> ไปช่องถัดไป
            if state[target] == "clicked" and (not present or not ready):
                collected.append(item)
                move_on(target, img, f"เก็บแล้ว ({'ช่องว่าง' if not present else f'กลับมาทึบ bg={bg_v:.0f}'})")
                continue

            # ไม่เห็นไอเท็มในช่อง (ยังไม่เคยกด): รอ ถ้านานเกิน REPICK_AFTER ไปหาช่องอื่น
            if not present:
                if target not in absent_since:
                    absent_since[target] = now
                    log.info(f"  {elapsed_ms():7.0f}ms  {name} ไม่เห็นในช่อง (score={score:.3f}) -> ยังไม่กด  "
                             f"debug: {_save_debug(img, 'absent_' + name)}")
                elif GONE_AFTER is not None and now - absent_since[target] >= GONE_AFTER:
                    drop(target)
                    log.info(f"  {elapsed_ms():7.0f}ms  {name} หายจากช่องเกิน {GONE_AFTER}s -> เลิกรอตัวนี้")
                elif now - absent_since[target] >= REPICK_AFTER:
                    move_on(target, img, f"ไม่เห็นในช่องเกิน {REPICK_AFTER}s")
                continue
            if target in absent_since:
                log.info(f"  {elapsed_ms():7.0f}ms  {name} กลับมาในช่อง (score={score:.3f})")
                del absent_since[target]

            # ตัดสินว่าต้องกดไหม
            why = None
            if state[target] == "first":
                if random_loot:
                    why = "random loot"
                elif CLICK_ON_FOUND:
                    why = "เจอแล้ว"
                elif ready:
                    why = "พร้อมกด"
            elif state[target] == "wait_ready" and ready:
                why = "GET!" if showing_get else "พร้อมกด"
            elif state[target] == "clicked" and now - last_click[target] >= RETRY_AFTER:
                why = "ยังพร้อมกดอยู่ (คลิกก่อนหน้าอาจไม่เข้า)"
            if why is None:
                continue

            # เว้นระยะจากคลิกก่อนหน้า ให้เห็นทึบแว้บของคลิกนั้นก่อน (ไม่งั้นแยกไม่ออกว่าทึบเพราะคลิกไหน)
            if not SINGLE_CLICK and time.perf_counter() - last_any[0] < CLICK_GAP:
                continue

            # ก่อนกด: หน้าจอยังเปิดอยู่ไหม (เห็นหัวข้อในเฟรมนี้ + ขอบกรอบตรงกับตอนเจอ)
            is_open, _ = _screen_open(img, sig)
            if not on_screen or not is_open:
                continue

            t_ready = elapsed_ms()
            hx, hy = hover_pt.setdefault(target, _hover_point(item, region_box))
            if not dry_run:
                if hovering is not target:      # ไม่ได้ hover รอตัวนี้อยู่ -> ขยับไปหาก่อน
                    move_to(hx, hy)
                    time.sleep(MOVE_DELAY)
                _send_mouse(down)
            t_down = elapsed_ms()
            if not dry_run:
                time.sleep(random.uniform(*CLICK_HOLD))
                _send_mouse(up)
            hovering = target
            clicks[target] += 1
            clicked += 1
            last_click[target] = last_any[0] = time.perf_counter()
            log.info(f"  {t_ready:7.0f}ms  {name} {why} sat={sat:5.1f} bg={bg_v:5.1f} score={score:.3f} "
                     f"get={get_score:.3f} -> click #{clicks[target]} screen=({hx}, {hy})  "
                     f"(กดลง {t_down - t_ready:.0f}ms หลังพร้อมกด)" + ("  [dry_run]" if dry_run else "")
                     + f"  debug: {_save_debug(img, 'click_' + name)}")

            if random_loot:
                drop(target)
                log.info(f"  {elapsed_ms():7.0f}ms  {name} random loot กดแล้ว -> จบตัวนี้")
            elif SINGLE_CLICK:
                # กดครั้งเดียว ถือว่าเก็บแล้ว ไม่กลับมากดช่องนี้ซ้ำ -> ไปช่องถัดไปทันที
                collected.append(item)
                move_on(target, img, "กดแล้ว (ครั้งเดียว) -> ไปช่องถัดไปทันที")
            elif not ready:
                # กดตอนยังทึบ (CLICK_ON_FOUND) -> ยังไม่นับว่าเก็บ รอช่องนี้พร้อมแล้วกดอีกครั้ง
                state[target] = "wait_ready"
            else:
                # กดตอนพร้อมแล้ว -> ไปช่องถัดไปทันที ช่องนี้รอยืนยันเบื้องหลัง (ทึบแว้บ = เก็บได้)
                verify[target] = (last_click[target], RETRY_AFTER * random.uniform(*RETRY_JITTER))
                move_on(target, img, "กดแล้ว -> ไปช่องถัดไปทันที (ช่องเดิมยืนยันเบื้องหลัง)")

        loop_ms_total += (time.perf_counter() - t0) * 1000
        time.sleep(watch_interval)

    log.info(f"  เก็บได้ {len(collected)} ช่อง")

    if loops:
        log.info(f"  เฝ้าดูสี {loops} รอบ  เฉลี่ย {loop_ms_total / loops:.1f}ms/รอบ "
                 f"(+ รอ {watch_interval * 1000:.0f}ms)")
    log.info(f"  สรุป: เก็บ {len(targets)} ช่อง  คลิกรวม {clicked} ครั้ง  รวม {elapsed_ms():.0f}ms")
    return clicked


def wait_gone(screen, names=None, region="mission_reward", interval=0.3, confirm=3):
    """รอจนหน้า Mission Result ปิด (ขอบกรอบไม่ตรงกับตอนเจอ ติดกัน confirm ครั้ง)
    ใช้หลังคลิกเสร็จ กันไม่ให้รอบถัดไปคลิกไอเท็มเดิมซ้ำ
    เมาส์ค้างอยู่บนไอเท็ม / tooltip ขึ้น ก็ไม่ทำให้เข้าใจผิดว่าหน้าจอปิด
    กด F12 ค้าง -> raise ClickAborted"""
    region_box = screen.get_region(region)
    if _round_sig is None:
        return

    start = time.perf_counter()
    misses = 0
    while misses < confirm:
        frame = screen.grab(region_box)
        _loot_log(frame, get_scale(screen))
        is_open, _ = _screen_open(frame, _round_sig)
        misses = 0 if is_open else misses + 1
        if misses < confirm:
            _sleep(interval)
    log.info(f"  หน้า Mission Result ปิดแล้ว (รอ {time.perf_counter() - start:.1f}s)")


# ---------------------------------------------------------------------------
# ทดสอบ: รอไอเท็มขึ้นสีปกติ -> คลิก
#   python click.py                 -> คลิกทุก template ในโฟลเดอร์ templates/
#   python click.py carrot skybug_spike  -> คลิกเฉพาะที่ระบุ
#   python click.py --dry           -> ไม่คลิก แค่ log (ใช้ร่วมกับแบบอื่นได้)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    from screen import Screen

    dry_run = "--dry" in sys.argv
    names = [a for a in sys.argv[1:] if not a.startswith("--")] or None

    for n in range(3, 0, -1):
        print(f"เริ่มใน {n}... (สลับไปหน้าเกม / กด {STOP_KEY_NAME} ค้างเพื่อหยุด)")
        time.sleep(1)

    try:
        wait_and_click(Screen(), names, timeout=30, dry_run=dry_run)
    except ClickAborted as e:
        log.info(f"  {e}")
