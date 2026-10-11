"""
timing.py - จับเวลาอย่างเดียว (ไม่คลิก ไม่ขยับเมาส์): หน้า Mission Reward ขึ้นเมื่อไหร่ / ไอเท็มแต่ละช่องขึ้นเมื่อไหร่ /
แต่ละช่องเปลี่ยนจากทึบเป็นสีปกติเมื่อไหร่ (นับ ms จากเฟรมแรกที่เห็น Mission Reward)  ผลเขียนที่ timing.log

    python timing.py            (หยุดด้วย Ctrl+C หรือ F12)
"""
import ctypes
import logging
import os
import time

import click
from click import (BG_READY_V, GRID_ORIGIN, GRID_PITCH, GRID_SIZE, HEADER_MATCH, SLOT_WH, STOP_KEY, _header_score,
                   _is_empty_slot, _slot_bg_v)
from object_detection import BASE_DIR, get_scale
from screen import Screen

logging.getLogger("detection").handlers = []
LOG = os.path.join(BASE_DIR, "timing.log")


def out(msg):
    line = f"{time.strftime('%H:%M:%S')}  {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def slots(scale):
    (ox, oy), (px, py), (nc, nr), (sw, sh) = GRID_ORIGIN, GRID_PITCH, GRID_SIZE, SLOT_WH
    for r in range(nr):
        for c in range(nc):
            yield (r, c), (int(round(5 + (ox + px * c) * scale)), int(round(5 + (oy + py * r) * scale)),
                           int(sw * scale), int(sh * scale), 0)


def main():
    screen = Screen()
    scale = get_scale(screen)
    box = screen.get_region("mission_reward")
    out(f"เริ่มจับเวลา (ไม่คลิก)  scale={scale:.3f}  หยุด: F12")
    visible = False
    while not ctypes.windll.user32.GetAsyncKeyState(STOP_KEY) & 0x8000:
        t_frame = time.perf_counter()
        img = screen.grab(box)
        t_grab = (time.perf_counter() - t_frame) * 1000
        if _header_score(img, scale) < HEADER_MATCH:
            if visible:
                visible = False
                out(f"Mission Reward หายไป ({(time.perf_counter() - t0) * 1000:.0f}ms) | เฟรมเฉลี่ย "
                    f"{sum(gaps) / max(len(gaps), 1):.1f}ms (จับภาพ {sum(grabs) / max(len(grabs), 1):.1f}ms) "
                    f"สูงสุด {max(gaps, default=0):.0f}ms")
            time.sleep(0.01)
            continue
        now = time.perf_counter()
        if not visible:
            visible, t0, last, first_seen, normal_at, gaps, grabs = True, now, now, {}, {}, [], []
            out("=== Mission Reward ขึ้น  (0ms)")
        gaps.append((now - last) * 1000)
        grabs.append(t_grab)
        last = now
        ms = (now - t0) * 1000
        for key, sl in slots(scale):
            if _is_empty_slot(img, *sl[:4], scale):
                continue
            v = _slot_bg_v(img, sl)
            if key not in first_seen:
                first_seen[key] = ms
                out(f"  {ms:6.0f}ms  ช่อง แถว{key[0] + 1} คอลัมน์{key[1] + 1} ขึ้น  (พื้นหลัง V={v:.0f} "
                    f"{'ทึบ' if v < BG_READY_V else 'ปกติ'})")
            elif key not in normal_at and v >= BG_READY_V and first_seen[key] < ms:
                normal_at[key] = ms
                out(f"  {ms:6.0f}ms  ช่อง แถว{key[0] + 1} คอลัมน์{key[1] + 1} เปลี่ยนเป็นสีปกติ  (V={v:.0f})  "
                    f"= {ms - first_seen[key]:.0f}ms หลังช่องขึ้น")
        time.sleep(0.002)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
