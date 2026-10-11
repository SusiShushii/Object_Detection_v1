"""
click_at.py - ทดสอบ "กดที่เวลา X ms หลังหน้า Mission Reward ขึ้น" แล้วดูว่ากดเข้าไหม (ไว้หาว่ากดเร็วสุดได้เท่าไหร่)

    python click_at.py 3997                    กดที่ 3997 ms ช่องแรกที่มีไอเท็ม
    python click_at.py 3950 blue               กดที่ 3950 ms ช่องของไอเท็ม blue (ชื่อ/โปรไฟล์เหมือน main.py)
    python click_at.py 3900 --slot 1,3         กดช่อง แถว1 คอลัมน์3
    python click_at.py 3997 --dry              ไม่คลิกจริง (ดูเวลา/การเลือกช่องเฉยๆ)

  เวลานับจากเฟรมแรกที่เห็น Mission Reward (เหมือน timing.py: ปกติสีเปลี่ยนที่ ~3997 ms)
  ใส่น้อยกว่านั้น = กดก่อนสีเปลี่ยน (ทดสอบว่าเกมรับคลิกก่อนไหม)  รันวนทุกรอบจนกด F12
  ผล: บอกว่ากดจริงตอนกี่ ms และ "กดเข้า / ไม่เข้า" (ช่องว่างหรือทึบแว้บหลังกด)  เขียน timing.log
"""
import ctypes
import os
import random
import sys
import time

import cv2

import click
from click import (BG_READY_V, HEADER_MATCH, MOUSE_FLAGS, STOP_KEY, _detect_all, _header_score, _hover_point,
                   _is_empty_slot, _send_mouse, _slot_bg_v, hover)
from object_detection import get_scale
from profiles import resolve
from screen import Screen
from timing import LOG, OCC_EVERY, out, slots

VERIFY_WINDOW = 1.0         # หลังกด ดูผลกี่วินาที
LOST_CHECKS = 3


def stopped():
    return bool(ctypes.windll.user32.GetAsyncKeyState(STOP_KEY) & 0x8000)


def wait_until(t_target):
    """รอแบบแม่นระดับ sub-ms: sleep หยาบ แล้ว spin ช่วงท้าย"""
    while True:
        left = t_target - time.perf_counter()
        if left <= 0:
            return
        time.sleep(0 if left < 0.002 else left / 2)


def main():
    args = sys.argv[1:]
    if not args or not args[0].replace(".", "", 1).isdigit():
        print(__doc__)
        return
    at_ms = float(args[0])
    rest = args[1:]
    dry = "--dry" in rest
    slot_arg = None
    if "--slot" in rest:
        i = rest.index("--slot")
        r, c = rest[i + 1].split(",")
        slot_arg = (int(r) - 1, int(c) - 1)
        del rest[i:i + 2]
    rest = [a for a in rest if not a.startswith("--")]
    names = None
    if rest:
        res = resolve(rest)
        for e in res["errors"]:
            print(e)
        if res["missing"] or not res["names"]:
            print("ไม่พบไอเท็ม:", res["missing"])
            return
        names = res["names"]

    screen = Screen()
    scale = get_scale(screen)
    box = screen.get_region("mission_reward")
    slot_rects = dict(slots(scale))
    out(f"[click_at] กดที่ {at_ms:.0f}ms หลัง Mission Reward ขึ้น  "
        f"{'ไอเท็ม ' + ','.join(names) if names else ('ช่อง แถว%d คอลัมน์%d' % (slot_arg[0] + 1, slot_arg[1] + 1) if slot_arg else 'ช่องแรกที่มีไอเท็ม')}"
        f"{'  [dry]' if dry else ''}  หยุด: F12")

    visible = False
    while not stopped():
        img = screen.grab(box)
        now = time.perf_counter()
        score = _header_score(img, scale)
        if not visible:
            if score < HEADER_MATCH:
                time.sleep(0.003)
                continue
            visible, t0, miss, chosen, target_t, clicked, done = True, now, 0, None, None, None, False
            hover_xy, trace, t_occ, v_hist = None, [], 0.0, []
            out(f"=== Mission Reward ขึ้น (0ms)")
        elif score < HEADER_MATCH:
            miss += 1
            if miss >= LOST_CHECKS:
                out("Mission Reward หายไป" + ("" if clicked else " (ยังไม่ได้กด)"))
                visible = False
            continue
        else:
            miss = 0
        ms = (now - t0) * 1000

        # เลือกช่อง (ครั้งเดียว) เมื่อไอเท็มขึ้นแล้ว แล้ววางเมาส์รอ
        if chosen is None and not done and ms >= 500 and now - t_occ >= OCC_EVERY:
            t_occ = now
            if names:
                found = _detect_all(img, names, scale)
                if found:
                    chosen = max(found, key=lambda t: t[1][4])[1]
            elif slot_arg is not None:
                chosen = slot_rects.get(slot_arg)
            else:
                chosen = next((sl for k, sl in slots(scale) if not _is_empty_slot(img, *sl[:4], scale)), None)
            if chosen is not None:
                hx, hy = _hover_point(chosen, box)
                hover_xy = (hx, hy)
                if not dry:
                    hover(hx, hy)
                out(f"  {ms:7.1f}ms เลือกช่อง x={chosen[0]} y={chosen[1]}  เมาส์รอที่ ({hx}, {hy})  "
                    f"V={_slot_bg_v(img, chosen):.0f}")
                target_t = t0 + at_ms / 1000

        # ถึงเวลากด: spin-wait แล้วคลิก (ถ้าเวลายังไม่ถึงกด จะรอในลูปนี้เลย ไม่จับภาพเพิ่ม)
        if chosen is not None and clicked is None and target_t is not None and (target_t - now) < 0.05:
            wait_until(target_t)
            t_down = time.perf_counter()
            if not dry:
                _send_mouse(MOUSE_FLAGS["left"][0])
                time.sleep(random.uniform(0.03, 0.05))
                _send_mouse(MOUSE_FLAGS["left"][1])
            v_at = _slot_bg_v(img, chosen)
            clicked, was_ready, ever_ready = t_down, v_at >= BG_READY_V, v_at >= BG_READY_V
            out(f"  กดจริงที่ {(t_down - t0) * 1000:.1f}ms (ตั้งไว้ {at_ms:.0f}ms)  "
                f"ภาพล่าสุดก่อนกด V={v_at:.0f} ({'ปกติ' if v_at >= BG_READY_V else 'ทึบ'}){'  [dry]' if dry else ''}")
            v_hist = []
            continue

        # ดูผลหลังกด
        if clicked is not None:
            since = now - clicked
            v = _slot_bg_v(img, chosen)
            empty = _is_empty_slot(img, *chosen[:4], scale)
            v_hist.append((round(since * 1000), round(v)))
            # เก็บได้ = ช่องว่าง หรือ ทึบแว้บหลังที่เคยเห็นปกติ (ทึบอยู่ตลอดตั้งแต่ก่อนกด = ยังไม่ใช่ผลของคลิก)
            ever_ready = ever_ready or v >= BG_READY_V
            if empty or (ever_ready and v < BG_READY_V and since > 0.004):
                out(f"  ผล: กดเข้า ✓  ({'ช่องว่าง' if empty else f'ทึบแว้บ V={v:.0f}'} หลังกด {since * 1000:.0f}ms)"
                    f"{'' if was_ready else '  (ภาพล่าสุดก่อนกดยังทึบ: ถ้ากดเร็วกว่าสีเปลี่ยนเกิน 1 เฟรม แปลว่าเกมรับคลิกก่อนสีเปลี่ยน)'}")
                done, clicked, chosen, target_t = True, None, None, None    # กดรอบละครั้ง
            elif since > VERIFY_WINDOW:
                out(f"  ผล: ไม่เข้า ✗  (หลังกด {VERIFY_WINDOW:.1f}s ไอเท็มยังอยู่  V ล่าสุด {v_hist[-6:]})")
                done, clicked, chosen, target_t = True, None, None, None
    out("[click_at] จบ")


if __name__ == "__main__":
    main()
