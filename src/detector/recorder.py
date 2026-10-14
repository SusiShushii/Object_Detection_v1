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


def _patch_v(vch, x, y, w, h):
    """ค่าเดียวกับ clicker._slot_bg_v แต่คำนวณจากช่อง V ที่แปลงไว้แล้ว (ค่ากลางของ 4 จุดมุม/ขอบช่อง)"""
    f = lambda a: int(a)
    p = [vch[y + f(h * .06):y + f(h * .15), x + f(w * .06):x + f(w * .16)],
         vch[y + f(h * .06):y + f(h * .15), x + f(w * .84):x + f(w * .94)],
         vch[y + f(h * .46):y + f(h * .55), x + f(w * .03):x + f(w * .10)],
         vch[y + f(h * .46):y + f(h * .55), x + f(w * .90):x + f(w * .97)]]
    vals = [float(a.mean()) for a in p if a.size]
    return float(np.median(vals)) if vals else 0.0


class RoundRecorder:
    def __init__(self, scale, box, rects, t0, round_no=1):
        self.scale, self.box, self.rects, self.t0 = scale, box, rects, t0
        self.dir = os.path.join(paths.OUTPUT, "assist_out", f"{time.strftime('%Y%m%d_%H%M%S')}_round{round_no}")
        os.makedirs(self.dir, exist_ok=True)
        self.frame_no = 0
        self.last = None                       # (ms, img) เฟรมล่าสุด (ไว้เซฟภาพตอนคลิก)
        self.clicks = []                       # [(n, ms, group)]  group = จังหวะเก็บ (คลิกหลัก + กันพลาด รวมเป็นกลุ่มเดียว)
        self.click_frames = []                 # [(n, frame_no)]
        self.trace = []                        # (ms, V, empty, slot_idx)
        self.last_empty, self.last_empty_t = None, -1e9
        self.q = queue.Queue()
        self.th = threading.Thread(target=self._writer, daemon=True)
        self.th.start()
        self.last_t = None
        self.verdicts = []                     # [(n, ms, ok หรือ None, ข้อความ)]

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
        v = _slot_bg_v(img, self.rects[idx]) if idx is not None else None       # เบา (~0.2 ms)
        fps = 1000 / (ms - self.last_t) if self.last_t is not None and ms > self.last_t else 0
        self.last_t = ms
        entry = [ms, v, None, idx]                                              # ช่องว่างไหม: thread เขียนเติมให้ (template match หนัก)
        self.trace.append(entry)
        frame_copy = img.copy()
        self.last = (frame_copy, cur, idx, v, ms, self.frame_no, fps)
        self.q.put(("frame", frame_copy, cur, idx, v, ms, self.frame_no, fps, entry))

    def mark_click(self, n, ms, group=None):
        self.clicks.append((n, ms, n if group is None else group))
        self.click_frames.append((n, self.frame_no))
        if self.last is not None:
            self.q.put(("snap", f"click{n}.png") + self.last)

    # ---------------- thread วาด + เขียนไฟล์ ----------------
    def _draw(self, img, cur, idx, v, ms, frame_no, fps):
        big = cv2.resize(img, None, fx=ZOOM, fy=ZOOM, interpolation=cv2.INTER_NEAREST)
        vch = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)[..., 2]                       # V ทั้งภาพครั้งเดียว
        for i, (x, y, w, h, _) in enumerate(self.rects):
            bv = _patch_v(vch, x, y, w, h)
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
            _, img, cur, idx, v, ms, frame_no, fps, entry = it
            if idx is not None and ms - self.last_empty_t >= 40:               # เช็กช่องว่างทุก ~40 ms (ใน thread ไม่ถ่วงลูปหลัก)
                sx, sy, sw, sh, _ = self.rects[idx]
                self.last_empty = _is_empty_slot(img, sx, sy, sw, sh, self.scale)
                self.last_empty_t = ms
            entry[2] = self.last_empty if idx is not None else None
            if writer is not None and int((ms - self._t_first) / 1000 * VIDEO_FPS) + 1 <= written:
                continue                                                       # เฟรมนี้ไม่เพิ่มเฟรมวิดีโอ (วิดีโอ 120 fps) ข้ามการวาด
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

    def _states(self):
        out = []
        for ms, v, e, i in self.trace:
            if v is not None:
                out.append((ms, "ว่าง" if e else ("พร้อม" if v >= BG_READY_V else "ทึบ")))
        return out

    def _verdicts(self, window=400.0):
        """ต่อจังหวะ (กลุ่มคลิก): เก็บได้ = ช่องกลายเป็นว่างภายใน window ms หลังคลิกแรกของกลุ่ม หรือ พร้อม->ทึบแว้บ
        คลิกตอนช่องว่างอยู่แล้ว = ไม่นับ  (คลิกหลัก + กันพลาดห่างกันไม่กี่ ms แยกไม่ออกว่าอันไหนติด จึงนับเป็นกลุ่มเดียว)"""
        states = self._states()
        groups = {}
        for n, t, g in self.clicks:
            groups.setdefault(g, []).append(t)
        res = []
        for g, times in sorted(groups.items()):
            t = min(times)
            clicks_txt = "คลิก " + ", ".join(f"{x:.0f}" for x in sorted(times))
            before = [s_ for ms, s_ in states if ms <= t]
            prior = before[-1] if before else None
            after = [(ms, s_) for ms, s_ in states if t < ms <= t + window]
            if prior == "ว่าง":
                res.append((g, t, None, f"{clicks_txt}  ช่องว่างอยู่แล้วตอนกด (คลิกลงช่องว่าง)"))
                continue
            empty = next((ms for ms, s_ in after if s_ == "ว่าง"), None)
            seq = [prior] + [s_ for _, s_ in after]
            flash = next((after[k - 1][0] for k in range(1, len(seq)) if seq[k - 1] == "พร้อม" and seq[k] == "ทึบ"), None)
            if empty is not None:
                res.append((g, t, True, f"{clicks_txt}  ช่องว่างที่ +{empty - t:.0f}ms  ก่อนกด={prior}"))
            elif flash is not None:
                res.append((g, t, True, f"{clicks_txt}  ทึบแว้บที่ +{flash - t:.0f}ms  ก่อนกด={prior}"))
            else:
                res.append((g, t, False, f"{clicks_txt}  ไม่เห็นช่องว่าง/ทึบแว้บใน {window:.0f}ms  ก่อนกด={prior}"))
        return res

    # ---------------- จบรอบ: เขียนไฟล์ + สรุป timeline ----------------
    def finish(self, log):
        self.q.put(None)
        self.th.join(timeout=60)
        self.trace = [tuple(e) for e in self.trace]
        with open(os.path.join(self.dir, "trace.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["ms", "V_under_cursor", "empty_under_cursor", "slot_index"])
            for ms, v, e, i in self.trace:
                w.writerow([f"{ms:.1f}", "" if v is None else f"{v:.1f}", "" if e is None else int(e), "" if i is None else i + 1])
        events = []                                                      # (ms, ข้อความ)
        for n, ms, g in self.clicks:
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
        self.verdicts = self._verdicts()
        got = sum(1 for _, _, ok, _ in self.verdicts if ok)
        for n, ms, ok, text in self.verdicts:
            mark = "✓ เก็บได้" if ok else ("- " if ok is None else "✗ ไม่เข้า")
            log.info(f"  [วิเคราะห์] จังหวะที่ {n} ที่ {ms:.0f}ms: {mark}  ({text})")
        if len(self.clicks) >= 2:
            gaps = ", ".join(f"{a[0]}→{b[0]}: {b[1] - a[1]:.0f}" for a, b in zip(self.clicks, self.clicks[1:]))
            log.info(f"  [วิเคราะห์] ระยะห่างระหว่างคลิก (ms): {gaps}")
        return self.dir
