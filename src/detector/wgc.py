"""
wgc.py - อ่านภาพจากหน้าต่างเกมตาม process (.exe) ด้วย Windows Graphics Capture (ผ่านไลบรารี windows-capture)

แทน mss (จับจอ): ได้เฟรมตามที่ "เกม" วาดจริง (เช่น 240 fps) ไม่ผูกกับรีเฟรชเรตของจอ/จอเสมือน
และไม่ติดหน้าต่างอื่นที่มาบัง  ตั้งใน config.py:  CAPTURE_BACKEND = "wgc"  CAPTURE_WINDOW = "ชื่อเกม.exe หรือชื่อหน้าต่าง"

ใช้ผ่าน make_screen() (คืน WgcScreen ถ้าใช้ได้ ไม่งั้นคืน Screen ปกติ (mss) พร้อม log เหตุผล)
WgcScreen.grab(box) รอ "เฟรมใหม่" ของเกม (≤ ~4 ms ที่ 240 fps) แล้วคืนภาพ BGR ของกรอบ  -> ลูปของบอทวนตามเฟรมเกมเอง
"""
import atexit
import ctypes
import threading
import time
from ctypes import wintypes

import cv2
import numpy as np

import config

from .detection import log
from .screen import Screen

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
dwmapi = ctypes.windll.dwmapi
DWMWA_EXTENDED_FRAME_BOUNDS = 9
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WAIT_FRAME = 0.04            # รอเฟรมใหม่นานสุดเท่านี้ (วินาที) ไม่มา = ใช้ mss ชั่วคราว
STALL_AFTER = 4              # หมดเวลาติดกันกี่ครั้ง = ถือว่าเกมไม่ส่งเฟรม (เช่นย่อหน้าต่าง) ใช้ mss จนกว่าเฟรมจะกลับมา
GEOMETRY_EVERY = 1.0         # ตรวจตำแหน่งหน้าต่างเกมซ้ำทุกกี่วินาที


def _exe_name(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(512)
        n = wintypes.DWORD(512)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return buf.value.replace("\\", "/").split("/")[-1].lower()
    finally:
        kernel32.CloseHandle(h)
    return ""


def window_bounds(hwnd):
    """พื้นที่หน้าต่างที่มองเห็นจริง (left, top, right, bottom) บนจอ"""
    r = wintypes.RECT()
    if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)) != 0:
        user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def find_game_window(name):
    """หาหน้าต่างเกมที่ชื่อหน้าต่างหรือชื่อไฟล์ .exe มีคำว่า name (ไม่สนตัวพิมพ์) เลือกอันที่ใหญ่สุด คืน hwnd หรือ None"""
    name = name.lower()
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if name in buf.value.lower() or name in _exe_name(hwnd):
            l, t, r, b = window_bounds(hwnd)
            if r - l > 200 and b - t > 200:
                found.append(((r - l) * (b - t), hwnd))
        return True

    user32.EnumWindows(cb, 0)
    return max(found)[1] if found else None


class WgcScreen(Screen):
    """Screen ที่อ่านภาพจากหน้าต่างเกม (WGC)  ส่วนที่เหลือ (พิกัด/กรอบ/คลิก) ใช้ตัว Screen เดิมทั้งหมด"""

    backend = "wgc"

    def __init__(self, window_name, **kw):
        super().__init__(**kw)
        from windows_capture import WindowsCapture

        self.window_name = window_name
        self.hwnd = find_game_window(window_name)
        if not self.hwnd:
            raise RuntimeError(f"ไม่พบหน้าต่างเกม '{window_name}' (เปิดเกมก่อน / ไม่ย่อหน้าต่าง)")
        self.origin = window_bounds(self.hwnd)[:2]
        self._cond = threading.Condition()
        self._seq, self._latest, self._want = 0, None, None
        self._last_returned, self._timeouts, self._stalled = 0, 0, False
        self._stall_seq, self._geo_t = 0, 0.0
        self.frames, self.fallbacks = 0, 0
        self.debug_stamps = None                    # ตั้งเป็น list เพื่อเก็บเวลาที่เฟรมมาถึงจริง (ไว้วัดเฟรมเรตเกม)

        cap = WindowsCapture(cursor_capture=False, draw_border=False, minimum_update_interval=0, window_hwnd=self.hwnd)

        @cap.event
        def on_frame_arrived(frame, control):
            want = self._want
            arr = None
            if want is not None:                                   # คัดลอกเฉพาะกรอบที่ขอ (เล็ก) ภาพต้นฉบับใช้ได้แค่ใน callback นี้
                fb = frame.frame_buffer
                h, w = fb.shape[:2]
                if want == "full":
                    arr = cv2.cvtColor(np.array(fb), cv2.COLOR_BGRA2BGR)
                else:
                    x1, y1, x2, y2 = want
                    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
                    if x2 > x1 and y2 > y1:
                        arr = cv2.cvtColor(np.array(fb[y1:y2, x1:x2]), cv2.COLOR_BGRA2BGR)
            if self.debug_stamps is not None:
                self.debug_stamps.append(time.perf_counter())
            with self._cond:
                self._seq += 1
                self._latest = (self._seq, want, arr)
                self._cond.notify_all()

        @cap.event
        def on_closed():
            pass

        self._ctl = cap.start_free_threaded()
        atexit.register(self.close)

    def close(self):
        ctl, self._ctl = getattr(self, "_ctl", None), None
        if ctl is not None:
            try:
                ctl.stop()
            except Exception:
                pass

    def _refresh_geometry(self):
        now = time.perf_counter()
        if now - self._geo_t >= GEOMETRY_EVERY:
            self._geo_t = now
            if user32.IsWindow(self.hwnd):
                self.origin = window_bounds(self.hwnd)[:2]

    def grab(self, box=None):
        """รอเฟรมใหม่ของเกมแล้วคืนภาพ BGR ของกรอบ (พิกัดจอ) ไม่มาในเวลา -> ใช้ mss แทนชั่วคราว"""
        self._refresh_geometry()
        if self._ctl is None:
            return super().grab(box)
        if box is None:
            want = "full"
        else:
            ox, oy = self.origin
            want = (box[0] - ox, box[1] - oy, box[2] - ox, box[3] - oy)
        if self._stalled:
            if self._seq != self._stall_seq:
                self._stalled = False                              # เฟรมกลับมาแล้ว
            else:
                self.fallbacks += 1
                return super().grab(box)
        self._want = want
        deadline = time.perf_counter() + WAIT_FRAME
        with self._cond:
            while True:
                lat = self._latest
                if lat is not None and lat[1] == want and lat[0] != self._last_returned and lat[2] is not None:
                    break
                left = deadline - time.perf_counter()
                if left <= 0:
                    break
                self._cond.wait(left)
            lat = self._latest
        if lat is not None and lat[1] == want and lat[2] is not None and lat[0] != self._last_returned:
            self._last_returned = lat[0]
            self._timeouts = 0
            self.frames += 1
            return lat[2]
        self._timeouts += 1
        self.fallbacks += 1
        if self._timeouts >= STALL_AFTER:
            self._stalled, self._stall_seq = True, self._seq
            log.info("[wgc] ไม่ได้รับเฟรมจากเกม -> ใช้ mss ชั่วคราว (ย่อหน้าต่างอยู่ไหม?)")
        return super().grab(box)


def make_screen():
    """สร้างตัวจับภาพตาม config.CAPTURE_BACKEND: "wgc" (ตามหน้าต่างเกม) หรือ "mss" (จับจอ)  wgc ใช้ไม่ได้ -> mss พร้อมบอกเหตุผล"""
    backend = str(getattr(config, "CAPTURE_BACKEND", "mss")).lower()
    if backend == "wgc":
        try:
            s = WgcScreen(config.CAPTURE_WINDOW)
            l, t, r, b = window_bounds(s.hwnd)
            probe = s.grab((l, t, l + 16, t + 16))                  # ทดสอบว่าเฟรมมาจริง
            if s.frames == 0 or probe is None:
                s.close()
                raise RuntimeError("เปิด WGC ได้แต่ไม่ได้รับเฟรมจากเกม")
            log.info(f"[capture] ใช้ WGC จับหน้าต่างเกม '{config.CAPTURE_WINDOW}' (hwnd {s.hwnd}, {r - l}x{b - t} @({l},{t})) "
                     f"-> เฟรมตามเกม ไม่ผูกกับรีเฟรชเรตจอ")
            return s
        except Exception as e:                                       # noqa: BLE001
            log.info(f"[capture] ใช้ WGC ไม่ได้ ({type(e).__name__}: {e}) -> ใช้ mss (จับจอ) แทน")
    else:
        log.info("[capture] ใช้ mss (จับจอ)")
    return Screen()
