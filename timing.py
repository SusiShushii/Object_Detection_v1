"""
timing.py - จับภาพเร็วที่สุดเท่าที่ทำได้ แล้วบอกว่าหน้า Mission Reward ขึ้นกี่ ms / แต่ละช่องเปลี่ยนจากทึบเป็นสีปกติกี่ ms
(ไม่คลิก ไม่ขยับเมาส์)

    python timing.py                 จอ monitor สด + บันทึกวิดีโอทุกรอบ + log   (หยุด: F12 หรือ Ctrl+C)
    python timing.py --no-window     ไม่เปิดหน้าต่าง monitor
    python timing.py --no-video      ไม่บันทึกวิดีโอ
    python timing.py --image loot\\xxx.png ...        ทดสอบกับรูป: วาดกรอบลง timing_out\\ (ไม่จับจอ)
    python timing.py --selftest dim.png normal.png    ทดสอบทั้งระบบด้วยภาพปลอม: ทึบ -> สีปกติ (ไม่จับจอ)

กรอบในวิดีโอ/หน้าต่าง:  แดง = ทึบ (ยังกดไม่ได้)   เขียว = สีปกติ (กดได้)   เหลือง = เพิ่งเปลี่ยนเป็นสีปกติ (10 เฟรมแรก)
  ข้อความมุมบน: รอบที่ / เฟรมที่ / เวลา ms นับจากเฟรมแรกที่เห็น Mission Reward / fps ตอนนี้
ผล: timing.log (สรุป ms + เลขเฟรม)  timing_out\\<เวลา>_roundN\\ (วิดีโอ .mp4 + รูปตอนขึ้น/ตอนเปลี่ยนสี)
"""
import ctypes
import logging
import os
import queue
import sys
import threading
import time

import cv2
import numpy as np

from click import (BG_READY_V, GRID_ORIGIN, GRID_PITCH, GRID_SIZE, HEADER_MATCH, SLOT_WH, STOP_KEY, _header_score,
                   _is_empty_slot, _slot_bg_v)
from object_detection import BASE_DIR

logging.getLogger("detection").handlers = []
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
LOG = os.path.join(BASE_DIR, "timing.log")
OUT = os.path.join(BASE_DIR, "timing_out")
VIDEO_FPS = 120            # fps ของวิดีโอ (เวลาจริงถูกต้อง: ช่วงจับภาพไม่ทันจะซ้ำเฟรม เฟรมเกินจะถูกข้าม)
ZOOM = 2                   # ขยายภาพในวิดีโอ/หน้าต่าง
BAR_H = 26
HEADER_EVERY = 3           # เช็กหัวข้อทุก N เฟรม (ตอนรอ = ทุกเฟรม)
OCC_EVERY = 0.25           # เช็กว่าช่องไหนมีไอเท็มทุกกี่วินาที (ช่วงอื่นวัดแค่ความสว่างพื้นหลัง = เร็ว)
LOST_CHECKS = 3
FLASH_FRAMES = 10
RED, GREEN, YELLOW, GREY = (0, 0, 255), (0, 200, 0), (0, 230, 255), (120, 120, 120)


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


def occupancy(img, scale):
    return {k: not _is_empty_slot(img, *sl[:4], scale) for k, sl in slots(scale)}


def draw(img, scale, occ, flash, header, round_no, frame, ms, fps):
    """ภาพ monitor: ขยาย + กรอบทุกช่อง (แดงทึบ/เขียวปกติ/เหลืองเพิ่งเปลี่ยน/เทาว่าง) + ข้อความ"""
    big = cv2.resize(img, None, fx=ZOOM, fy=ZOOM, interpolation=cv2.INTER_NEAREST)
    for key, sl in slots(scale):
        x, y, w, h, _ = sl
        v = _slot_bg_v(img, sl)
        if not occ.get(key):
            col = GREY
        elif frame - flash.get(key, -999) < FLASH_FRAMES:
            col = YELLOW
        else:
            col = GREEN if v >= BG_READY_V else RED
        cv2.rectangle(big, (x * ZOOM, y * ZOOM), ((x + w) * ZOOM, (y + h) * ZOOM), col, 2 if col != GREY else 1)
        if occ.get(key):
            cv2.putText(big, f"{v:.0f}", (x * ZOOM + 3, (y + h) * ZOOM - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.4, col, 1)
    bar = np.zeros((BAR_H, big.shape[1], 3), np.uint8)
    cv2.putText(bar, f"round {round_no}  frame {frame}  {ms:7.1f}ms  {fps:5.0f}fps  header={header:.2f}",
                (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    return np.vstack([bar, big])


class Recorder(threading.Thread):
    """วาดกรอบ + แสดงหน้าต่าง + เขียนวิดีโอ/รูป ใน thread แยก (ไม่ถ่วงการจับภาพ)"""

    def __init__(self, show):
        super().__init__(daemon=True)
        self.q, self.show = queue.Queue(), show
        self.writer, self.dir, self.written = None, None, 0
        self.start()

    def _open(self, path_dir, shape):
        h, w = shape[0] * ZOOM + BAR_H, shape[1] * ZOOM
        for name, cc in (("round.mp4", "mp4v"), ("round.avi", "MJPG")):
            wr = cv2.VideoWriter(os.path.join(path_dir, name), cv2.VideoWriter_fourcc(*cc), VIDEO_FPS, (w, h))
            if wr.isOpened():
                return wr
        return None

    def run(self):
        if self.show:
            cv2.namedWindow("timing monitor", cv2.WINDOW_AUTOSIZE)
            cv2.moveWindow("timing monitor", 0, 0)
        while True:
            try:
                it = self.q.get(timeout=0.03)
            except queue.Empty:
                if self.show:
                    cv2.waitKey(1)              # ให้หน้าต่างตอบสนองแม้ไม่มีภาพเข้า
                continue
            if it is None:
                break
            if it[0] == "idle":                 # ยังไม่เห็น Mission Reward: โชว์ภาพสดที่จับได้ + สถานะรอ
                if self.show:
                    _, img, header = it
                    big = cv2.resize(img, None, fx=ZOOM, fy=ZOOM, interpolation=cv2.INTER_NEAREST)
                    bar = np.zeros((BAR_H, big.shape[1], 3), np.uint8)
                    cv2.putText(bar, f"waiting for Mission Reward...  header={header:.2f}", (6, 18),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1)
                    cv2.imshow("timing monitor", np.vstack([bar, big]))
                    cv2.waitKey(1)
                continue
            if it[0] == "start":
                _, self.dir, shape, video = it
                os.makedirs(self.dir, exist_ok=True)
                self.writer = self._open(self.dir, shape) if video else None
                self.written = 0
            elif it[0] == "frame":
                _, img, scale, occ, flash, header, rn, frame, ms, fps, snap = it
                pic = draw(img, scale, occ, flash, header, rn, frame, ms, fps)
                if self.show:
                    cv2.imshow("timing monitor", pic)
                    cv2.waitKey(1)
                if snap and self.dir:
                    cv2.imwrite(os.path.join(self.dir, f"frame{frame:05d}_{snap}.png"), pic)
                if self.writer is not None:
                    target = int(ms / 1000 * VIDEO_FPS) + 1
                    for _ in range(max(target - self.written, 0)):
                        self.writer.write(pic)
                    self.written = max(self.written, target)
            elif it[0] == "end" and self.writer is not None:
                self.writer.release()
                self.writer = None
        if self.writer is not None:
            self.writer.release()


def run(grab, scale, show=True, video=True, stop=lambda: False):
    rec = Recorder(show)
    visible, rounds, n, miss, header, idle_t = False, 0, 0, 0, 0.0, 0.0
    R = {}                                           # สถานะของรอบปัจจุบัน

    def finish(why):
        nonlocal visible
        ms = (time.perf_counter() - R["t0"]) * 1000
        gaps, grabs = R["gaps"], R["grabs"]
        avg = sum(gaps) / max(len(gaps), 1)
        out(f"รอบ {rounds}: {why} ({ms:.0f}ms, {n} เฟรม) | เฟรมเฉลี่ย {avg:.1f}ms = {1000 / max(avg, 0.01):.0f}fps "
            f"(จับภาพ {sum(grabs) / max(len(grabs), 1):.1f}ms) ช้าสุด {max(gaps, default=0):.0f}ms")
        normals = sorted(R["normal_at"].values())
        if normals:
            out(f"รอบ {rounds}: ช่องแรกที่เปลี่ยนเป็นสีปกติ {normals[0][0]:.0f}ms (เฟรม {normals[0][1]}) | ช่องสุดท้าย "
                f"{normals[-1][0]:.0f}ms (เฟรม {normals[-1][1]}) | เปลี่ยนแล้ว {len(normals)}/{len(R['dim_seen'])} ช่องที่เคยทึบ")
        else:
            out(f"รอบ {rounds}: ไม่เห็นช่องไหนเปลี่ยนจากทึบเป็นปกติ (ช่องที่เห็นทึบ {len(R['dim_seen'])}) "
                f"-> ถ้าทุกช่องปกติตั้งแต่เฟรมแรก แปลว่าเปิดสคริปต์หลังของขึ้นครบแล้ว")
        rec.q.put(("end",))
        visible = False

    out(f"เริ่มจับภาพ (ไม่คลิก)  scale={scale:.3f}  แดง=ทึบ เขียว=ปกติ เหลือง=เพิ่งเปลี่ยน  "
        f"วิดีโอ={'เปิด' if video else 'ปิด'}  หยุด: F12")
    while not stop():
        t_a = time.perf_counter()
        img = grab()
        if img is None:
            break
        now = time.perf_counter()
        t_grab = (now - t_a) * 1000
        if not visible or n % HEADER_EVERY == 0:
            header = _header_score(img, scale)
        seen = header >= HEADER_MATCH
        if not visible:
            if not seen:
                if show and now - idle_t >= 0.05 and rec.q.qsize() < 3:
                    idle_t = now
                    rec.q.put(("idle", img.copy(), header))
                time.sleep(0.005)
                continue
            visible, rounds, n, miss = True, rounds + 1, 0, 0
            R = dict(t0=now, last=now, first_seen={}, normal_at={}, dim_seen=set(), flash={}, gaps=[], grabs=[],
                     v_prev={}, occ=occupancy(img, scale), t_occ=now, snaps=0)
            rdir = os.path.join(OUT, f"{time.strftime('%Y%m%d_%H%M%S')}_round{rounds}")
            rec.q.put(("start", rdir, img.shape, video))
            out(f"=== รอบ {rounds}: Mission Reward ขึ้น (เฟรม 1, 0ms)  header={header:.2f}  -> {rdir}")
        elif n % HEADER_EVERY == 0:
            miss = 0 if seen else miss + 1
            if miss >= LOST_CHECKS:
                finish("Mission Reward หายไป")
                continue
        n += 1
        ms = (now - R["t0"]) * 1000
        gap = (now - R["last"]) * 1000
        if n > 1:
            R["gaps"].append(gap)
        R["grabs"].append(t_grab)
        R["last"] = now
        if now - R["t_occ"] >= OCC_EVERY:
            R["occ"], R["t_occ"] = occupancy(img, scale), now
        snap = "start" if n == 1 else ""
        for key, sl in slots(scale):
            if not R["occ"].get(key):
                continue
            v = _slot_bg_v(img, sl)
            if key not in R["first_seen"]:
                R["first_seen"][key] = ms
                if v < BG_READY_V:
                    R["dim_seen"].add(key)
                out(f"  {ms:7.1f}ms f{n:<4} แถว{key[0] + 1} คอลัมน์{key[1] + 1} ขึ้น  "
                    f"(V={v:.0f} {'ทึบ' if v < BG_READY_V else 'ปกติ'})")
            elif key in R["dim_seen"] and key not in R["normal_at"] and v >= BG_READY_V:
                R["normal_at"][key] = (ms, n)
                R["flash"][key] = n
                out(f"  {ms:7.1f}ms f{n:<4} แถว{key[0] + 1} คอลัมน์{key[1] + 1} เปลี่ยนเป็นสีปกติ  "
                    f"(V={v:.0f}, เฟรมก่อนหน้า V={R['v_prev'].get(key, 0):.0f})  = {ms - R['first_seen'][key]:.0f}ms หลังช่องขึ้น")
                if R["snaps"] < 3:
                    snap, R["snaps"] = "normal", R["snaps"] + 1
            R["v_prev"][key] = v
        rec.q.put(("frame", img.copy(), scale, dict(R["occ"]), dict(R["flash"]), header, rounds, n, ms,
                   1000 / gap if gap > 0 else 0, snap))
    if visible:
        finish("จบ")
    rec.q.put(None)
    rec.join(timeout=60)


def scale_of(img):
    return max((img.shape[1] - 10) / 525, 0.3)          # กรอบกว้าง 525 ที่จอ 2K + pad 5 สองข้าง


def main():
    args = sys.argv[1:]
    flags = {a for a in args if a.startswith("--")}
    files = [a for a in args if not a.startswith("--")]
    show, video = "--no-window" not in flags, "--no-video" not in flags

    if "--image" in flags:                       # ทดสอบกับรูป: วาดกรอบ ไม่จับจอ
        os.makedirs(OUT, exist_ok=True)
        for f in files:
            img = cv2.imread(f)
            scale = scale_of(img)
            occ = occupancy(img, scale)
            hdr = _header_score(img, scale)
            vs = [_slot_bg_v(img, sl) for k, sl in slots(scale) if occ[k]]
            dim = sum(v < BG_READY_V for v in vs)
            path = os.path.join(OUT, os.path.splitext(os.path.basename(f))[0] + "_timing.png")
            cv2.imwrite(path, draw(img, scale, occ, {}, hdr, 0, 0, 0.0, 0.0))
            print(f"{f}: header={hdr:.2f}  ช่องมีไอเท็ม {len(vs)}  ทึบ(แดง) {dim}  ปกติ(เขียว) {len(vs) - dim}  -> {path}")
        return

    if "--selftest" in flags:                    # ภาพปลอม: ทึบ -> สีปกติ ผ่านระบบทั้งหมด (ไม่จับจอ)
        dim, normal = cv2.imread(files[0]), cv2.imread(files[1])
        it = iter([dim] * 40 + [normal] * 40)

        def grab():
            time.sleep(0.008)
            return next(it, None)
        run(grab, scale_of(dim), show=False, video=video)
        return

    from object_detection import get_scale
    from screen import Screen
    screen = Screen()
    box = screen.get_region("mission_reward")
    try:
        run(lambda: screen.grab(box), get_scale(screen), show=show, video=video,
            stop=lambda: bool(ctypes.windll.user32.GetAsyncKeyState(STOP_KEY) & 0x8000))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
