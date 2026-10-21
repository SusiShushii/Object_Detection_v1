"""
capture_compare.py - เทียบ WGC กับ mss "พร้อมกัน" บนเกมจริง (ไม่คลิก ไม่ขยับเมาส์)

    python main.py capcmp                 เปิดทิ้งไว้ แล้วเล่นเกมตามปกติ (ผ่านหน้า Mission Result หลายๆ รอบ)  หยุด: F12 ค้าง / Ctrl+C
    python main.py capcmp --rounds 15     หยุดเองเมื่อได้ครบ 15 รอบ

ทั้งสองวิธีจับภาพกรอบ Mission Reward เดียวกันในเวลาเดียวกัน (2 thread, นาฬิกาเดียวกัน) แล้ววัดต่อรอบ:
  - t_header = เฟรมแรกที่เห็นหัวข้อ "Mission Reward"
  - t_dim    = เฟรมแรกที่เห็นช่องทึบ      t_normal = เฟรมแรกที่ช่องเปลี่ยนเป็นสีปกติ
สรุป (ต่อรอบ + สะสม + ตอนจบ):
  - ใครเห็นก่อนกัน: ส่วนต่าง (wgc - mss) ของ t_header / t_normal  (ติดลบ = wgc เห็นก่อน)
  - ใครแกว่งกว่ากัน: ระยะ t_normal - t_header ของแต่ละวิธี  ค่าจริงของเกมคงที่ ~4,000 ms  ยิ่งเบี่ยงเบน(std)ต่ำ = ยิ่งนิ่ง
ผลเก็บที่ output/capcmp/<เวลา>/rounds.csv + log.txt  ต้องเปิดเกมไว้หน้าสุด (ไม่ย่อ) ไม่งั้น WGC เหลือ ~15 fps
"""
import csv
import os
import statistics as st
import sys
import threading
import time

import config
from detector import clicker as ck
from detector import paths
from detector.collector import slot_rects
from detector.detection import get_scale
from detector.screen import Screen

HDR_MISS_END = ck.HEADER_LOST_FRAMES
locate_lock = threading.Lock()


class Worker(threading.Thread):
    """จับภาพด้วยวิธีหนึ่ง วนจับกรอบ Mission Reward ตลอด แล้วเก็บเวลาแต่ละรอบ"""

    def __init__(self, name, make):
        super().__init__(daemon=True)
        self.name_, self.make = name, make
        self.rounds, self.stop, self.error = [], False, None
        self.frames, self.fallbacks, self.t_start = 0, 0, time.perf_counter()
        self.ready = threading.Event()

    def run(self):
        try:
            screen = self.make()                       # สร้างใน thread นี้ (mss ใช้ข้าม thread ไม่ได้)
            self.backend = getattr(screen, "backend", "mss")
            self.ready.set()
            scale = get_scale(screen)
            box = screen.get_region("mission_reward")
            rects = slot_rects(scale)
            cur, miss, last_locate = None, 0, 0.0
            while not self.stop:
                img = screen.grab(box)
                now = time.perf_counter()
                self.frames += 1
                hdr = ck._header_score(img, scale) >= ck.HEADER_MATCH
                if cur is None:
                    if hdr:
                        cur = {"t_header": now, "t_dim": None, "t_normal": None, "frames": 0,
                               "fb0": getattr(screen, "fallbacks", 0)}
                        miss = 0
                    elif now - last_locate >= ck.LOCATE_EVERY:
                        last_locate = now
                        with locate_lock:
                            if ck.locate_header(screen, scale):
                                box = screen.get_region("mission_reward")
                    continue
                cur["frames"] += 1
                miss = 0 if hdr else miss + 1
                if miss >= HDR_MISS_END:
                    cur["fallbacks"] = getattr(screen, "fallbacks", 0) - cur.pop("fb0")
                    cur["t_end"] = now
                    self.rounds.append(cur)
                    cur = None
                    continue
                if cur["t_normal"] is None:
                    dims = occ = 0                         # ช่องที่มีของ / ในนั้นกี่ช่องยังทึบ
                    for (x, y, w, h, _) in rects:
                        if ck._is_empty_slot(img, x, y, w, h, scale):
                            continue
                        occ += 1
                        dims += ck._slot_bg_v(img, (x, y, w, h, 0)) < ck.BG_READY_V
                    if dims and cur["t_dim"] is None:
                        cur["t_dim"] = now
                    elif occ and not dims and cur["t_dim"] is not None:
                        cur["t_normal"] = now              # ทุกช่องที่มีของเป็นสีปกติแล้ว (หลังเคยเห็นทึบ)
            self.fallbacks = getattr(screen, "fallbacks", 0)
            screen.close()
        except Exception as e:                         # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"
            self.ready.set()


def _make_wgc():
    from detector.wgc import WgcScreen
    return WgcScreen(config.CAPTURE_WINDOW)


def _stats(xs):
    if not xs:
        return "-"
    sd = st.pstdev(xs) if len(xs) > 1 else 0.0
    return f"n={len(xs)} เฉลี่ย {st.mean(xs):7.1f}  กลาง {st.median(xs):7.1f}  std {sd:6.1f}  min {min(xs):7.1f}  max {max(xs):7.1f}"


def match_rounds(a, b):
    """จับคู่รอบของสองวิธีตามลำดับเวลา (t_header ห่างกันไม่เกิน 1.5 วินาที)"""
    pairs, j = [], 0
    for ra in a:
        while j < len(b) and b[j]["t_header"] < ra["t_header"] - 1.5:
            j += 1
        if j < len(b) and abs(b[j]["t_header"] - ra["t_header"]) <= 1.5:
            pairs.append((ra, b[j]))
            j += 1
    return pairs


def summarize(w, m, out=print):
    pairs = match_rounds(w.rounds, m.rounds)
    out(f"--- สรุป: รอบที่จับได้ wgc={len(w.rounds)} mss={len(m.rounds)}  จับคู่ได้ {len(pairs)} รอบ ---")
    d_hdr = [(a["t_header"] - b["t_header"]) * 1000 for a, b in pairs]
    d_nrm = [(a["t_normal"] - b["t_normal"]) * 1000 for a, b in pairs if a["t_normal"] and b["t_normal"]]
    out(f"เห็นหัวข้อ Mission Reward  ส่วนต่าง wgc-mss (ms, ติดลบ=wgc เห็นก่อน): {_stats(d_hdr)}")
    out(f"เห็นช่องเป็นสีปกติ        ส่วนต่าง wgc-mss (ms, ติดลบ=wgc เห็นก่อน): {_stats(d_nrm)}")
    for name, rs in (("wgc", w.rounds), ("mss", m.rounds)):
        iv = [(r["t_normal"] - r["t_header"]) * 1000 for r in rs if r["t_normal"]]
        out(f"ระยะ หัวข้อ→สีปกติ ({name}) ms (เกมจริง ~4000 ยิ่ง std ต่ำยิ่งนิ่ง): {_stats(iv)}")
    out(f"เฟรมที่จับได้: wgc {w.frames} ({w.frames / max(1e-9, time.perf_counter() - w.t_start):.0f} fps)  "
        f"mss {m.frames} ({m.frames / max(1e-9, time.perf_counter() - m.t_start):.0f} fps)   "
        f"wgc ตกไปใช้ mss {sum(r.get('fallbacks', 0) for r in w.rounds)} เฟรม")
    return pairs


def main():
    argv = sys.argv[1:]
    want = int(argv[argv.index("--rounds") + 1]) if "--rounds" in argv else None
    stamp = time.strftime("%Y%m%d_%H%M%S")
    outdir = os.path.join(paths.OUTPUT, "capcmp", stamp)
    os.makedirs(outdir, exist_ok=True)
    logf = open(os.path.join(outdir, "log.txt"), "w", encoding="utf-8")

    def out(msg=""):
        print(msg, flush=True)
        logf.write(msg + "\n")
        logf.flush()

    w, m = Worker("wgc", _make_wgc), Worker("mss", Screen)
    w.start(); m.start()
    w.ready.wait(15); m.ready.wait(15)
    if w.error or m.error:
        out(f"เริ่มไม่ได้: wgc={w.error}  mss={m.error}")
        return
    out(f"[capcmp] เทียบ wgc vs mss พร้อมกัน  ผลเก็บที่ {os.path.relpath(outdir, paths.ROOT)}  "
        f"เล่นเกมผ่านหน้า Mission Result หลายๆ รอบ (หยุด: F12 ค้าง / Ctrl+C)")
    shown = 0
    try:
        while not (ck.esc_pressed() or (want and len(match_rounds(w.rounds, m.rounds)) >= want)):
            time.sleep(0.2)
            pairs = match_rounds(w.rounds, m.rounds)
            while shown < len(pairs):
                a, b = pairs[shown]
                shown += 1
                f = lambda r: "-" if r["t_normal"] is None else f"{(r['t_normal'] - r['t_header']) * 1000:6.0f}"
                out(f"รอบ {shown}: หัวข้อ wgc-mss {(a['t_header'] - b['t_header']) * 1000:+6.1f}ms | "
                    f"หัวข้อ→สีปกติ wgc {f(a)} mss {f(b)} ms | "
                    f"สีปกติ wgc-mss "
                    + ("-" if not (a['t_normal'] and b['t_normal']) else f"{(a['t_normal'] - b['t_normal']) * 1000:+6.1f}ms")
                    + f" | fallback wgc {a.get('fallbacks', 0)}")
    except KeyboardInterrupt:
        pass
    w.stop = m.stop = True
    time.sleep(0.5)
    pairs = summarize(w, m, out)
    with open(os.path.join(outdir, "rounds.csv"), "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["round", "backend", "t_header_s", "dim_ms", "normal_ms", "frames", "fallback_frames"])
        for name, rs in (("wgc", w.rounds), ("mss", m.rounds)):
            for i, r in enumerate(rs, 1):
                wr.writerow([i, name, f"{r['t_header']:.4f}",
                             "" if r["t_dim"] is None else f"{(r['t_dim'] - r['t_header']) * 1000:.1f}",
                             "" if r["t_normal"] is None else f"{(r['t_normal'] - r['t_header']) * 1000:.1f}",
                             r["frames"], r.get("fallbacks", 0)])
    out(f"บันทึก {os.path.relpath(outdir, paths.ROOT)}")


if __name__ == "__main__":
    main()
