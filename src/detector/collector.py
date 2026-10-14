"""
collector.py - โหมด "เก็บทุกช่องตามเวลา" (python main.py collect)

ไม่ใช้การ detect ไอเท็ม (ไม่มี template matching ของไอเท็ม) จึงไม่มี false negative:
    1. รอหัวข้อ Mission Reward (นับเวลาจากเฟรมแรกที่เห็น เหมือนโหมดปกติ)
    2. รอไอเท็มขึ้นครบ (ช่องที่ไม่ว่าง = มีของ ตรวจแค่ "ช่องว่างหรือไม่") แล้ววางเมาส์รอที่ช่องแรก
    3. ถึงเวลา CLICK_AT_MS (config.py) -> กดช่องแรก แล้วไล่กดช่องที่เหลือตามลำดับ ซ้าย->ขวา บน->ล่าง
    4. ตรวจซ้ำ: ช่องไหนยังไม่ว่าง กดอีกรอบ (สูงสุด COLLECT_MAX_PASSES)
    5. บันทึกภาพ loot (ตอนทึบ + ตอนสีปกติ) ลง output/loot เหมือนเดิม  -> ใช้ crop ตัดรูปไอเท็มต่อได้
กดทุกอย่างที่อยู่ในกรอบ = เก็บของที่ไม่ต้องการไปด้วย
"""

import random
import time
import traceback

import config

from . import clicker as ck
from .clicker import (GRID_ORIGIN, GRID_PITCH, GRID_SIZE, HEADER_MATCH, SLOT_WH, ClickAborted, _header_score,
                      _hover_point, _is_empty_slot, _loot_log, _loot_state, esc_pressed, move_to, real_move,
                      tune_realtime)
from .detection import get_scale, log
from .screen import Screen

TIMED_LEAD = ck.TIMED_LEAD
STABLE_FOR = 0.15          # จำนวนช่องที่มีของต้องนิ่งนานเท่านี้ (วินาที) ถึงถือว่าไอเท็มขึ้นครบ
VERIFY_AFTER = 0.30        # กดครบแล้วรอเท่านี้ก่อนตรวจว่าช่องว่างหมดหรือยัง (เกมตอบสนองหลังปล่อยปุ่ม ~20-120 ms)
HDR_EVERY = 0.04           # ช่วงรอ: เช็กหัวข้อทุกกี่วินาที (ก่อนถึงเวลากดจะเช็กสดอีกครั้งเสมอ)


def slot_rects(scale):
    """ช่องทั้งหมด (x, y, w, h, 0) ในพิกัดภาพ region เรียงซ้าย->ขวา บน->ล่าง"""
    (ox, oy), (px, py), (nc, nr), (sw, sh) = GRID_ORIGIN, GRID_PITCH, GRID_SIZE, SLOT_WH
    return [(int(round(5 + (ox + px * c) * scale)), int(round(5 + (oy + py * r) * scale)),
             int(sw * scale), int(sh * scale), 0) for r in range(nr) for c in range(nc)]


def _occupied(img, scale, rects):
    return [i for i, (x, y, w, h, _) in enumerate(rects) if not _is_empty_slot(img, x, y, w, h, scale)]


def collect_round(screen, dry_run=False):
    """1 รอบ (1 หน้า Mission Result)  คืนจำนวนคลิก"""
    scale = get_scale(screen)
    box = screen.get_region("mission_reward")
    rects = slot_rects(scale)
    down, up = ck.MOUSE_FLAGS["left"]
    clicked = 0

    # ---- 1) รอหัวข้อ Mission Reward ----
    while True:
        if esc_pressed():
            raise ClickAborted(f"กด {ck.STOP_KEY_NAME} -> หยุด")
        img = screen.grab(box)
        _loot_log(img, scale)
        if _header_score(img, scale) >= HEADER_MATCH:
            break
        time.sleep(0.002)
    t0 = _loot_state["since"] if _loot_state["visible"] else time.perf_counter()
    due = t0 + (config.CLICK_AT_MS or 0) / 1000
    ms = lambda: (time.perf_counter() - t0) * 1000
    log.info("=" * 60)
    log.info(f"[collect] เห็น Mission Reward  จะกดที่ {config.CLICK_AT_MS}ms  กดค้าง {config.CLICK_HOLD_MS}ms"
             f"  ระยะห่างระหว่างช่อง {config.COLLECT_GAP_MS}ms{'  [dry_run]' if dry_run else ''}")

    state = {"miss": 0, "last_hdr": 0.0}

    def check_frame(frame):
        """ทุกเฟรมระหว่างรอ/ขยับ: บันทึกภาพ loot + เช็กหัวข้อ  คืน False ถ้าหน้าจอปิดไปแล้ว"""
        _loot_log(frame, scale)
        now = time.perf_counter()
        if now - state["last_hdr"] >= HDR_EVERY:
            state["last_hdr"] = now
            state["miss"] = 0 if _header_score(frame, scale) >= HEADER_MATCH else state["miss"] + 1
        return state["miss"] < ck.HEADER_LOST_FRAMES

    step_n = [0]

    def step():
        """ระหว่างขยับเมาส์: จับภาพบางก้าว (ไว้บันทึก loot ตอนสีปกติ) คืน True = หยุดขยับ"""
        step_n[0] += 1
        if step_n[0] % ck.STEP_CHECK_EVERY:
            return False
        return not check_frame(screen.grab(box))

    # ---- 2) รอไอเท็มขึ้นครบ ----
    occ, since_same, last_probe = [], None, 0.0
    while True:
        if esc_pressed():
            raise ClickAborted(f"กด {ck.STOP_KEY_NAME} -> หยุด")
        img = screen.grab(box)
        if not check_frame(img):
            log.info(f"  {ms():7.0f}ms  หัวข้อ Mission Reward หายก่อนไอเท็มขึ้น -> เลิกรอบนี้")
            return clicked
        now = time.perf_counter()
        if now - last_probe >= 0.05:
            last_probe = now
            cur = _occupied(img, scale, rects)
            if cur != occ:
                occ, since_same = cur, now
            elif occ and now - since_same >= STABLE_FOR:
                break                                      # ไอเท็มขึ้นครบและนิ่งแล้ว
        time.sleep(0.002)

    slots = [rects[i] for i in occ]
    log.info(f"  {ms():7.0f}ms  ไอเท็มขึ้นแล้ว {len(slots)} ช่อง: " + ",".join(str(i + 1) for i in occ))

    def point(slot):
        return _hover_point(slot, box)

    # ---- 3) วางเมาส์รอที่ช่องแรก ----
    hx, hy = point(slots[0])
    if not dry_run:
        move_to(hx, hy, on_step=step, relaxed=ck.HUMAN_HOVER and time.perf_counter() < due - 0.5)
        real_move(hx, hy)
    log.info(f"  {ms():7.0f}ms  วางเมาส์รอที่ช่อง {occ[0] + 1} screen=({hx}, {hy})")

    # ---- 4) ถึงเวลา -> กดช่องแรก ----
    while time.perf_counter() < due - TIMED_LEAD:
        if not check_frame(screen.grab(box)):
            log.info(f"  {ms():7.0f}ms  หัวข้อ Mission Reward หายก่อนถึงเวลากด -> เลิกรอบนี้ ไม่คลิก")
            return clicked
    if _header_score(screen.grab(box), scale) < HEADER_MATCH:      # เช็กสดก่อนกดทุกครั้ง
        log.info(f"  {ms():7.0f}ms  ไม่เห็นหัวข้อก่อนกด -> ไม่คลิก")
        return clicked
    while time.perf_counter() < due:
        time.sleep(0)

    def click_at_point(x, y, label):
        nonlocal clicked
        t = ms()
        if not dry_run:
            real_move(x, y)
            time.sleep(ck.REAL_MOVE_DWELL)
            ck._send_mouse(down)
            time.sleep(random.uniform(*ck.CLICK_HOLD))
            ck._send_mouse(up)
        clicked += 1
        log.info(f"  {t:7.0f}ms  กด {label}  screen=({x}, {y})")

    click_at_point(hx, hy, f"ช่อง {occ[0] + 1} (ตั้งเวลา)")

    # ---- 5) ไล่กดช่องที่เหลือ ----
    def sweep(indices):
        for k in indices:
            if esc_pressed():
                raise ClickAborted(f"กด {ck.STOP_KEY_NAME} -> หยุด")
            time.sleep(config.COLLECT_GAP_MS / 1000)
            x, y = point(slots[k])
            if not dry_run and move_to(x, y, on_step=step):
                log.info(f"  {ms():7.0f}ms  หัวข้อ Mission Reward หายระหว่างขยับ -> หยุด")
                return False
            click_at_point(x, y, f"ช่อง {occ[k] + 1}")
        return True

    if not sweep(range(1, len(slots))):
        return clicked

    # ---- 6) ตรวจซ้ำ: ช่องไหนยังไม่ว่าง กดอีก ----
    for p in range(2, config.COLLECT_MAX_PASSES + 1):
        if dry_run:
            break
        end = time.perf_counter() + VERIFY_AFTER
        while time.perf_counter() < end:
            if not check_frame(screen.grab(box)):
                return clicked
        frame = screen.grab(box)
        left = [k for k, s in enumerate(slots) if not _is_empty_slot(frame, s[0], s[1], s[2], s[3], scale)]
        if not left:
            log.info(f"  {ms():7.0f}ms  ช่องว่างครบ -> เก็บเสร็จ ({clicked} คลิก)")
            return clicked
        log.info(f"  {ms():7.0f}ms  ยังไม่ว่าง {len(left)} ช่อง: {','.join(str(occ[k] + 1) for k in left)} -> กดรอบที่ {p}")
        if not sweep(left):
            return clicked
    log.info(f"  {ms():7.0f}ms  จบรอบ ({clicked} คลิก)")
    return clicked


def wait_closed(screen, interval=0.2, confirm=3):
    """รอจนหัวข้อ Mission Reward หาย ติดกัน confirm ครั้ง (บันทึกภาพ loot ตอนสีปกติระหว่างรอ)"""
    scale = get_scale(screen)
    box = screen.get_region("mission_reward")
    misses = 0
    while misses < confirm:
        if esc_pressed():
            raise ClickAborted(f"กด {ck.STOP_KEY_NAME} -> หยุด")
        img = screen.grab(box)
        _loot_log(img, scale)
        misses = misses + 1 if _header_score(img, scale) < HEADER_MATCH else 0
        if misses < confirm:
            time.sleep(interval)
    log.info("  หน้า Mission Result ปิดแล้ว")


def run_collect(argv):
    dry_run = "--dry" in argv
    if config.CLICK_AT_MS is None:
        print("โหมดนี้ต้องใช้เวลากด: ตั้ง CLICK_AT_MS ใน config.py (เช่น 3988) ก่อน")
        return
    screen = Screen()
    tuned = tune_realtime()
    w, h = screen.get_screen_rect()[2:4]
    log.info("#" * 60)
    log.info(f"[collect] เริ่มทำงาน (เก็บทุกช่อง ไม่ใช้ detect ไอเท็ม)  dry_run={dry_run}  จอ={w}x{h}  "
             f"กดที่ {config.CLICK_AT_MS}ms  กดค้าง {config.CLICK_HOLD_MS}ms  {tuned}  กด {ck.STOP_KEY_NAME} ค้าง/Ctrl+C เพื่อหยุด")
    rounds, total, errors = 0, 0, 0
    while True:
        try:
            n = collect_round(screen, dry_run)
            rounds += 1
            total += n
            log.info(f"[collect] จบรอบที่ {rounds}  คลิก {n}  (รวม {total})")
            wait_closed(screen)
        except ClickAborted as e:
            log.info(f"[collect] หยุดทำงาน ({e})  {rounds} รอบ  คลิกรวม {total}  error {errors} ครั้ง")
            break
        except KeyboardInterrupt:
            log.info(f"[collect] หยุดทำงาน (Ctrl+C)  {rounds} รอบ  คลิกรวม {total}  error {errors} ครั้ง")
            break
        except Exception:
            errors += 1
            log.info(f"[collect] เกิด error ครั้งที่ {errors} -> ทำงานต่อใน {config.ERROR_RETRY_DELAY:.0f} วินาที\n"
                     + traceback.format_exc())
            try:
                time.sleep(config.ERROR_RETRY_DELAY)
                screen = Screen()
            except KeyboardInterrupt:
                break


# ---------------------------------------------------------------------------
# โหมดช่วยกด (python main.py assist)  คุณคุมเมาส์/เลือกช่องเอง บอทแค่กดคลิกให้ตรงจังหวะ
# ---------------------------------------------------------------------------

def assist_round(screen, at_ms, clicks, dry_run=False, record=False, round_no=1):
    """1 รอบ: รอหัวข้อ Mission Reward -> ที่ at_ms กดคลิกตรงที่เมาส์อยู่ (ไม่ขยับเมาส์ ไม่เลือกช่อง)
    clicks > 1 = กดซ้ำทุก config.RETRY_AFTER_MS  record = บันทึกวิดีโอ + วิเคราะห์ timeline (RoundRecorder)
    คืนจำนวนคลิก"""
    scale = get_scale(screen)
    box = screen.get_region("mission_reward")
    down, up = ck.MOUSE_FLAGS["left"]

    while True:                                                   # รอหัวข้อ (ทุกเฟรม) + บันทึกภาพ loot
        if esc_pressed():
            raise ClickAborted(f"กด {ck.STOP_KEY_NAME} -> หยุด")
        img = screen.grab(box)
        _loot_log(img, scale)
        if _header_score(img, scale) >= HEADER_MATCH:
            break
        time.sleep(0.002)
    t0 = _loot_state["since"] if _loot_state["visible"] else time.perf_counter()
    ms = lambda: (time.perf_counter() - t0) * 1000
    times = [at_ms + k * config.RETRY_AFTER_MS for k in range(clicks)]
    log.info("=" * 60)
    log.info(f"[assist] เห็น Mission Reward  จะกดที่ " + ", ".join(f"{t:.0f}" for t in times)
             + f" ms  กดค้าง {config.CLICK_HOLD_MS}ms  (เมาส์คุณเอง ไม่ขยับให้){'  [dry_run]' if dry_run else ''}"
             + ("  [บันทึกวิดีโอ]" if record else ""))

    from .recorder import RoundRecorder
    rec = RoundRecorder(scale, box, slot_rects(scale), t0, round_no) if record else None

    def grab():
        frame = screen.grab(box)
        if rec is not None:
            rec.add(frame, ck.get_cursor())
        return frame

    state = {"miss": 0, "last_hdr": 0.0}

    def alive(frame):
        _loot_log(frame, scale)
        now = time.perf_counter()
        if now - state["last_hdr"] >= HDR_EVERY:
            state["last_hdr"] = now
            state["miss"] = 0 if _header_score(frame, scale) >= HEADER_MATCH else state["miss"] + 1
        return state["miss"] < ck.HEADER_LOST_FRAMES

    done = 0
    try:
        for k, at in enumerate(times):
            due = t0 + at / 1000
            while time.perf_counter() < due - TIMED_LEAD:
                if esc_pressed():
                    raise ClickAborted(f"กด {ck.STOP_KEY_NAME} -> หยุด")
                if not alive(grab()):
                    log.info(f"  {ms():7.0f}ms  หัวข้อ Mission Reward หายก่อนถึงเวลากด -> ไม่คลิก")
                    return done
            if _header_score(grab(), scale) < HEADER_MATCH:               # เช็กสดก่อนกดทุกครั้ง
                log.info(f"  {ms():7.0f}ms  ไม่เห็นหัวข้อก่อนกด -> ไม่คลิก")
                return done
            x, y = ck.get_cursor()
            if not (box[0] <= x <= box[2] and box[1] <= y <= box[3]):      # กันคลิกนอกกรอบรางวัลโดยไม่ตั้งใจ
                log.info(f"  {ms():7.0f}ms  เมาส์อยู่นอกกรอบรางวัล ({x}, {y}) -> ไม่คลิก (วางเมาส์บนช่องที่ต้องการก่อน)")
                return done
            while time.perf_counter() < due:
                time.sleep(0)
            t = ms()
            if not dry_run:
                ck._send_mouse(down)
                time.sleep(random.uniform(*ck.CLICK_HOLD))
                ck._send_mouse(up)
            done += 1
            if rec is not None:
                rec.mark_click(k + 1, t)
            log.info(f"  {t:7.0f}ms  กดครั้งที่ {k + 1} ตรงที่เมาส์อยู่ screen=({x}, {y})  (ตั้งไว้ {at:.0f}ms)")
        if rec is not None:                                              # หลังคลิกครั้งสุดท้าย บันทึกต่ออีก ~0.8 วินาที (ดูช่องทึบแว้บ/ว่าง)
            end = time.perf_counter() + 0.8
            while time.perf_counter() < end:
                if esc_pressed():
                    raise ClickAborted(f"กด {ck.STOP_KEY_NAME} -> หยุด")
                if not alive(grab()):
                    break
        return done
    finally:
        if rec is not None:
            rec.finish(log)


def run_assist(argv):
    dry_run = "--dry" in argv
    once = "--once" in argv
    record = "--record" in argv
    nums = [a for a in argv if not a.startswith("--")]
    at_ms = float(nums[0]) if nums else config.CLICK_AT_MS
    if at_ms is None:
        print("ใส่เวลากด (ms) เช่น  python main.py assist 3988  หรือตั้ง CLICK_AT_MS ใน config.py")
        return
    clicks = 1 if once else max(1, config.MAX_CLICKS_PER_ITEM)
    screen = Screen()
    tuned = tune_realtime()
    log.info("#" * 60)
    log.info(f"[assist] เริ่มทำงาน (คุณคุมเมาส์ บอทกดให้ตรงจังหวะ)  dry_run={dry_run}  กดที่ {at_ms:.0f}ms  "
             f"{clicks} ครั้ง ห่าง {config.RETRY_AFTER_MS}ms  กดค้าง {config.CLICK_HOLD_MS}ms  {tuned}  "
             f"กด {ck.STOP_KEY_NAME} ค้าง/Ctrl+C เพื่อหยุด")
    rounds, total, errors = 0, 0, 0
    while True:
        try:
            n = assist_round(screen, at_ms, clicks, dry_run, record, rounds + 1)
            rounds += 1
            total += n
            log.info(f"[assist] จบรอบที่ {rounds}  คลิก {n}  (รวม {total})")
            wait_closed(screen)
        except ClickAborted as e:
            log.info(f"[assist] หยุดทำงาน ({e})  {rounds} รอบ  คลิกรวม {total}  error {errors} ครั้ง")
            break
        except KeyboardInterrupt:
            log.info(f"[assist] หยุดทำงาน (Ctrl+C)  {rounds} รอบ  คลิกรวม {total}  error {errors} ครั้ง")
            break
        except Exception:
            errors += 1
            log.info(f"[assist] เกิด error ครั้งที่ {errors} -> ทำงานต่อใน {config.ERROR_RETRY_DELAY:.0f} วินาที\n"
                     + traceback.format_exc())
            try:
                time.sleep(config.ERROR_RETRY_DELAY)
                screen = Screen()
            except KeyboardInterrupt:
                break
