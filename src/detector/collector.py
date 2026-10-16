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
from .clicker import (BG_READY_V, GET_MATCH, LOCATE_EVERY, _get_score, _slot_bg_v, locate_header, GRID_ORIGIN, GRID_PITCH, GRID_SIZE, HEADER_MATCH, SLOT_WH, ClickAborted, _header_score,
                      _hover_point, _is_empty_slot, _loot_log, _loot_state, esc_pressed, move_to, real_move,
                      tune_realtime)
from .detection import get_scale, log
from .wgc import make_screen

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
    last_locate = 0.0
    while True:
        if esc_pressed():
            raise ClickAborted(f"กด {ck.STOP_KEY_NAME} -> หยุด")
        img = screen.grab(box)
        _loot_log(img, scale)
        if _header_score(img, scale) >= HEADER_MATCH:
            break
        if time.perf_counter() - last_locate >= LOCATE_EVERY:           # หัวข้อไม่อยู่ในกรอบ: ลองหาในพื้นที่กว้างขึ้น
            last_locate = time.perf_counter()
            if locate_header(screen, scale):
                box = screen.get_region("mission_reward")
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
    screen = make_screen()
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
                screen.close()
                screen = make_screen()
            except KeyboardInterrupt:
                break


# ---------------------------------------------------------------------------
# โหมดช่วยกด (python main.py assist)  คุณคุมเมาส์/เลือกช่องเอง บอทแค่กดคลิกให้ตรงจังหวะ
# ---------------------------------------------------------------------------

def assist_round(screen, at_ms, clicks, dry_run=False, record=False, round_no=1, times=None):
    """1 รอบ: รอหัวข้อ Mission Reward -> ที่ at_ms กดคลิกตรงที่เมาส์อยู่ (ไม่ขยับเมาส์ ไม่เลือกช่อง)
    clicks > 1 = กดซ้ำทุก config.RETRY_AFTER_MS  record = บันทึกวิดีโอ + วิเคราะห์ timeline (RoundRecorder)
    คืนจำนวนคลิก"""
    scale = get_scale(screen)
    box = screen.get_region("mission_reward")
    down, up = ck.MOUSE_FLAGS["left"]

    last_locate = 0.0
    while True:                                                   # รอหัวข้อ (ทุกเฟรม) + บันทึกภาพ loot
        if esc_pressed():
            raise ClickAborted(f"กด {ck.STOP_KEY_NAME} -> หยุด")
        img = screen.grab(box)
        _loot_log(img, scale)
        if _header_score(img, scale) >= HEADER_MATCH:
            break
        if time.perf_counter() - last_locate >= LOCATE_EVERY:           # หัวข้อไม่อยู่ในกรอบ: ลองหาในพื้นที่กว้างขึ้น
            last_locate = time.perf_counter()
            if locate_header(screen, scale):
                box = screen.get_region("mission_reward")
        time.sleep(0.002)
    t0 = _loot_state["since"] if _loot_state["visible"] else time.perf_counter()
    ms = lambda: (time.perf_counter() - t0) * 1000
    times = list(times) if times else [at_ms + k * config.RETRY_AFTER_MS for k in range(clicks)]
    assist_round.verdicts = []
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
    iter_ms = 8.0                                                         # เวลาต่อรอบของลูปรอ (EMA) ไว้ตั้งลีดก่อนถึงเวลากด
    try:
        for k, at in enumerate(times):
            due = t0 + at / 1000
            while True:
                t_it = time.perf_counter()
                lead = max(TIMED_LEAD, 2.5 * iter_ms / 1000 + 0.002)      # ลีด = พอสำหรับเช็กสด 1 รอบ (ถ้าลูปช้า/อัดวิดีโอ ลีดยาวขึ้นเอง)
                if t_it >= due - lead:
                    break
                if esc_pressed():
                    raise ClickAborted(f"กด {ck.STOP_KEY_NAME} -> หยุด")
                if not alive(grab()):
                    log.info(f"  {ms():7.0f}ms  หัวข้อ Mission Reward หายก่อนถึงเวลากด -> ไม่คลิก")
                    return done
                iter_ms = 0.8 * iter_ms + 0.2 * (time.perf_counter() - t_it) * 1000
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
            assist_round.verdicts = rec.verdicts


def run_assist(argv):
    if "--react" in argv:
        return run_react(argv)
    dry_run = "--dry" in argv
    once = "--once" in argv
    record = "--record" in argv
    first_list = None                               # --first a,b,c = ทดสอบเวลาคลิกแรก (สลับสุ่มทีละรอบ)
    if "--first" in argv:
        i = argv.index("--first")
        first_list = [float(x) for x in argv[i + 1].split(",")]
        argv = argv[:i] + argv[i + 2:]
        record = True
    second, deltas = None, None                     # --second a,b,c = เวลาคลิกที่ 2 (ms จากหัวข้อ)  --after a,b,c = ห่างจากคลิกแรก (ms)
    if "--after" in argv:
        i = argv.index("--after")
        deltas = [float(x) for x in argv[i + 1].split(",")]
        argv = argv[:i] + argv[i + 2:]
        record = True
    if "--second" in argv:
        i = argv.index("--second")
        second = [float(x) for x in argv[i + 1].split(",")]
        argv = argv[:i] + argv[i + 2:]
        record = True                               # ต้องมีวิดีโอ/trace ถึงจะรู้ว่าเก็บได้ไหม
    nums = [a for a in argv if not a.startswith("--")]
    fixed = None                                    # assist 3988,5000 = กดที่เวลาเหล่านี้เป๊ะๆ ในรอบเดียว
    if nums and "," in nums[0]:
        fixed = [float(x) for x in nums[0].split(",")]
        nums = [str(fixed[0])]
    at_ms = float(nums[0]) if nums else config.CLICK_AT_MS
    if at_ms is None:
        print("ใส่เวลากด (ms) เช่น  python main.py assist 3988  หรือตั้ง CLICK_AT_MS ใน config.py")
        return
    if deltas:
        second = [at_ms + d for d in deltas]            # เวลาคลิกที่ 2 = คลิกแรก + d (ระยะห่างคงที่จากคลิกแรกพอดี)
    clicks = 1 if once else max(1, config.MAX_CLICKS_PER_ITEM)
    if fixed:
        clicks = len(fixed)
    if second:
        clicks = 2
    sweep, order = {s: [] for s in (second or [])}, []
    sweep1, order1 = {s: [] for s in (first_list or [])}, []
    screen = make_screen()
    tuned = tune_realtime()
    log.info("#" * 60)
    log.info(f"[assist] เริ่มทำงาน (คุณคุมเมาส์ บอทกดให้ตรงจังหวะ)  dry_run={dry_run}  กดที่ {at_ms:.0f}ms  "
             f"{clicks} ครั้ง ห่าง {config.RETRY_AFTER_MS}ms  กดค้าง {config.CLICK_HOLD_MS}ms  {tuned}  "
             f"กด {ck.STOP_KEY_NAME} ค้าง/Ctrl+C เพื่อหยุด")
    rounds, total, errors = 0, 0, 0
    while True:
        try:
            times = fixed
            cand1 = None
            if first_list:
                if not order1:
                    order1 = random.sample(first_list, len(first_list))
                cand1 = order1.pop()
                times = [cand1] + ([cand1 + d for d in deltas] if deltas else [])
            if second:
                if not order:
                    order = random.sample(second, len(second))
                cand = order.pop()
                first_t = cand1 if cand1 is not None else at_ms
                times = [first_t, first_t + (cand - at_ms)] if deltas else [first_t, cand]
            n = assist_round(screen, at_ms, clicks, dry_run, record, rounds + 1, times)
            if first_list:
                v1 = getattr(assist_round, 'verdicts', [])
                got1 = [ok for k, _, ok, _ in v1 if k == 1]
                if got1:
                    sweep1[cand1].append(bool(got1[0]))
                log.info('[assist] สรุปสะสมคลิกแรก: ' + '  '.join(
                    f'{c:.0f}ms={sum(r)}/{len(r)}' for c, r in sorted(sweep1.items()) if r) + '   (เก็บได้/ทั้งหมด)')
            if second:
                v = getattr(assist_round, 'verdicts', [])
                got2 = [ok for k, _, ok, _ in v if k == 2]
                if got2:                                          # คลิกที่ 2 ตกช่องว่าง (ชิ้นถัดไปยังไม่ขึ้น) นับเป็นไม่ได้เก็บ
                    sweep[cand].append(bool(got2[0]))
                log.info('[assist] สรุปสะสมคลิกที่ 2: ' + '  '.join(
                    (f'+{c - at_ms:.0f}ms' if deltas else f'{c:.0f}ms') + f'={sum(r)}/{len(r)}'
                    for c, r in sorted(sweep.items()) if r) + '   (เก็บได้/ทั้งหมด' + (' คิดเป็น ms หลังคลิกแรก)' if deltas else ')'))
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
                screen.close()
                screen = make_screen()
            except KeyboardInterrupt:
                break


# ---------------------------------------------------------------------------
# โหมดตามสี (python main.py assist --react): คุณคุมเมาส์ บอทกดทันทีที่ช่องใต้เมาส์พร้อมเก็บ
# ---------------------------------------------------------------------------

def react_round(screen, dry_run=False, record=False, round_no=1):
    """1 รอบ: รอหัวข้อ Mission Reward -> เฝ้าช่องใต้เมาส์ (เมาส์คุณเอง ไม่ขยับ)
    กดทันทีเมื่อ  พื้นหลังช่องเปลี่ยนจากทึบเป็นสีปกติ  หรือ  เห็นป้าย GET!  (OR)
    กดครั้งแรกแล้ว กดซ้ำกันพลาดอีกครั้งหลัง REACT_BACKUP_MS  แล้วเฝ้าช่องเดิมรอชิ้นถัดไปต่อ
    จนกว่า Mission Reward จะหายไป (บันทึกวิดีโอ/log ต่อจนหัวข้อหายด้วย)  คืนจำนวนคลิก"""
    scale = get_scale(screen)
    box = screen.get_region("mission_reward")
    rects = slot_rects(scale)
    down, up = ck.MOUSE_FLAGS["left"]

    last_locate = 0.0
    while True:                                                   # รอหัวข้อ (ทุกเฟรม) + บันทึกภาพ loot
        if esc_pressed():
            raise ClickAborted(f"กด {ck.STOP_KEY_NAME} -> หยุด")
        img = screen.grab(box)
        _loot_log(img, scale)
        if _header_score(img, scale) >= HEADER_MATCH:
            break
        if time.perf_counter() - last_locate >= LOCATE_EVERY:           # หัวข้อไม่อยู่ในกรอบ: ลองหาในพื้นที่กว้างขึ้น
            last_locate = time.perf_counter()
            if locate_header(screen, scale):
                box = screen.get_region("mission_reward")
        time.sleep(0.002)
    t0 = _loot_state["since"] if _loot_state["visible"] else time.perf_counter()
    ms = lambda: (time.perf_counter() - t0) * 1000
    react_round.verdicts = []
    backup = config.REACT_BACKUP_MS
    log.info("=" * 60)
    log.info("[react] เห็น Mission Reward  เฝ้าช่องใต้เมาส์: กดทันทีเมื่อ (ทึบ→พร้อม) หรือ (เห็น GET!)"
             + (f"  +กดซ้ำ {backup:.0f}ms" if backup else "") + f"  กดค้าง {config.CLICK_HOLD_MS}ms  (เมาส์คุณเอง ไม่ขยับให้)"
             + ("  [dry_run]" if dry_run else "") + ("  [บันทึกวิดีโอ]" if record else ""))

    from .recorder import RoundRecorder
    rec = RoundRecorder(scale, box, rects, t0, round_no) if record else None

    def slot_at(cur):
        x, y = cur[0] - box[0], cur[1] - box[1]
        for i, (sx, sy, sw, sh, _) in enumerate(rects):
            if sx <= x <= sx + sw and sy <= y <= sy + sh:
                return i
        return None

    def send_click(grp):
        """ส่งคลิก ณ ตอนนี้ (ตรงที่เมาส์อยู่) ไม่ log (log ช้า) คืน (เวลา ms จากหัวข้อ, perf_counter ตอนส่ง)"""
        nonlocal done
        tp = time.perf_counter()
        t = (tp - t0) * 1000
        if not dry_run:
            ck._send_mouse(down)
            time.sleep(random.uniform(*ck.CLICK_HOLD))
            ck._send_mouse(up)
        done += 1
        if rec is not None:
            rec.mark_click(done, t, grp)
        return t, tp

    def log_click(label, t, n_click, reason):
        x, y = ck.get_cursor()
        log.info(f"  {t:7.1f}ms  กด {label} (คลิกที่ {n_click}) {reason}  screen=({x}, {y})")

    done, groups = 0, 0
    cur_idx = None
    armed, was_dim = True, False
    ready_since = None                    # เฟรมแรกที่ช่องนี้พร้อม (ไว้วัดว่ากดช้ากว่าช่องพร้อมกี่ ms)
    fired_at, retries = None, 0
    last_empty_chk, last_empty = 0.0, False
    miss, last_hdr = 0, 0.0
    try:
        while True:
            if esc_pressed():
                raise ClickAborted(f"กด {ck.STOP_KEY_NAME} -> หยุด")
            img = screen.grab(box)
            now = time.perf_counter()
            cur = ck.get_cursor()
            if rec is not None:
                rec.add(img, cur)
            _loot_log(img, scale)
            if now - last_hdr >= HDR_EVERY:
                last_hdr = now
                miss = 0 if _header_score(img, scale) >= HEADER_MATCH else miss + 1
                if miss >= ck.HEADER_LOST_FRAMES:
                    log.info(f"  {ms():7.1f}ms  Mission Reward หายไปแล้ว -> จบรอบ (กดไป {done} ครั้ง {groups} ชิ้น)")
                    return done
            idx = slot_at(cur)
            if idx != cur_idx:                                           # ย้ายเมาส์ไปช่องอื่น: เริ่มเฝ้าช่องใหม่
                cur_idx, armed, was_dim, ready_since, fired_at, retries = idx, True, False, None, None, 0
            if idx is None:
                continue
            item = rects[idx]
            v = _slot_bg_v(img, item)
            if v < BG_READY_V:                                           # ช่องทึบ
                was_dim, ready_since = True, None
                if not armed and fired_at is not None and now - fired_at > 0.3:
                    armed, fired_at, retries = True, None, 0             # ทึบหลังกด (ชิ้นถัดไปเลื่อนเข้ามา) -> พร้อมเฝ้าใหม่
                continue
            if ready_since is None:
                ready_since = now
            if not armed:                                                # กดไปแล้ว: รอให้ช่องว่างก่อนถึงจะเฝ้าชิ้นใหม่
                if now - last_empty_chk >= 0.04:
                    last_empty_chk = now
                    last_empty = _is_empty_slot(img, item[0], item[1], item[2], item[3], scale)
                if last_empty:
                    armed, was_dim, ready_since, fired_at, retries = True, False, None, None, 0
                elif (fired_at is not None and now - fired_at >= config.REACT_RETRY_MS / 1000 and retries < 2
                      and _get_score(img, ("x", item), scale) >= GET_MATCH):
                    armed, retries = True, retries + 1                   # กดแล้วช่องยังพร้อมค้าง (มีป้าย GET!) = ไม่ติด -> กดใหม่
                    log.info(f"  {ms():7.1f}ms  ช่อง {idx + 1} ยังพร้อมค้างหลังกด {config.REACT_RETRY_MS:.0f}ms -> กดใหม่")
                continue
            # armed + ช่องพร้อม: ตัดสินว่ากดไหม  (GET! หรือ ทึบ→พร้อม)
            reason = None
            if _get_score(img, ("x", item), scale) >= GET_MATCH:
                reason = "เห็นป้าย GET!"
            elif was_dim and not _is_empty_slot(img, item[0], item[1], item[2], item[3], scale):
                reason = "ทึบ→พร้อม"
            if reason is None:
                continue
            lat = (time.perf_counter() - ready_since) * 1000
            groups += 1
            t1, tp1 = send_click(groups)
            n1 = done
            armed, fired_at = False, tp1
            t2 = None
            if backup:                                                   # กดซ้ำกันพลาดให้ตรงเวลา (spin ไม่รอรอบลูปถัดไป ไม่ log คั่นกลาง)
                due_b = tp1 + backup / 1000
                while time.perf_counter() < due_b:
                    time.sleep(0)
                t2, _ = send_click(groups)
            log_click(f"ชิ้นที่ {groups}", t1, n1, f"[{reason}] ช่อง {idx + 1} V={v:.0f} (ช่องพร้อมมาแล้ว {lat:.1f}ms)")
            if t2 is not None:
                log_click("กันพลาด", t2, done, f"(ห่างคลิกแรก {t2 - t1:.1f}ms)")
    finally:
        if rec is not None:
            rec.finish(log)
            react_round.verdicts = rec.verdicts


def run_react(argv):
    dry_run = "--dry" in argv
    record = "--record" in argv
    screen = make_screen()
    tuned = tune_realtime()
    log.info("#" * 60)
    log.info(f"[react] เริ่มทำงาน (คุณคุมเมาส์ บอทกดตามสีช่อง/GET!)  dry_run={dry_run}  กดค้าง {config.CLICK_HOLD_MS}ms  "
             f"กดซ้ำ {config.REACT_BACKUP_MS}ms  {tuned}  กด {ck.STOP_KEY_NAME} ค้าง/Ctrl+C เพื่อหยุด")
    rounds, total, errors = 0, 0, 0
    while True:
        try:
            n = react_round(screen, dry_run, record, rounds + 1)
            rounds += 1
            total += n
            log.info(f"[react] จบรอบที่ {rounds}  คลิก {n}  (รวม {total})")
            wait_closed(screen)
        except ClickAborted as e:
            log.info(f"[react] หยุดทำงาน ({e})  {rounds} รอบ  คลิกรวม {total}  error {errors} ครั้ง")
            break
        except KeyboardInterrupt:
            log.info(f"[react] หยุดทำงาน (Ctrl+C)  {rounds} รอบ  คลิกรวม {total}  error {errors} ครั้ง")
            break
        except Exception:
            errors += 1
            log.info(f"[react] เกิด error ครั้งที่ {errors} -> ทำงานต่อใน {config.ERROR_RETRY_DELAY:.0f} วินาที\n"
                     + traceback.format_exc())
            try:
                time.sleep(config.ERROR_RETRY_DELAY)
                screen.close()
                screen = make_screen()
            except KeyboardInterrupt:
                break
