"""
wgc_check.py - เช็กว่า WGC อ่านภาพจาก process เกมได้ไหม วัด fps จริงของเฟรมที่เกมส่งมา และ record ค้างไว้ (ไม่คลิก ไม่ขยับเมาส์)

    python main.py wgc --check                    เช็กต่อเนื่อง: บอก process / หน้าต่าง / fps ทุกวินาที  (หยุด: Ctrl+C หรือ F12)
    python main.py wgc --check --record           + บันทึกวิดีโอกรอบ Mission Reward ที่บอทใช้ (60 fps) พร้อมข้อมูลสถานะบนภาพ
    python main.py wgc --check --record --full   + บันทึกทั้งหน้าต่างเกม (ย่อเป็น 1280x720, 30 fps)
    python main.py wgc --check --seconds 30       เช็กแค่ 30 วินาทีแล้วสรุป
    python main.py wgc --check --window Tails     ระบุชื่อ process/หน้าต่างเอง (ค่าเริ่มต้นจาก CAPTURE_WINDOW ใน config.py)

ที่ log (หน้าจอ + ไฟล์ output/wgc_check/<เวลา>/log.txt):
  - เจอ process ไหม: ชื่อ .exe, PID, path, ชื่อหน้าต่าง, ตำแหน่ง/ขนาด, ย่ออยู่ไหม, โฟกัสอยู่ไหม, รีเฟรชเรตจอ
  - ทุกวินาที: เฟรมที่เกมส่งมา (fps), ช่วงห่างเฟรม กลาง/p99/สูงสุด, สถานะ
  - เหตุการณ์: เฟรมหยุดมา / กลับมา, หน้าต่างถูกย่อ / กลับมา, โฟกัสเปลี่ยน, ขนาดเปลี่ยน, เกมปิด/เปิดใหม่
  - สรุปท้ายรอบ: fps เฉลี่ย แยกตามสถานะ (โฟกัส / ไม่โฟกัส / ย่อ)  + stats.csv ทุกวินาที
ลองสลับสถานะหน้าต่างเกมระหว่างเช็ก (โฟกัส / เอาหน้าต่างอื่นบัง / ย่อ) จะเห็นว่าแต่ละสถานะได้เฟรมไหม
"""
import csv
import ctypes
import os
import queue
import sys
import threading
import time
from collections import deque
from ctypes import wintypes

import cv2
import numpy as np

import config
from detector import paths
from detector.clicker import STOP_KEY
from detector.screen import Screen
from detector.wgc import find_game_window, kernel32, user32, window_bounds

STALL_SEC = 0.5          # ไม่มีเฟรมนานเท่านี้ = "เฟรมหยุด"
REGION_FPS, FULL_FPS = 60, 30
ZOOM = 2
BAR_H = 54

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

LOGF = {"f": None}


def out(msg):
    line = f"{time.strftime('%H:%M:%S')}  {msg}"
    print(line, flush=True)
    if LOGF["f"]:
        LOGF["f"].write(line + "\n")
        LOGF["f"].flush()


class DEVMODE(ctypes.Structure):
    _fields_ = [("name", ctypes.c_wchar * 32), ("spec", wintypes.WORD), ("drv", wintypes.WORD), ("size", wintypes.WORD),
                ("extra", wintypes.WORD), ("fields", wintypes.DWORD), ("px", ctypes.c_long), ("py", ctypes.c_long),
                ("orient", wintypes.DWORD), ("fixed", wintypes.DWORD), ("color", ctypes.c_short), ("duplex", ctypes.c_short),
                ("yres", ctypes.c_short), ("tt", ctypes.c_short), ("collate", ctypes.c_short),
                ("form", ctypes.c_wchar * 32), ("logpix", wintypes.WORD), ("bits", wintypes.DWORD), ("w", wintypes.DWORD),
                ("h", wintypes.DWORD), ("flags", wintypes.DWORD), ("hz", wintypes.DWORD), ("rest", ctypes.c_byte * 32)]


def display_mode():
    dm = DEVMODE()
    dm.size = ctypes.sizeof(DEVMODE) - 32
    if user32.EnumDisplaySettingsW(None, -1, ctypes.byref(dm)):
        return f"{dm.w}x{dm.h} @ {dm.hz} Hz"
    return "?"


def proc_info(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    path = ""
    h = kernel32.OpenProcess(0x1000, False, pid.value)
    if h:
        buf = ctypes.create_unicode_buffer(520)
        n = wintypes.DWORD(520)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            path = buf.value
        kernel32.CloseHandle(h)
    n = user32.GetWindowTextLengthW(hwnd)
    title = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, title, n + 1)
    return pid.value, path, title.value


def window_state(hwnd):
    """dict สถานะหน้าต่าง: alive, minimized, visible, foreground, size"""
    if not hwnd or not user32.IsWindow(hwnd):
        return dict(alive=False, minimized=False, visible=False, foreground=False, size=(0, 0), pos=(0, 0))
    l, t, r, b = window_bounds(hwnd)
    return dict(alive=True, minimized=bool(user32.IsIconic(hwnd)), visible=bool(user32.IsWindowVisible(hwnd)),
                foreground=user32.GetForegroundWindow() == hwnd, size=(r - l, b - t), pos=(l, t))


def label(st, flowing):
    if not st["alive"]:
        return "ไม่พบหน้าต่าง"
    if st["minimized"]:
        return "ถูกย่อ"
    return ("โฟกัส" if st["foreground"] else "ไม่โฟกัส") + ("" if flowing else " (เฟรมไม่มา)")


class Checker:
    def __init__(self, args):
        self.args = args
        self.lock = threading.Lock()
        self.arrivals = deque()                       # perf_counter ของเฟรมที่มาถึงล่าสุด (เก็บ ~10 วินาที)
        self.total_frames = 0
        self.ctl, self.cap_hwnd = None, None
        self.slot = None                              # (ภาพ BGR ล่าสุดที่ใช้ record, เวลา)
        self.last_save = 0.0
        self.region = None                            # (x1,y1,x2,y2) กรอบของบอทในพิกัดจอ
        self.rec_q = None
        self.err = None

    # ---------------- ตัวจับภาพ ----------------
    def start_capture(self, hwnd):
        from windows_capture import WindowsCapture
        self.stop_capture()
        cap = WindowsCapture(cursor_capture=False, draw_border=False, minimum_update_interval=0, window_hwnd=hwnd)
        record = self.args.record
        full = self.args.full
        interval = 1.0 / (FULL_FPS if full else REGION_FPS)

        @cap.event
        def on_frame_arrived(frame, control):
            now = time.perf_counter()
            with self.lock:
                self.arrivals.append(now)
                self.total_frames += 1
                while self.arrivals and now - self.arrivals[0] > 10:
                    self.arrivals.popleft()
            if record and now - self.last_save >= interval:
                self.last_save = now
                fb = frame.frame_buffer
                h, w = fb.shape[:2]
                if full:
                    img = cv2.resize(np.ascontiguousarray(fb), (1280, 720), interpolation=cv2.INTER_AREA)
                    img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
                else:
                    ox, oy = window_bounds(hwnd)[:2]
                    x1, y1, x2, y2 = self.region
                    x1, y1, x2, y2 = max(0, x1 - ox), max(0, y1 - oy), min(w, x2 - ox), min(h, y2 - oy)
                    if x2 <= x1 or y2 <= y1:
                        return
                    img = cv2.cvtColor(np.array(fb[y1:y2, x1:x2]), cv2.COLOR_BGRA2BGR)
                self.slot = (img, now)

        @cap.event
        def on_closed():
            pass

        self.ctl = cap.start_free_threaded()
        self.cap_hwnd = hwnd

    def stop_capture(self):
        if self.ctl is not None:
            try:
                self.ctl.stop()
            except Exception:
                pass
        self.ctl, self.cap_hwnd = None, None

    def stats(self, window=1.0):
        """(จำนวนเฟรมใน window วินาทีล่าสุด, gap กลาง, p99, สูงสุด ms, เวลาตั้งแต่เฟรมล่าสุด)"""
        now = time.perf_counter()
        with self.lock:
            a = [t for t in self.arrivals if now - t <= window]
            last = self.arrivals[-1] if self.arrivals else None
        since = (now - last) if last else 1e9
        if len(a) < 3:
            return len(a), 0.0, 0.0, 0.0, since
        g = np.diff(a) * 1000
        return len(a), float(np.median(g)), float(np.percentile(g, 99)), float(g.max()), since

    # ---------------- record (thread แยก: เขียนวิดีโอที่ fps คงที่ แม้เฟรมหยุดมา) ----------------
    def start_recorder(self, folder):
        full = self.args.full
        fps = FULL_FPS if full else REGION_FPS
        self.rec_stop = threading.Event()
        self.info = {"text": ""}

        def loop():
            writer, nxt = None, time.perf_counter()
            while not self.rec_stop.is_set():
                nxt += 1.0 / fps
                time.sleep(max(0.0, nxt - time.perf_counter()))
                slot = self.slot
                if slot is None:
                    continue
                img, t_frame = slot
                if not full:
                    img = cv2.resize(img, None, fx=ZOOM, fy=ZOOM, interpolation=cv2.INTER_NEAREST)
                age = time.perf_counter() - t_frame
                bar = np.zeros((BAR_H, img.shape[1], 3), np.uint8)
                col = (0, 0, 255) if age > STALL_SEC else (255, 255, 255)
                cv2.putText(bar, self.info["text"], (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, col, 1)
                cv2.putText(bar, ("NO NEW FRAMES  %.1fs" % age) if age > STALL_SEC else "frames flowing", (6, 44),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255) if age > STALL_SEC else (0, 200, 0), 1)
                pic = np.vstack([bar, img])
                if writer is None:
                    h, w = pic.shape[:2]
                    for name, cc in (("wgc.mp4", "mp4v"), ("wgc.avi", "MJPG")):
                        wr = cv2.VideoWriter(os.path.join(folder, name), cv2.VideoWriter_fourcc(*cc), fps, (w, h))
                        if wr.isOpened():
                            writer = wr
                            break
                if writer is not None:
                    writer.write(pic)
            if writer is not None:
                writer.release()

        self.rec_thread = threading.Thread(target=loop, daemon=True)
        self.rec_thread.start()

    def stop_recorder(self):
        if getattr(self, "rec_stop", None):
            self.rec_stop.set()
            self.rec_thread.join(timeout=10)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--full", action="store_true", help="record ทั้งหน้าต่างเกม (ย่อ 1280x720) แทนกรอบของบอท")
    ap.add_argument("--seconds", type=float, default=None)
    ap.add_argument("--window", default=config.CAPTURE_WINDOW)
    args = ap.parse_args()
    if args.full:
        args.record = True
    try:
        import windows_capture  # noqa: F401
    except ImportError:
        print("ยังไม่ได้ติดตั้ง windows-capture:  pip install --no-deps windows-capture==2.0.1")
        return

    stamp = time.strftime("%Y%m%d_%H%M%S")
    folder = os.path.join(paths.OUTPUT, "wgc_check", stamp)
    os.makedirs(folder, exist_ok=True)
    LOGF["f"] = open(os.path.join(folder, "log.txt"), "w", encoding="utf-8")
    csvf = open(os.path.join(folder, "stats.csv"), "w", newline="", encoding="utf-8")
    cw = csv.writer(csvf)
    cw.writerow(["t_s", "frames_last_1s", "gap_median_ms", "gap_p99_ms", "gap_max_ms", "state", "w", "h"])

    screen = Screen()
    chk = Checker(args)
    chk.region = screen.get_region("mission_reward")
    out(f"[wgc] เริ่มเช็ก  ค้นหา process/หน้าต่าง: '{args.window}'  จอ: {display_mode()}  "
        f"{'record ' + ('ทั้งหน้าต่าง' if args.full else f'กรอบบอท {chk.region}') if args.record else 'ไม่ record'}  "
        f"โฟลเดอร์: {os.path.relpath(folder, paths.ROOT)}  หยุด: Ctrl+C / F12")
    if args.record:
        chk.start_recorder(folder)

    t_start = time.perf_counter()
    prev = None
    hwnd = None
    per_state = {}                                           # สถานะ -> [วินาทีที่อยู่, เฟรมรวม]
    last_print = 0.0
    last_total = 0
    try:
        while True:
            if ctypes.windll.user32.GetAsyncKeyState(STOP_KEY) & 0x8000:
                out("[wgc] กด F12 -> หยุด")
                break
            now = time.perf_counter()
            if args.seconds and now - t_start >= args.seconds:
                break
            # หา process/หน้าต่าง (ถ้าหายหรือเกมเปิดใหม่)
            if hwnd is None or not user32.IsWindow(hwnd):
                hwnd = find_game_window(args.window)
                if hwnd:
                    pid, path, title = proc_info(hwnd)
                    st0 = window_state(hwnd)
                    out(f"[พบ process] {os.path.basename(path)}  PID {pid}  path {path}")
                    out(f"            หน้าต่าง '{title}'  hwnd {hwnd}  ขนาด {st0['size'][0]}x{st0['size'][1]} @{st0['pos']}  "
                        f"ย่อ={st0['minimized']}  โฟกัส={st0['foreground']}")
                    chk.start_capture(hwnd)
                    out("[wgc] เริ่มจับภาพหน้าต่างเกมแล้ว (WGC, minimum_update_interval=0)")
                    prev = None
                else:
                    if prev != "notfound":
                        out(f"[wgc] ไม่พบ process/หน้าต่าง '{args.window}' (เปิดเกมและอย่าย่อหน้าต่าง) รอต่อ...")
                        prev = "notfound"
                    time.sleep(0.5)
                    continue
            st = window_state(hwnd)
            n, med, p99, mx, since = chk.stats(1.0)
            flowing = since <= STALL_SEC
            state = label(st, flowing)
            # เหตุการณ์ (เปลี่ยนสถานะ)
            if prev is not None and prev != "notfound":
                for key, txt_on, txt_off in (("minimized", "หน้าต่างถูกย่อ", "หน้าต่างกลับมาจากการย่อ"),
                                              ("foreground", "เกมได้โฟกัส", "เกมเสียโฟกัส (มีหน้าต่างอื่นอยู่หน้า)")):
                    if st[key] != prev[key]:
                        out(f"[เหตุการณ์] {txt_on if st[key] else txt_off}")
                if st["size"] != prev["size"]:
                    out(f"[เหตุการณ์] ขนาดหน้าต่างเปลี่ยน {prev['size']} -> {st['size']}")
                if prev["flowing"] and not flowing:
                    out(f"[เหตุการณ์] เฟรมหยุดมา (ไม่มีเฟรมใหม่เกิน {STALL_SEC}s)  สถานะหน้าต่าง: {label(st, True)}")
                if not prev["flowing"] and flowing:
                    out(f"[เหตุการณ์] เฟรมกลับมาแล้ว ({n} เฟรมใน 1 วินาทีล่าสุด)  สถานะหน้าต่าง: {label(st, True)}")
            st["flowing"] = flowing
            prev = st
            if now - last_print >= 1.0:
                last_print = now
                el = now - t_start
                total = chk.total_frames
                key = label(st, True)
                ps = per_state.setdefault(key, [0.0, 0])
                ps[0] += 1.0
                ps[1] += total - last_total
                last_total = total
                out(f"  t={el:5.0f}s  เฟรมจากเกม {n:4d} fps  gap กลาง {med:5.2f}ms p99 {p99:6.2f} สูงสุด {mx:7.1f}ms  "
                    f"สถานะ: {state}  {st['size'][0]}x{st['size'][1]}")
                cw.writerow([f"{el:.1f}", n, f"{med:.2f}", f"{p99:.2f}", f"{mx:.1f}", state, st["size"][0], st["size"][1]])
                csvf.flush()
                chk.info = getattr(chk, "info", {"text": ""})
                if args.record:
                    chk.info["text"] = f"t={el:5.0f}s  {n} fps  gap {med:.2f}ms (p99 {p99:.1f}, max {mx:.0f})  [{state}]  {st['size'][0]}x{st['size'][1]}"
            time.sleep(0.05)
    except KeyboardInterrupt:
        out("[wgc] หยุด (Ctrl+C)")
    finally:
        chk.stop_capture()
        chk.stop_recorder()
        total_t = time.perf_counter() - t_start
        out(f"\n[สรุป] เช็ก {total_t:.0f} วินาที  เฟรมรวมที่เกมส่งมา {chk.total_frames}  เฉลี่ย {chk.total_frames / max(total_t, 1):.1f} fps")
        for k, (secs, frames) in sorted(per_state.items(), key=lambda kv: -kv[1][0]):
            out(f"   สถานะ {k:<14} {secs:5.0f} วินาที  เฟรม {frames:6d}  = {frames / max(secs, 1):6.1f} fps")
        out(f"   ผลอยู่ที่ {os.path.relpath(folder, paths.ROOT)}  (log.txt, stats.csv" + (", วิดีโอ wgc.mp4)" if args.record else ")"))
        csvf.close()
        LOGF["f"].close()


if __name__ == "__main__":
    main()
