"""
screen.py - จับภาพหน้าจอ / หาตำแหน่ง region แบบ normalized (0-1)

พิกัดทั้งหมดใน REGIONS อ้างอิงจาก "พื้นที่เกม 16:9" (ไม่ใช่ทั้งจอ)
จึงใช้ได้ทุกความละเอียด เช่น 1920x1080, 2560x1440, 3840x2160
และรองรับจอ 21:9 / 16:10 (ชดเชยขอบด้วย offset อัตโนมัติ)
"""

import ctypes
from ctypes import wintypes

import cv2
import mss

# ปิด CAPTUREBLT ของ mss: ทำให้เมาส์กระพริบทุกครั้งที่จับภาพจอ
# (จำเป็นแค่จับหน้าต่างโปร่งใสแบบ layered ซึ่งไม่ได้ใช้)
try:
    import mss.windows.gdi as _mss_gdi
    _mss_gdi.CAPTUREBLT = 0
except Exception:
    pass
import numpy as np

# ---------------------------------------------------------------------------
# ตั้งค่า
# ---------------------------------------------------------------------------

GAME_ASPECT = 16 / 9

# (x1, y1, x2, y2) แบบ normalized 0-1 วัดจากภาพ 2560x1440
REGIONS = {
    # y1 = 710 - 20: ขยายขึ้นไป 20px ให้เห็นหัวข้อ "Mission Reward" (ใช้บันทึกภาพทุกหน้า Mission Result)
    "mission_reward": (695 / 2560, 690 / 1440, 1220 / 2560, 945 / 1440),
}

# ให้ Windows คืนค่าพิกเซลจริง (กันปัญหา Display Scaling 125% / 150%)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except (AttributeError, OSError):
    ctypes.windll.user32.SetProcessDPIAware()


# ---------------------------------------------------------------------------
# หาตำแหน่งหน้าต่างเกม
# ---------------------------------------------------------------------------

def find_window_rect(title):
    """คืน (left, top, width, height) ของพื้นที่ภายในหน้าต่าง (ไม่รวมขอบ/title bar)
    ถ้าหาหน้าต่างไม่เจอคืน None"""
    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        return None

    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    point = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(point))
    return point.x, point.y, rect.right, rect.bottom


# ---------------------------------------------------------------------------
# Screen
# ---------------------------------------------------------------------------

class Screen:
    backend = "mss"

    def close(self):
        pass

    def __init__(self, window_title=None, monitor=1):
        """
        window_title : ชื่อหน้าต่างเกม (โหมด windowed) ถ้า None จะใช้ทั้งจอ
        monitor      : หมายเลขจอ (1 = จอหลัก) ใช้เมื่อ window_title เป็น None
        """
        self.window_title = window_title
        self.monitor = monitor
        self.sct = mss.MSS()
        self.region_offset = (0, 0)          # หน้าต่างรางวัลเลื่อนจากตำแหน่งปกติ (dx, dy) px: ตั้งโดย locate_header อัตโนมัติ

    # ---- พื้นที่ ----------------------------------------------------------

    def get_screen_rect(self):
        """(left, top, width, height) ของจอหรือหน้าต่างเกม"""
        if self.window_title:
            rect = find_window_rect(self.window_title)
            if rect is None:
                raise RuntimeError(f"ไม่พบหน้าต่าง: {self.window_title!r}")
            return rect

        mon = self.sct.monitors[self.monitor]
        return mon["left"], mon["top"], mon["width"], mon["height"]

    def get_game_rect(self):
        """(left, top, width, height) ของพื้นที่เกม 16:9 ที่อยู่กลางจอ"""
        left, top, screen_w, screen_h = self.get_screen_rect()

        game_w = min(screen_w, screen_h * GAME_ASPECT)
        game_h = game_w / GAME_ASPECT
        off_x = (screen_w - game_w) / 2
        off_y = (screen_h - game_h) / 2

        return left + off_x, top + off_y, game_w, game_h

    def get_region(self, name, pad=5):
        """แปลง region ชื่อ name เป็นพิกัดจริงบนจอ (x1, y1, x2, y2)"""
        nx1, ny1, nx2, ny2 = REGIONS[name]
        gx, gy, gw, gh = self.get_game_rect()

        ox, oy = getattr(self, "region_offset", (0, 0))
        x1 = int(gx + nx1 * gw) - pad + ox
        y1 = int(gy + ny1 * gh) - pad + oy
        x2 = int(gx + nx2 * gw) + pad + ox
        y2 = int(gy + ny2 * gh) + pad + oy
        return x1, y1, x2, y2

    # ---- จับภาพ -----------------------------------------------------------

    def grab(self, box=None):
        """จับภาพ box = (x1, y1, x2, y2) ถ้า None จับทั้งจอ/หน้าต่าง
        คืนค่า numpy array แบบ BGR (ใช้กับ OpenCV ได้ทันที)"""
        if box is None:
            left, top, w, h = self.get_screen_rect()
            box = (left, top, left + w, top + h)

        x1, y1, x2, y2 = box
        shot = self.sct.grab({"left": x1, "top": y1, "width": x2 - x1, "height": y2 - y1})
        return cv2.cvtColor(np.asarray(shot), cv2.COLOR_BGRA2BGR)

    def grab_region(self, name, pad=5):
        """จับภาพเฉพาะ region ตามชื่อ"""
        return self.grab(self.get_region(name, pad))
