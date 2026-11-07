"""
run_detect_ab.py - เทียบ "โค้ดเก่า vs โค้ดใหม่" ของ assist <ชื่อ/โปรไฟล์> บนเกมจริง + หาเวลากดตั้งเวลาที่ต่ำสุดที่ยังเก็บติด (ไม่ใช่ production)

แบบโค้ดที่เทียบ (--modes):
    old       หาไอเท็มแบบเก่า (ทุก 50 ms เทียบทั้งภาพ ในลูปหลัก) + เช็กหัวข้อก่อนกดด้วยการจับภาพใหม่
    new       แบบ production ตอนนี้ (เธรดแยก + เทียบเฉพาะ 21 ช่อง)
    new_fast  เหมือน new แต่เช็กหัวข้อก่อนกดตั้งเวลาจากเฟรมเดิม (ไม่รอเฟรมใหม่)

    python test_assist_and_react/detect_ab/run_detect_ab.py 2_4 --single --times 3975,3980,3985 --rounds 15
        --single   กดตามเวลาเท่านั้น คลิกเดียว อิงไอเท็มชิ้นเดียว (ชิ้นแรกตามลำดับความสำคัญ) ไม่กดตามสี ไม่ไปชิ้นอื่น
        --times    เวลากดที่จะลอง (ms) หลายค่า: สลับสุ่มรอบละค่า  ไม่ใส่ = ใช้ CLICK_AT_MS จาก test_config.py
        --rounds N หยุดเองเมื่อครบ N รอบ แล้วสรุปให้ (ไม่ใส่ = วนจนกด F12 / Ctrl+C)
        --modes    old,new,new_fast  เลือกแบบที่จะเทียบ (ไม่ใส่ = ทั้งสามแบบ)   --dry = ไม่คลิกจริง

สรุปหลังจบ: เก็บติดกี่ครั้งต่อ (แบบ, เวลา)  คลิกแรกกดจริงช้ากว่าที่ตั้งกี่ ms  เจอไอเท็มที่กี่ ms  ตรวจรอบละกี่ ms  + เวลาที่ต่ำสุดที่เก็บติดทุกรอบ
ค่าตั้งอยู่ที่ test_assist_and_react/test_config.py   ผลอยู่ที่ output/test/detect_ab/<เวลา>/ (test.log, วิดีโอแต่ละรอบ, summary.csv)
หยุด: F12 ค้าง / Ctrl+C
"""
import csv
import os
import random
import statistics
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _setup import setup  # noqa: E402

config = setup("detect_ab")                                # ค่าจาก test_config.py ทับ config.py + แยก log/record ไว้ที่ output/test/detect_ab/
from detector import clicker as ck  # noqa: E402
from detector.clicker import ClickAborted, tune_realtime  # noqa: E402
from detector.collector import _fmt_times, click_times, wait_closed  # noqa: E402
from detector.detection import log  # noqa: E402
from detector.profiles import list_profiles, resolve  # noqa: E402
from detector.recorder import RoundRecorder  # noqa: E402
from detector.wgc import make_screen  # noqa: E402

try:
    import variant_round as vr
except ImportError:
    sys.exit("ยังไม่มี variant_round.py  -> รัน: python test_assist_and_react/detect_ab/make_variant.py")

ALL_MODES = ("old", "new", "new_fast")


def avg(vals, fmt="{:7.1f}"):
    vals = [v for v in vals if v is not None]
    return fmt.format(statistics.mean(vals)) if vals else "      -"


def first_late(x):
    """คลิกแรก (ตั้งเวลา) กดจริงช้ากว่าเวลาที่ตั้งกี่ ms"""
    if x["first_click_ms"] is None or not x["timed"] or not x["first_method"] or "ตั้งเวลา" not in x["first_method"]:
        return None
    return x["first_click_ms"] - x["timed"][0]


def summary(rows, final=False):
    log.info("=" * 90)
    log.info(f"[สรุป detect_ab] {len(rows)} รอบ" + ("  (สรุปสุดท้าย)" if final else "")
             + "   เจอไอเท็ม = ms หลังหัวข้อขึ้น | ตรวจ/รอบ = เวลาต่อการตรวจ 1 ครั้ง | คลิกแรกช้า = กดจริง − เวลาที่ตั้ง | ว่างหลังคลิก = ช่องแรกว่างหลังคลิกกี่ ms")
    log.info(f"{'แบบ':9s} {'ตั้งไว้ms':>9s} {'รอบ':>4s} {'เก็บติด':>7s} {'คลิกแรกช้า':>10s} {'เจอไอเท็ม':>9s} {'ตรวจ/รอบ':>9s} {'ว่างหลังคลิก':>12s}")
    keys = sorted({(x["mode"], x["timed"][0] if x["timed"] else -1) for x in rows}, key=lambda k: (ALL_MODES.index(k[0]), k[1]))
    for mode, t in keys:
        r = [x for x in rows if x["mode"] == mode and (x["timed"][0] if x["timed"] else -1) == t]
        ok = [x for x in r if x["first_ok"] is not None]
        got = f"{sum(1 for x in ok if x['first_ok'])}/{len(ok)}" if ok else "-"
        log.info(f"{mode:9s} {t:9.0f} {len(r):4d} {got:>7s} {avg([first_late(x) for x in r]):>10s} {avg([x['found_ms'] for x in r]):>9s} "
                 f"{avg([x['pass_ms'] for x in r]):>9s} {avg([x['first_empty_after_ms'] for x in r]):>12s}")
    # เวลาตั้งต่ำสุดที่เก็บติดทุกรอบ (รวมทุกแบบ)
    by_t = {}
    for x in rows:
        if x["timed"] and x["first_ok"] is not None:
            by_t.setdefault(x["timed"][0], []).append(bool(x["first_ok"]))
    if len(by_t) > 1:
        log.info("[เวลาที่ตั้ง → เก็บติด]  " + "   ".join(f"{t:.0f}: {sum(v)}/{len(v)}" for t, v in sorted(by_t.items())))
        ok_all = [t for t, v in by_t.items() if all(v) and len(v) >= 2]
        if ok_all:
            log.info(f"[เวลาตั้งต่ำสุดที่เก็บติดทุกรอบ (อย่างน้อย 2 รอบ)] {min(ok_all):.0f} ms   (ข้อมูลน้อยมาก ควรรันหลายรอบต่อค่า)")
    path = os.path.join(RoundRecorder.base_dir, "summary.csv")
    cols = ["round", "mode", "single", "timed", "found_ms", "passes", "pass_ms", "frame_to_found_ms", "first_click_ms", "first_method",
            "first_ok", "first_empty_after_ms"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    log.info(f"[สรุป detect_ab] บันทึก {os.path.relpath(path)}")


def opt(argv, name):
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else None


def main():
    argv = sys.argv[1:]
    dry, single = "--dry" in argv, "--single" in argv
    modes = [m for m in (opt(argv, "--modes") or "").split(",") if m in ALL_MODES] or list(ALL_MODES)
    times = [float(t) for t in (opt(argv, "--times") or "").split(",") if t.strip()] or None
    max_rounds = int(opt(argv, "--rounds") or 0)
    valued = {"--modes", "--times", "--rounds"}
    args = [a for i, a in enumerate(argv) if not a.startswith("--") and (i == 0 or argv[i - 1] not in valued)]
    if not args:
        sys.exit("ใส่ชื่อไอเท็ม/โปรไฟล์ด้วย เช่น  python test_assist_and_react/detect_ab/run_detect_ab.py 2_4 --single --times 3975,3980 --rounds 10")
    info = resolve(args)
    for e in info["errors"]:
        print(f"ผิดพลาด: {e}")
    if info["errors"] or not info["names"]:
        print(f"ที่มี: {info['available']}   โปรไฟล์ที่มี: {list_profiles() or '(ยังไม่มี)'}")
        return
    names = info["names"]
    record = bool(config.RECORD)
    screen = make_screen()
    tuned = tune_realtime()
    plan_times = times or [None]
    log.info("#" * 60)
    log.info(f"[detect_ab] เริ่ม  แบบที่เทียบ: {', '.join(modes)}  ไอเท็ม: {', '.join(names)}  "
             f"กดตามเวลา {_fmt_times(times) if times else (_fmt_times(click_times()) or '-')}ms  "
             f"{'[single: ms เท่านั้น คลิกเดียว ไอเท็มชิ้นเดียว]' if single else ''}  "
             f"{'หยุดเองเมื่อครบ ' + str(max_rounds) + ' รอบ  ' if max_rounds else ''}"
             f"dry_run={dry}  record={record}  จับภาพ={screen.backend}  {tuned}  กด {ck.STOP_KEY_NAME} ค้าง/Ctrl+C เพื่อหยุด")
    rows, order, rounds = [], [], 0
    try:
        while not (max_rounds and rounds >= max_rounds):
            if not order:
                order = [(m, t) for m in modes for t in plan_times]
                random.shuffle(order)
            mode, t = order.pop()
            try:
                log.info(f"[detect_ab] รอบที่ {rounds + 1}{'/' + str(max_rounds) if max_rounds else ''}  แบบ = {mode}"
                         + (f"  ตั้งเวลา = {t:.0f}ms" if t else ""))
                n = vr.assist_round_variant(screen, dry, record, rounds + 1, names, mode, single, [t] if t else None)
                rounds += 1
                row = dict(vr.assist_round_variant.metrics, round=rounds)
                rows.append(row)
                late = first_late(row)
                log.info(f"[detect_ab] จบรอบที่ {rounds} ({mode})  คลิก {n}  เจอไอเท็ม {row['found_ms'] and round(row['found_ms'])}ms  "
                         f"ตรวจรอบละ {row['pass_ms']:.1f}ms  คลิกแรก {row['first_click_ms'] and round(row['first_click_ms'], 1)}ms"
                         + (f" (ช้ากว่าที่ตั้ง {late:.1f}ms)" if late is not None else "") + f"  เก็บติด={row['first_ok']}")
                summary(rows)
                wait_closed(screen)
            except ClickAborted as e:
                log.info(f"[detect_ab] หยุด ({e})")
                break
            except Exception:
                log.info("[detect_ab] error:\n" + traceback.format_exc())
                time.sleep(config.ERROR_RETRY_DELAY)
    except KeyboardInterrupt:
        log.info("[detect_ab] หยุด (Ctrl+C)")
    finally:
        if rows:
            summary(rows, final=True)
        screen.close()


if __name__ == "__main__":
    main()
