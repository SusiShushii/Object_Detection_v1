"""
recorder.py - บันทึกวิดีโอ + วิเคราะห์ต่อรอบ ตอนรัน assist --record

ต่อรอบได้ output/assist_out/<เวลา>_roundN/
    round.mp4        วิดีโอกรอบ Mission Reward (ขยาย 2x, 120 fps ความเร็วจริง) พร้อมเลขเฟรม / ms / fps
                     กรอบสีทุกช่อง: แดง = ทึบ, เขียว = สีปกติ, เทา = ว่าง  ช่องที่เมาส์อยู่ = กรอบหนา + ค่า V
                     วงกลมฟ้า = เมาส์  ป้าย "CLICK n" ขึ้น 12 เฟรมหลังคลิก
    click<n>.png     ภาพเฟรมที่คลิกครั้งที่ n (เฟรมล่าสุดก่อนกด)
    trace.csv        ทุกเฟรม: ms, ค่า V ของช่องใต้เมาส์, ช่องว่างไหม, คลิกไหม
และ log สรุป timeline: ช่องใต้เมาส์ "พร้อม/ทึบ/ว่าง" ตอนกี่ ms เทียบกับเวลาที่คลิก
"""

import csv
import os
import queue
import threading
import time

import cv2
import numpy as np

from . import paths
from .clicker import BG_READY_V, _is_empty_slot, _slot_bg_v

VIDEO_FPS = 120
ZOOM = 2
BAR_H = 26
CLICK_FLASH_FRAMES = 12
RED, GREEN, GREY, CYAN, YELLOW = (0, 0, 255), (0, 200, 0), (120, 120, 120), (255, 200, 0), (0, 230, 255)


class RoundRecorder:
    def __init__(self, scale, box, rects, t0, round_no=1):
        self.scale, self.box, self.rects, self.t0 = scale, box, rects, t0
        self.dir = os.path.join(paths.OUTPUT, "assist_out", f"{time.strftime('%Y%m%d_%H%M%S')}_round{round_no}")
        os.makedirs(self.dir, exist_ok=True)
        self.frame_no = 0
        self.last = None                       # (ms, img) เฟรมล่าสุด (ไว้เซฟภาพตอนคลิก)
        self.clicks = []                       # [(n, ms)]
        self.click_frames = []                 # [(n, frame_no)]
        self.trace = []                        # (ms, V, empty, slot_idx)
        self.last_empty, self.last_empty_t = None, 0.0
        self.q = queue.Queue()
        self.th = threading.Thread(target=self._writer, daemon=True)
        self.th.start()
        self.last_t = None

    # ---------------- ฝั่งจับภาพ (เรียกจากลูปหลัก ต้องเบา) ----------------
    def slot_at(self, cur):
        x, y = cur[0] - self.box[0], cur[1] - self.box[1]
        for i, (sx, sy, sw, sh, _) in enumerate(self.rects):
            if sx <= x <= sx + sw and sy <= y <= sy + sh:
                return i
        return None

    def add(self, img, cur):
        now = time.perf_counter()
        ms = (now - self.t0) * 1000
        self.frame_no += 1
        idx = self.slot_at(cur)
        v, empty = None, None
        if idx is not None:
            sx, sy, sw, sh, _ = self.rects[idx]
            v = _slot_bg_v(img, self.rects[idx])
            if now - self.last_empty_t >= 0.04:                 # เช็กช่องว่างทุก ~40 ms (ถูกกว่าเช็กทุกเฟรม)
                self.last_empty = _is_empty_slot(img, sx, sy, sw, sh, self.scale)
                self.last_empty_t = now
            empty = self.last_empty
        fps = 1000 / (ms - self.last_t) if self.last_t is not None and ms > self.last_t else 0
        self.last_t = ms
        self.trace.append((ms, v, empty, idx))
        frame_copy = img.copy()
        self.last = (frame_copy, cur, idx, v, ms, self.frame_no, fps)
        self.q.put(("frame", frame_copy, cur, idx, v, ms, self.frame_no, fps))

    def mark_click(self, n, ms):
        self.clicks.append((n, ms))
        self.click_frames.append((n, self.frame_no))
        if self.last is not None:
            self.q.put(("snap", f"click{n}.png") + self.last)

    # ---------------- thread วาด + เขียนไฟล์ ----------------
    def _draw(self, img, cur, idx, v, ms, frame_no, fps):
        big = cv2.resize(img, None, fx=ZOOM, fy=ZOOM, interpolation=cv2.INTER_NEAREST)
        for i, (x, y, w, h, _) in enumerate(self.rects):
            bv = _slot_bg_v(img, (x, y, w, h, 0))
            col = GREEN if bv >= BG_READY_V else RED
            thick = 1
            if i == idx:
                thick = 3
                cv2.putText(big, f"V={bv:.0f}", (x * ZOOM + 3, (y + h) * ZOOM - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 1)
            cv2.rectangle(big, (x * ZOOM, y * ZOOM), ((x + w) * ZOOM, (y + h) * ZOOM), col, thick)
        cx, cy = (cur[0] - self.box[0]) * ZOOM, (cur[1] - self.box[1]) * ZOOM
        cv2.circle(big, (int(cx), int(cy)), 8, CYAN, 2)
        bar = np.zeros((BAR_H, big.shape[1], 3), np.uint8)
        cv2.putText(bar, f"frame {frame_no}  {ms:7.1f}ms  {fps:4.0f}fps", (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (255, 255, 255), 1)
        for n, fr in self.click_frames:
            if 0 <= frame_no - fr < CLICK_FLASH_FRAMES:
                cv2.putText(bar, f"CLICK {n}", (big.shape[1] - 120, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.6, YELLOW, 2)
                cv2.circle(big, (int(cx), int(cy)), 16, YELLOW, 3)
        return np.vstack([bar, big])

    def _writer(self):
        writer, written = None, 0
        while True:
            it = self.q.get()
            if it is None:
                break
            if it[0] == "snap":                                # ภาพเฟรมล่าสุดก่อนกด (วาดกรอบ + ป้าย CLICK)
                _, name, simg, scur, sidx, sv, sms, sfn, sfps = it
                cv2.imwrite(os.path.join(self.dir, name), self._draw(simg, scur, sidx, sv, sms, sfn, sfps))
                continue
            _, img, cur, idx, v, ms, frame_no, fps = it
            pic = self._draw(img, cur, idx, v, ms, frame_no, fps)
            if writer is None:
                h, w = pic.shape[:2]
                for name, cc in (("round.mp4", "mp4v"), ("round.avi", "MJPG")):
                    wr = cv2.VideoWriter(os.path.join(self.dir, name), cv2.VideoWriter_fourcc(*cc), VIDEO_FPS, (w, h))
                    if wr.isOpened():
                        writer = wr
                        break
                self._t_first = ms
            if writer is not None:
                target = int((ms - self._t_first) / 1000 * VIDEO_FPS) + 1
                for _ in range(max(target - written, 0)):
                    writer.write(pic)
                written = max(written, target)
        if writer is not None:
            writer.release()

    # ---------------- จบรอบ: เขียนไฟล์ + สรุป timeline ----------------
    def finish(self, log):
        self.q.put(None)
        self.th.join(timeout=60)
        with open(os.path.join(self.dir, "trace.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["ms", "V_under_cursor", "empty_under_cursor", "slot_index"])
            for ms, v, e, i in self.trace:
                w.writerow([f"{ms:.1f}", "" if v is None else f"{v:.1f}", "" if e is None else int(e), "" if i is None else i + 1])
        events = []                                                      # (ms, ข้อความ)
        for n, ms in self.clicks:
            events.append((ms, f"กดครั้งที่ {n}"))
        prev_state = None
        for ms, v, e, i in self.trace:
            if v is None:
                continue
            state = "ว่าง" if e else ("พร้อม(สีปกติ)" if v >= BG_READY_V else "ทึบ")
            if state != prev_state:
                events.append((ms, f"ช่องใต้เมาส์ -> {state} (V={v:.0f})"))
                prev_state = state
        events.sort(key=lambda z: z[0])
        log.info(f"  [วิเคราะห์] timeline ช่องใต้เมาส์ ({len(self.trace)} เฟรม  บันทึกที่ {os.path.relpath(self.dir, paths.ROOT)})")
        for ms, text in events:
            log.info(f"      {ms:8.1f}ms  {text}")
        if len(self.clicks) >= 2:
            gaps = ", ".join(f"ครั้งที่ {a[0]}→{b[0]} ห่าง {b[1] - a[1]:.0f}ms" for a, b in zip(self.clicks, self.clicks[1:]))
            log.info(f"  [วิเคราะห์] ระยะห่างระหว่างคลิก: {gaps}")
        return self.dir
