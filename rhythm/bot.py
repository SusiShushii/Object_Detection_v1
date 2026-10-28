"""
rhythm/bot.py - เกมเต้น (Dance Audition) กดตามจังหวะ 3 ปุ่ม A S D

    python main.py rhythm calibrate     จับภาพ 1 เฟรม วาดเส้น/ตำแหน่งโน้ตที่เจอ -> output/rhythm/calibrate.png (เปิดดูว่าตรงไหม)
    python main.py rhythm               เริ่มกดตามจังหวะ (เปิดเกมไว้หน้าต่างหลัก กด F12 ค้างหยุด)
    python main.py rhythm --dry         ไม่กดปุ่มจริง แค่ log ว่าจะกดอะไรเมื่อไหร่ (ไว้จูน)

หลักการ: โน้ตสีส้ม/ม่วง/เขียววิ่งจากขวาไปซ้ายในแถว A/S/D เข้าหาวงกลมจาง ๆ ทางซ้าย
บอทหาจุดสีสด ๆ ในแต่ละแถว ตามความเร็วโน้ต แล้วกดปุ่มเมื่อโน้ตใกล้ถึงวงกลมเป้าหมาย (ชดเชยหน่วงด้วย PRESS_LEAD_MS)
ตำแหน่งต่าง ๆ วัดจากภาพตัวอย่าง 1792x1021 (สเกลตามขนาดเกม) ถ้าไม่ตรงให้แก้ค่าด้านล่างแล้วใช้ calibrate ดู
"""
import ctypes
import logging
import os
import sys
import time

import cv2
import numpy as np

from detector.clicker import esc_pressed, tune_realtime
from detector.wgc import make_screen

# ---- ค่าที่ปรับได้ (พิกัดวัดจากภาพ 1792x1021) -------------------------------
REF_W, REF_H = 1792, 1021
LANES = [("A", 0x1E, 820), ("S", 0x1F, 866), ("D", 0x20, 915)]   # (ชื่อ, scancode, y กลางแถว)
LANE_HALF_H = 17            # ครึ่งความสูงแถวที่สแกน
SCAN_X = (500, 1490)        # ช่วง x ที่สแกนหาโน้ต (เลยวงกลมเป้าหมายนิดหน่อยถึงขอบขวา)
HIT_X = 547                 # x กลางวงกลมเป้าหมาย
MIN_SAT, MIN_VAL = 100, 130 # โน้ตสีสด (วงกลมเป้าหมาย/พื้นแถวสีจางกว่านี้)
MIN_AREA = 250              # พื้นที่จุดสีขั้นต่ำ (px ที่สเกล 1792x1021)
PRESS_LEAD_MS = 25          # กดก่อนถึงเป้าหมายกี่ ms (ชดเชยหน่วงจับภาพ/ส่งปุ่ม/เกม)  กดช้าไป = เพิ่ม  เร็วไป = ลด
DEFAULT_LEAD_PX = 12        # ยังวัดความเร็วไม่ได้ (เห็นโน้ตแค่เฟรมเดียว) ใช้ระยะนี้แทน
KEY_HOLD_MS = 30            # กดค้างกี่ ms
MISS_PX = 70                # โน้ตเลยเป้าหมายไปแล้วเกินนี้ = พลาด ไม่กดย้อนหลัง
# ---------------------------------------------------------------------------

OUT = os.path.join("output", "rhythm")
log = logging.getLogger("rhythm")
user32 = ctypes.windll.user32


class _KI(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", _KI), ("pad", ctypes.c_byte * 32)]
    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_ulong), ("u", _U)]


def send_key(scan, up=False):
    """ส่งปุ่มแบบ scancode (เกมส่วนใหญ่รับแบบนี้)"""
    flags = 0x0008 | (0x0002 if up else 0)                    # KEYEVENTF_SCANCODE | KEYUP
    i = _INPUT(type=1, ki=_KI(0, scan, flags, 0, None))
    user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(i))


def geometry(screen):
    """(box, sx, sy, y1): พื้นที่สแกนบนจอ + อัตราส่วนภาพอ้างอิง -> จอจริง"""
    gx, gy, gw, gh = screen.get_game_rect()
    sx, sy = gw / REF_W, gh / REF_H
    y1 = LANES[0][2] - LANE_HALF_H
    y2 = LANES[-1][2] + LANE_HALF_H
    box = (int(gx + SCAN_X[0] * sx), int(gy + y1 * sy), int(gx + SCAN_X[1] * sx), int(gy + y2 * sy))
    return box, sx, sy, y1


def find_notes(img, sx, sy, y1):
    """คืน (dict ชื่อแถว -> [x กลางโน้ตในพิกัดอ้างอิง เรียงซ้าย->ขวา], mask ไว้ debug)"""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    hue = hsv[..., 0]
    not_red = (hue > 5) & (hue < 160)                          # กากะบาดสีแดง/ชมพู (ตอนตาย) ไม่ใช่โน้ต  โน้ต = ส้ม/เขียว/ม่วง
    mask = ((hsv[..., 1] >= MIN_SAT) & (hsv[..., 2] >= MIN_VAL) & not_red).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    found = {n: [] for n, _, _ in LANES}
    for name, _, ly in LANES:
        top = max(0, int((ly - LANE_HALF_H - y1) * sy))
        bot = int((ly + LANE_HALF_H - y1) * sy)
        n, _, stats, cent = cv2.connectedComponentsWithStats(mask[top:bot], connectivity=8)
        for k in range(1, n):
            if stats[k, cv2.CC_STAT_AREA] >= MIN_AREA * sx * sy:
                found[name].append(SCAN_X[0] + cent[k][0] / sx)
        found[name].sort()
        merged = []                                            # โน้ตตัวเดียวอาจแตกเป็นหลายชิ้น (ตา/ปาก) -> รวมชิ้นที่ห่างกันไม่ถึง 30px
        for x in found[name]:
            if merged and x - merged[-1] < 30:
                merged[-1] = (merged[-1] + x) / 2
            else:
                merged.append(x)
        found[name] = merged
    return found, mask


class Track:
    __slots__ = ("x0", "t0", "x", "t", "pressed", "n")

    def __init__(self, x, t):
        self.x0, self.t0, self.x, self.t, self.pressed, self.n = x, t, x, t, False, 1

    def speed(self):
        """ความเร็ว px/s จากตำแหน่งแรกที่เห็นถึงตอนนี้ (ระยะยาว = นิ่งกว่าเทียบเฟรมต่อเฟรม) ยังไม่พอ = None"""
        dt = self.t - self.t0
        return (self.x0 - self.x) / dt if dt >= 0.2 and self.x0 - self.x >= 40 else None


def update_tracks(tracks, xs, now):
    """จับคู่โน้ตที่เห็นรอบนี้กับ track เดิม (โน้ตวิ่งไปทางซ้ายเท่านั้น) โน้ตที่หายไปชั่วคราว (โดนกากะบาด/วงเป้าหมายบัง) เก็บ track ไว้ 0.4 วินาที"""
    used = set()
    for x in xs:
        best = None
        for tr in tracks:
            v = tr.speed()
            ref = tr.x - v * (now - tr.t) if v else tr.x          # รู้ความเร็วแล้ว = เทียบกับตำแหน่งที่คาดไว้ (โน้ตเร็ว/โดนบังหลายเฟรมก็ยังจับคู่ได้)
            ok = abs(ref - x) <= 60 if v else -8 <= tr.x - x <= 160
            if id(tr) not in used and ok and (best is None or abs(tr.x - x) < abs(best.x - x)):
                best = tr
        if best is None:
            best = Track(x, now)
            if x < HIT_X + 40:
                best.pressed = True                            # โผล่มาที่เป้าหมายเลย = เศษภาพ ไม่ใช่โน้ตที่กำลังเข้ามา
            tracks.append(best)
        else:
            best.x, best.t, best.n = x, now, best.n + 1
        used.add(id(best))
    tracks[:] = [t for t in tracks if now - t.t < 0.4]


def setup_log():
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, time.strftime("rhythm_%Y%m%d_%H%M%S.log"))
    fmt = logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S")
    for h in (logging.FileHandler(path, encoding="utf-8"), logging.StreamHandler(sys.stdout)):
        h.setFormatter(fmt)
        log.addHandler(h)
    log.setLevel(logging.INFO)
    return path


def calibrate(screen):
    box, sx, sy, y1 = geometry(screen)
    img = screen.grab(box)
    found, mask = find_notes(img, sx, sy, y1)
    vis = img.copy()
    hx = int((HIT_X - SCAN_X[0]) * sx)
    cv2.line(vis, (hx, 0), (hx, vis.shape[0]), (0, 0, 255), 2)
    for name, _, ly in LANES:
        for x in found[name]:
            cv2.circle(vis, (int((x - SCAN_X[0]) * sx), int((ly - y1) * sy)), 14, (255, 255, 255), 2)
        cv2.putText(vis, name, (4, int((ly - y1) * sy) + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)
    os.makedirs(OUT, exist_ok=True)
    cv2.imwrite(os.path.join(OUT, "calibrate.png"), vis)
    cv2.imwrite(os.path.join(OUT, "calibrate_mask.png"), mask)
    cv2.imwrite(os.path.join(OUT, "calibrate_raw.png"), img)
    print(f"สแกนกรอบ {box}  ({img.shape[1]}x{img.shape[0]})  จับภาพ={screen.backend}")
    for name, _, _ in LANES:
        print(f"  แถว {name}: โน้ตที่เจอ x = {[round(x) for x in found[name]]}   (เป้าหมาย x = {HIT_X})")
    print(f"บันทึกไว้ที่ {OUT}: calibrate.png (เส้นแดง = เป้าหมาย วงขาว = โน้ตที่เจอ) / calibrate_mask.png / calibrate_raw.png")


def run(dry):
    path = setup_log()
    screen = make_screen()
    tune_realtime()
    box, sx, sy, y1 = geometry(screen)
    log.info(f"[rhythm] เริ่ม  จับภาพ={screen.backend}  กรอบ={box}  กดก่อนเป้าหมาย {PRESS_LEAD_MS}ms  กดค้าง {KEY_HOLD_MS}ms"
             + ("  [dry]" if dry else "") + "   กด F12 ค้างเพื่อหยุด")
    tracks = {n: [] for n, _, _ in LANES}
    release = []                                              # (เวลาปล่อย, scancode)
    presses, t_start, g_speed = 0, time.perf_counter(), None
    try:
        while True:
            if esc_pressed():
                break
            img = screen.grab(box)
            now = time.perf_counter()
            for _, scan in [r for r in release if r[0] <= now]:
                send_key(scan, up=True)
            release = [r for r in release if r[0] > now]
            found, _ = find_notes(img, sx, sy, y1)
            for name, scan, _ in LANES:
                update_tracks(tracks[name], found[name], now)
                for tr in tracks[name]:
                    v = tr.speed()
                    if v is not None:
                        g_speed = v if g_speed is None else 0.7 * g_speed + 0.3 * v        # ความเร็วโน้ตเท่ากันทั้งเพลง (ด่านยากเร็วขึ้น) ใช้ค่าล่าสุดช่วยตัวที่เพิ่งโผล่
                for tr in tracks[name]:
                    if tr.pressed:
                        continue
                    v = tr.speed() or g_speed
                    pred = tr.x - (v * (now - tr.t) if v else 0)      # คาดตำแหน่งตอนนี้ (โน้ตอาจโดนบังหลายเฟรม)
                    dist = pred - HIT_X
                    lead = min(v * PRESS_LEAD_MS / 1000, 120) if v else DEFAULT_LEAD_PX
                    if dist < -MISS_PX:
                        tr.pressed = True                      # เลยไปไกลแล้ว ไม่กดย้อน
                        log.info(f"{now - t_start:8.3f}s  แถว {name}  พลาด (โน้ตเลยเป้าหมายไปแล้ว x={pred:.0f})")
                    elif dist <= lead:
                        tr.pressed = True
                        if not dry:
                            send_key(scan)
                            release.append((now + KEY_HOLD_MS / 1000, scan))
                        presses += 1
                        vs = f"{v:.0f}px/s" if v else "?"
                        log.info(f"{now - t_start:8.3f}s  กด {name}  x={pred:.0f} ห่างเป้า {dist:.0f}px  v={vs}  (เห็นมา {tr.n} เฟรม)")
    except KeyboardInterrupt:
        pass
    finally:
        for _, scan in release:
            send_key(scan, up=True)
        log.info(f"[rhythm] หยุด  กดไป {presses} ครั้ง  log: {path}")
        screen.close()


def main():
    args = sys.argv[1:]
    if "--help" in args or "-h" in args:
        print(__doc__)
    elif "calibrate" in args:
        screen = make_screen()
        try:
            calibrate(screen)
        finally:
            screen.close()
    else:
        run("--dry" in args)


if __name__ == "__main__":
    main()
