"""
run_timed_only.py - ทดสอบ "ตั้งเวลา" อย่างเดียว (ไม่ใช่ production)

กดตาม CLICK_AT_MS ใน test_config.py (ตัวเลขเดียวหรือรายการ เช่น [3979, 3998]) ตรงที่เมาส์อยู่ แล้วไม่ทำอะไรอีก
ไม่มีกดตามสี / ป้าย GET! / ย้ายเมาส์ / คลิกสำรอง  ไว้วัดว่าเวลาที่ตั้งกดเข้าไหม โดยไม่ปนกับตัวอื่น
เมาส์เป็นของคุณ วางไว้ที่ไอเท็มก่อนหน้า Mission Reward ขึ้น  ตั้ง RECORD = True ใน test_config.py จะบันทึกวิดีโอ+สรุปว่าคลิกไหนเก็บติด

    python test_assist_and_react/ms_only/run_timed_only.py            กดจริง
    python test_assist_and_react/ms_only/run_timed_only.py --dry      ไม่คลิกจริง (ดูเวลาที่จะกด)
หยุด: F12 ค้าง / Ctrl+C
"""
import os
import random
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _setup import setup  # noqa: E402

config = setup("ms_only")                                           # ค่าจาก test_config.py ทับ config.py (ก่อน import detector)
from detector import clicker as ck  # noqa: E402
from detector.clicker import (HEADER_MATCH, LOCATE_EVERY, ClickAborted, _header_score, _loot_log, _loot_state,  # noqa: E402
                              esc_pressed, locate_header, tune_realtime)
from detector.collector import (HDR_EVERY, TIMED_LEAD, _fmt_times, _summarize_clicks, click_times, slot_rects,  # noqa: E402
                                wait_closed)
from detector.detection import get_scale, log  # noqa: E402
from detector.wgc import make_screen  # noqa: E402


def timed_round(screen, dry_run, record, round_no):
    scale = get_scale(screen)
    box = screen.get_region("mission_reward")
    rects = slot_rects(scale)
    down, up = ck.MOUSE_FLAGS["left"]

    last_locate = 0.0
    while True:                                                   # รอหัวข้อ Mission Reward
        if esc_pressed():
            raise ClickAborted(f"กด {ck.STOP_KEY_NAME} -> หยุด")
        img = screen.grab(box)
        _loot_log(img, scale)
        if _header_score(img, scale) >= HEADER_MATCH:
            break
        if time.perf_counter() - last_locate >= LOCATE_EVERY:
            last_locate = time.perf_counter()
            if locate_header(screen, scale):
                box = screen.get_region("mission_reward")
        time.sleep(0.002)
    t0 = _loot_state["since"] if _loot_state["visible"] else time.perf_counter()
    ms = lambda: (time.perf_counter() - t0) * 1000
    times = click_times()
    cap0 = getattr(screen, "fallbacks", 0)
    click_info = []
    log.info("=" * 60)
    log.info(f"[timed-only] เห็น Mission Reward  กดตั้งเวลาที่ {_fmt_times(times) or '(ไม่ได้ตั้ง)'}ms เท่านั้น (ไม่กดตามสี)"
             f"  จับภาพ={screen.backend}  กดค้าง {config.CLICK_HOLD_MS}ms" + ("  [dry_run]" if dry_run else "")
             + ("  [บันทึกวิดีโอ]" if record else ""))

    from detector.recorder import RoundRecorder
    rec = RoundRecorder(scale, box, rects, t0, round_no) if record else None

    def slot_at(cur):
        x, y = cur[0] - box[0], cur[1] - box[1]
        for i, (sx, sy, sw, sh, _) in enumerate(rects):
            if sx <= x <= sx + sw and sy <= y <= sy + sh:
                return i
        return None

    done, groups, timed_i = 0, 0, 0
    due = t0 + times[0] / 1000 if times else None
    fired_all = not times
    miss, last_hdr = 0, 0.0
    try:
        while True:
            if esc_pressed():
                raise ClickAborted(f"กด {ck.STOP_KEY_NAME} -> หยุด")
            img = screen.grab(box)
            now = time.perf_counter()
            if rec is not None:
                rec.add(img, ck.get_cursor())
            _loot_log(img, scale)
            if now - last_hdr >= HDR_EVERY:
                last_hdr = now
                miss = 0 if _header_score(img, scale) >= HEADER_MATCH else miss + 1
                if miss >= ck.HEADER_LOST_FRAMES:
                    log.info(f"  {ms():7.1f}ms  Mission Reward หายไปแล้ว -> จบรอบ (กดไป {done} ครั้ง)")
                    return done
            if fired_all or now < due - TIMED_LEAD:
                continue
            batch = [times[timed_i]]                              # เวลาที่ห่างกันไม่เกิน 60 ms กดต่อเนื่องในรอบเดียว (เหมือน production)
            while timed_i + len(batch) < len(times) and times[timed_i + len(batch)] - batch[0] <= 60:
                batch.append(times[timed_i + len(batch)])
            timed_i += len(batch)
            fired_all = timed_i >= len(times)
            if not fired_all:
                due = t0 + times[timed_i] / 1000
            if now > t0 + batch[0] / 1000 + 0.05:
                log.info(f"  {ms():7.1f}ms  เลยเวลา {_fmt_times(batch)}ms ไปแล้ว -> ข้าม")
                continue
            if _header_score(screen.grab(box), scale) < HEADER_MATCH:
                log.info(f"  {ms():7.1f}ms  ไม่เห็นหัวข้อก่อนกด -> ไม่กด")
                continue
            groups += 1
            fired = []
            for t_ms in batch:
                target = t0 + t_ms / 1000
                while time.perf_counter() < target:
                    time.sleep(0)
                tp = time.perf_counter()
                if not dry_run:
                    ck._send_mouse(down)
                    time.sleep(random.uniform(*ck.CLICK_HOLD))
                    ck._send_mouse(up)
                done += 1
                tt = (tp - t0) * 1000
                if rec is not None:
                    rec.mark_click(done, tt, groups)
                fired.append((done, tt, t_ms, slot_at(ck.get_cursor())))
            for n, tt, t_ms, sl in fired:
                click_info.append((n, tt, f"ตั้งเวลา {t_ms:g}", sl))
                x, y = ck.get_cursor()
                log.info(f"  {tt:7.1f}ms  กดตั้งเวลา (คลิกที่ {n}) (ตั้งไว้ {t_ms:g}ms)  screen=({x}, {y})")
    finally:
        if rec is not None:
            rec.finish(log)
        _summarize_clicks(screen, click_info, rec.verdicts if rec is not None else None, cap0)


def main():
    dry_run = "--dry" in sys.argv
    record = bool(config.RECORD)
    times = click_times()
    screen = make_screen()
    tuned = tune_realtime()
    log.info("#" * 60)
    log.info(f"[timed-only] เริ่มทำงาน กดที่ {_fmt_times(times) or '(ไม่ได้ตั้ง CLICK_AT_MS)'}ms อย่างเดียว  dry_run={dry_run}  "
             f"จับภาพ={screen.backend}  {tuned}  กด {ck.STOP_KEY_NAME} ค้าง/Ctrl+C เพื่อหยุด")
    rounds, total, errors = 0, 0, 0
    while True:
        try:
            n = timed_round(screen, dry_run, record, rounds + 1)
            rounds += 1
            total += n
            log.info(f"[timed-only] จบรอบที่ {rounds}  คลิก {n}  (รวม {total})")
            wait_closed(screen)
        except ClickAborted as e:
            log.info(f"[timed-only] หยุดทำงาน ({e})  {rounds} รอบ  คลิกรวม {total}")
            break
        except KeyboardInterrupt:
            log.info(f"[timed-only] หยุดทำงาน (Ctrl+C)  {rounds} รอบ  คลิกรวม {total}")
            break
        except Exception:
            errors += 1
            log.info(f"[timed-only] เกิด error ครั้งที่ {errors} -> ทำงานต่อใน {config.ERROR_RETRY_DELAY:.0f} วินาที\n"
                     + traceback.format_exc())
            try:
                time.sleep(config.ERROR_RETRY_DELAY)
                screen.close()
                screen = make_screen()
            except Exception:
                pass
    screen.close()


if __name__ == "__main__":
    main()
