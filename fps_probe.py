"""fps_probe.py - วัดรอบอัปเดตภาพของหน้าจอเครื่องนี้ (ไม่คลิก ไม่ขยับเมาส์)
  - DwmFlush: ช่วงเวลาระหว่างการรวมภาพ desktop ของ Windows (DWM) = เพดานที่บอทเห็นภาพใหม่
  - จอ/โหมดที่ Windows ใช้อยู่ (ความละเอียด + Hz)
  - ความเร็วจับภาพของ mss
ผลเขียนที่ fps_probe.txt"""
import ctypes
import ctypes.wintypes as wt
import statistics
import time

lines = []


def p(s):
    print(s, flush=True)
    lines.append(s)


class DEVMODE(ctypes.Structure):
    _fields_ = [("a", ctypes.c_wchar * 32), ("b", wt.WORD), ("c", wt.WORD), ("d", wt.WORD), ("e", wt.WORD),
                ("f", wt.DWORD), ("pos", ctypes.c_long * 2), ("g", wt.DWORD), ("h", wt.DWORD),
                ("i", ctypes.c_wchar * 32), ("j", wt.WORD), ("bits", wt.DWORD), ("w", wt.DWORD), ("h2", wt.DWORD),
                ("k", wt.DWORD), ("hz", wt.DWORD)] + [("pad", ctypes.c_byte * 64)]


try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass
dm = DEVMODE()
dm.f = ctypes.sizeof(DEVMODE) - 64
ok = ctypes.windll.user32.EnumDisplaySettingsW(None, -1, ctypes.byref(dm))
p(f"จอหลักที่ Windows ใช้: {dm.w}x{dm.h2} @ {dm.hz} Hz" if ok else "อ่านโหมดจอไม่ได้")

dwm = ctypes.windll.dwmapi
n = 0
gaps = []
t_end = time.perf_counter() + 3
last = time.perf_counter()
while time.perf_counter() < t_end:
    if dwm.DwmFlush() != 0:
        p("DwmFlush ใช้ไม่ได้ (ไม่มี session จอ?)")
        break
    now = time.perf_counter()
    gaps.append((now - last) * 1000)
    last = now
gaps = gaps[1:]
if gaps:
    p(f"DWM รวมภาพ {len(gaps) / 3:.0f} ครั้ง/วินาที  ช่วงเฉลี่ย {statistics.mean(gaps):.2f}ms "
      f"(ต่ำสุด {min(gaps):.2f} สูงสุด {max(gaps):.2f})  => เพดานภาพใหม่ ~{1000 / statistics.mean(gaps):.0f} fps")

try:
    import mss
    with mss.mss() as s:
        mon = {"left": 516, "top": 512, "width": 404, "height": 201}
        t0 = time.perf_counter()
        k = 0
        while time.perf_counter() - t0 < 2:
            s.grab(mon)
            k += 1
        p(f"mss จับกรอบเล็กได้ {k / 2:.0f} ครั้ง/วินาที ({2000 / k:.1f}ms ต่อภาพ)")
except Exception as e:
    p(f"mss error: {e}")
open("fps_probe.txt", "w", encoding="utf-8").write("\n".join(lines) + "\n")
