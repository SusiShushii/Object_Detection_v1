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
from .detection import get_scale, item_name, log
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
    clicks = 1                                      # ไม่มีคลิกสำรองอัตโนมัติ (อยากกดหลายครั้งให้ระบุเวลาเอง เช่น 3988,5000)
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
             f"{clicks} ครั้ง  กดค้าง {config.CLICK_HOLD_MS}ms  จับภาพ={screen.backend}  {tuned}  "
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

def _summarize_clicks(screen, click_info, verdicts, cap0):
    """สรุปท้ายรอบ: จับภาพด้วยอะไร (wgc/mss + กี่เฟรมที่ตกไปใช้ mss) และแต่ละคลิกกดที่กี่ ms ด้วยวิธีไหน เก็บติดไหม"""
    fb = getattr(screen, "fallbacks", 0) - cap0
    log.info(f"[สรุปรอบ] จับภาพ={screen.backend}" + (f"  (ตกไปใช้ mss {fb} เฟรม)" if fb else "")
             + ("" if verdicts is not None or not click_info else "   (ใส่ --record เพื่อดูว่าคลิกไหนเก็บติด)"))
    vd = {n: (ok, text) for n, _, ok, text in (verdicts or [])}
    for n, t, method, slot in click_info:
        ok = vd.get(n, (None, ""))[0]
        res = "" if verdicts is None else ("  -> เก็บติด" if ok else "  -> ไม่ติด/ไม่ได้เก็บ")
        log.info(f"[สรุปรอบ]   คลิกที่ {n}: {t:7.1f}ms  วิธี={method}  ช่อง {slot + 1 if slot is not None else '-'}{res}")


def react_round(screen, dry_run=False, record=False, round_no=1, timed_ms=None, mode=None):
    """1 รอบ: รอหัวข้อ Mission Reward -> เฝ้าช่องใต้เมาส์ (เมาส์คุณเอง ไม่ขยับ)
    กดทันทีเมื่อ  พื้นหลังช่องเปลี่ยนจากทึบเป็นสีปกติ  หรือ  เห็นป้าย GET!  (OR)
    กดครั้งแรกแล้ว กดซ้ำกันพลาดอีกครั้งหลัง REACT_BACKUP_MS  แล้วเฝ้าช่องเดิมรอชิ้นถัดไปต่อ
    จนกว่า Mission Reward จะหายไป (บันทึกวิดีโอ/log ต่อจนหัวข้อหายด้วย)  คืนจำนวนคลิก
    mode (ใช้ทดลอง --ab): "timed" = กดตั้งเวลาอย่างเดียว  "react" = ตามสีอย่างเดียว  None = ทั้งคู่
    timed_ms: ถ้าระบุ จะกดตรงตำแหน่งเมาส์ที่เวลานี้ (ms จากหัวข้อ) 1 ครั้งด้วย  ไม่ติดก็ยังมีส่วนตามสีทำงานต่อ"""
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
    if mode == "react":
        timed_ms = None
    use_react = mode != "timed"
    combined = timed_ms is not None or mode is not None           # โหมดรวม/ทดลอง: ไม่มีคลิกสำรอง/กดใหม่
    backup = 0 if combined else config.REACT_BACKUP_MS
    cap0 = getattr(screen, "fallbacks", 0)
    click_info = []                                               # (คลิกที่, ms, วิธี, ช่อง)
    log.info("=" * 60)
    log.info("[react] เห็น Mission Reward  " + (f"กดตั้งเวลาที่ {timed_ms:g}ms + " if timed_ms is not None else "")
             + "เฝ้าช่องใต้เมาส์: กดทันทีเมื่อ (ทึบ→พร้อม) หรือ (เห็น GET!)"
             + f"  จับภาพ={screen.backend}"
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
    timed_due = None if timed_ms is None else t0 + timed_ms / 1000
    timed_fired = timed_due is None
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
            if not timed_fired and now >= timed_due - TIMED_LEAD:       # ถึงเวลากดตั้งเวลา: เช็กหัวข้อสดแล้ว spin รอเวลาเป๊ะ
                timed_fired = True
                if now > timed_due + 0.05:
                    log.info(f"  {ms():7.1f}ms  เลยเวลากดตั้งเวลา {timed_ms:g}ms ไปแล้ว -> ข้าม (ใช้ตามสีแทน)")
                elif _header_score(screen.grab(box), scale) < HEADER_MATCH:
                    log.info(f"  {ms():7.1f}ms  ไม่เห็นหัวข้อก่อนกดตั้งเวลา -> ไม่กด")
                else:
                    while time.perf_counter() < timed_due:
                        time.sleep(0)
                    groups += 1
                    tt, _ = send_click(groups)
                    click_info.append((done, tt, "ตั้งเวลา", slot_at(ck.get_cursor())))
                    log_click("ตั้งเวลา", tt, done, f"(ตั้งไว้ {timed_ms:g}ms)")
                    continue
            if not use_react:
                continue
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
                elif (fired_at is not None and now - fired_at >= config.REACT_RETRY_MS / 1000 and retries < (0 if combined else 2)
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
            click_info.append((done, t1, f"ตามสี ({reason})", idx))
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
                click_info.append((done, t2, "กันพลาด", idx))
    finally:
        if rec is not None:
            rec.finish(log)
            react_round.verdicts = rec.verdicts
        react_round.clicks = click_info
        _summarize_clicks(screen, click_info, rec.verdicts if rec is not None else None, cap0)


def _ab_collect(res, mode, clicks, verdicts):
    """เก็บผลทดลองรอบนี้: (เก็บติดไหม, คลิกที่กี่ ms, ช่องว่างจริงที่กี่ ms จากหัวข้อ)  แล้วพิมพ์สรุปสะสม"""
    import re
    if not clicks:
        res[mode].append((None, None, None))                           # ไม่มีคลิกเลย (ตามสีไม่ทำงาน / พลาดเวลา)
    else:
        _, t, _, _ = clicks[0]
        v = next((x for x in verdicts if x[1] == min(c[1] for c in clicks)), None)
        ok = None if v is None else v[2]
        got = None
        if ok:
            m = re.search(r"(?:ช่องว่าง|ทึบแว้บ)ที่ \+(\d+)ms", v[3])
            got = t + float(m.group(1)) if m else None
        res[mode].append((ok, t, got))
    for k, name in (("timed", "ตั้งเวลา"), ("react", "ตามสี")):
        rs = res[k]
        if not rs:
            continue
        oks = [r for r in rs if r[0]]
        none = sum(1 for r in rs if r[1] is None)
        cl = [r[1] for r in oks]
        gt = [r[2] for r in oks if r[2] is not None]
        avg = lambda xs: f"{sum(xs) / len(xs):.1f}" if xs else "-"
        log.info(f"[ab] สะสม {name}: เก็บติด {len(oks)}/{len(rs)} รอบ (ไม่ได้กดเลย {none})  "
                 f"กดเฉลี่ย {avg(cl)}ms  เก็บเสร็จ(ช่องว่าง)เฉลี่ย {avg(gt)}ms")


def run_react(argv):
    dry_run = "--dry" in argv
    record = "--record" in argv
    nums = [a for a in argv if not a.startswith("--")]
    timed_ms = float(nums[0]) if nums else None             # assist 3979 --react = กดตั้งเวลา + ตามสี ทำงานคู่กัน
    ab = "--ab" in argv                                      # ทดลอง: สลับสุ่มรอบละวิธี (ตั้งเวลาอย่างเดียว / ตามสีอย่างเดียว) เทียบกัน
    if ab:
        if timed_ms is None:
            print("--ab ต้องระบุเวลา เช่น  python main.py assist 3979 --react --ab")
            return
        record = True                                        # ต้องมีวิดีโอ/trace ถึงจะรู้ว่าเก็บได้เมื่อไร
    ab_res, ab_order = {"timed": [], "react": []}, []
    screen = make_screen()
    tuned = tune_realtime()
    log.info("#" * 60)
    log.info(f"[react] เริ่มทำงาน (คุณคุมเมาส์ บอทกด{f'ที่ {timed_ms:g}ms และ' if timed_ms is not None else ''}ตามสีช่อง/GET!)  dry_run={dry_run}  กดค้าง {config.CLICK_HOLD_MS}ms  "
             f"กดซ้ำ {0 if timed_ms is not None else config.REACT_BACKUP_MS}ms  จับภาพ={screen.backend}  {tuned}  กด {ck.STOP_KEY_NAME} ค้าง/Ctrl+C เพื่อหยุด")
    rounds, total, errors = 0, 0, 0
    while True:
        try:
            mode = None
            if ab:
                if not ab_order:
                    ab_order = random.sample(["timed", "react"], 2)
                mode = ab_order.pop()
                log.info(f"[ab] รอบที่ {rounds + 1}: ทดลองวิธี = {'ตั้งเวลา ' + format(timed_ms, 'g') + 'ms อย่างเดียว' if mode == 'timed' else 'ตามสีอย่างเดียว'}")
            n = react_round(screen, dry_run, record, rounds + 1, timed_ms, mode)
            if ab:
                _ab_collect(ab_res, mode, react_round.clicks, react_round.verdicts)
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


# ---------------------------------------------------------------------------
# โหมด random loot (python main.py random blue_seaweed ...)
# ช่องเป็นกล่อง "?" -> รอกล่องหายหมด + ของเป็นสีปกติ -> ขยับเมาส์ไปกดเฉพาะไอเท็มที่ระบุ (ตามลำดับความสำคัญ)
# ---------------------------------------------------------------------------
RANDOM_BOX_GONE_FRAMES = 2     # ไม่เห็นกล่อง "?" ติดกันกี่เฟรมถึงถือว่ากล่องหายหมด
RANDOM_NO_BOX_WAIT = 1.0       # เห็นหัวข้อแล้วนานเท่านี้ (วินาที) ยังไม่เจอกล่อง "?" เลย = ไม่ใช่ด่าน random loot -> ข้ามรอบนี้
RANDOM_READY_WAIT = 1.5        # กล่องหายแล้ว รอไอเท็มเป้าหมายเป็นสีปกติไม่เกินเท่านี้ (วินาที)


def random_round(screen, names=None, dry_run=False):
    """1 รอบด่าน random loot  names = ชื่อไอเท็มที่ต้องการ (None = ทุกไอเท็มใน templates/)  คืนจำนวนคลิก"""
    scale = get_scale(screen)
    box = screen.get_region("mission_reward")
    down, up = ck.MOUSE_FLAGS["left"]
    priority = list(dict.fromkeys(names)) if names else None
    tnames = ck._resolve_names(names)
    if not tnames:
        log.info("[random] ไม่มี template ที่ตรงกับชื่อที่ระบุ")
        return 0
    clicked = 0

    last_locate = 0.0
    while True:                                                   # รอหัวข้อ
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
    log.info("=" * 60)
    log.info(f"[random] เห็น Mission Reward  เป้าหมาย: {', '.join(priority) if priority else 'ทุกไอเท็ม'}"
             f"  รอกล่อง ? หาย + ของสีปกติ{'  [dry_run]' if dry_run else ''}")

    state = {"miss": 0, "last_hdr": 0.0}

    def check_frame(frame):
        _loot_log(frame, scale)
        now = time.perf_counter()
        if now - state["last_hdr"] >= HDR_EVERY:
            state["last_hdr"] = now
            state["miss"] = 0 if _header_score(frame, scale) >= HEADER_MATCH else state["miss"] + 1
        return state["miss"] < ck.HEADER_LOST_FRAMES

    step_n = [0]

    def step():
        step_n[0] += 1
        if step_n[0] % ck.STEP_CHECK_EVERY:
            return False
        return not check_frame(screen.grab(box))

    # ---- 1) รอกล่อง ? หายหมด ----
    seen_box, gone = False, 0
    while True:
        if esc_pressed():
            raise ClickAborted(f"กด {ck.STOP_KEY_NAME} -> หยุด")
        img = screen.grab(box)
        if not check_frame(img):
            log.info(f"  {ms():7.0f}ms  หัวข้อ Mission Reward หายก่อนกล่องเปิด -> เลิกรอบนี้")
            return clicked
        has_box, _ = ck._is_random_loot(img, scale)
        if has_box:
            if not seen_box:
                log.info(f"  {ms():7.0f}ms  เห็นกล่อง ? (random loot) รอให้กล่องเปิดหมด")
            seen_box, gone = True, 0
        elif seen_box:
            gone += 1
            if gone >= RANDOM_BOX_GONE_FRAMES:
                break
        elif ms() > RANDOM_NO_BOX_WAIT * 1000:
            log.info(f"  {ms():7.0f}ms  ไม่เจอกล่อง ? -> ไม่ใช่ด่าน random loot ข้ามรอบนี้ (ใช้ run แทน)")
            return clicked
    log.info(f"  {ms():7.0f}ms  กล่อง ? หายหมดแล้ว")

    # ---- 2) หาไอเท็มเป้าหมาย + รอให้เป็นสีปกติ ----
    rank = lambda t: (priority.index(item_name(t[0])) if priority and item_name(t[0]) in priority else 0,
                      t[1][1], t[1][0])
    deadline = time.perf_counter() + RANDOM_READY_WAIT
    targets = []
    while True:
        if esc_pressed():
            raise ClickAborted(f"กด {ck.STOP_KEY_NAME} -> หยุด")
        img = screen.grab(box)
        if not check_frame(img):
            log.info(f"  {ms():7.0f}ms  หัวข้อ Mission Reward หายก่อนกด -> เลิกรอบนี้")
            return clicked
        found = sorted(ck._detect_all(img, tnames, scale), key=rank)
        ready = [t for t in found if _slot_bg_v(img, t[1]) >= BG_READY_V]
        if found and len(ready) == len(found):
            targets = found
            break
        if time.perf_counter() >= deadline:
            targets = ready
            if not found:
                log.info(f"  {ms():7.0f}ms  ไม่เจอไอเท็มเป้าหมายในรอบนี้ -> ไม่คลิก")
            elif not ready:
                log.info(f"  {ms():7.0f}ms  เจอ {len(found)} ตัวแต่ยังทึบ -> ไม่คลิก")
            else:
                log.info(f"  {ms():7.0f}ms  รอสีปกติไม่ครบ -> กดเฉพาะที่พร้อม {len(ready)}/{len(found)}")
            break
    if not targets:
        return clicked
    log.info(f"  {ms():7.0f}ms  เจอเป้าหมาย {len(targets)} ตัว: "
             + ", ".join(f"{item_name(n)}@({it[0]},{it[1]})" for n, it in targets))

    def click_item(it, label):
        nonlocal clicked
        x, y = _hover_point(it, box)
        if not dry_run:
            if move_to(x, y, on_step=step):
                log.info(f"  {ms():7.0f}ms  หัวข้อ Mission Reward หายระหว่างขยับ -> หยุด")
                return False
            real_move(x, y)
            time.sleep(ck.REAL_MOVE_DWELL)
            ck._send_mouse(down)
            time.sleep(random.uniform(*ck.CLICK_HOLD))
            ck._send_mouse(up)
        clicked += 1
        log.info(f"  {ms():7.0f}ms  กด {label}  screen=({x}, {y})")
        return True

    def sweep(items):
        for n, (name, it) in enumerate(items):
            if esc_pressed():
                raise ClickAborted(f"กด {ck.STOP_KEY_NAME} -> หยุด")
            if n:
                time.sleep(config.COLLECT_GAP_MS / 1000)
            if not click_item(it, item_name(name)):
                return False
        return True

    # ---- 3) กดตามลำดับความสำคัญ แล้วตรวจซ้ำว่าช่องว่างหรือยัง ----
    if not sweep(targets):
        return clicked
    for p in range(2, config.COLLECT_MAX_PASSES + 1):
        if dry_run:
            break
        end = time.perf_counter() + VERIFY_AFTER
        while time.perf_counter() < end:
            if not check_frame(screen.grab(box)):
                return clicked
        frame = screen.grab(box)
        left = [t for t in targets if not _is_empty_slot(frame, t[1][0], t[1][1], t[1][2], t[1][3], scale)]
        if not left:
            log.info(f"  {ms():7.0f}ms  ช่องเป้าหมายว่างครบ -> เก็บเสร็จ ({clicked} คลิก)")
            return clicked
        log.info(f"  {ms():7.0f}ms  ยังไม่ว่าง {len(left)} ช่อง -> กดรอบที่ {p}")
        if not sweep(left):
            return clicked
    log.info(f"  {ms():7.0f}ms  จบรอบ ({clicked} คลิก)")
    return clicked


def run_random(argv):
    dry_run = "--dry" in argv
    names = [a for a in argv if not a.startswith("--")] or None
    screen = make_screen()
    tuned = tune_realtime()
    log.info("#" * 60)
    log.info(f"[random] เริ่มทำงาน (random loot: กล่อง ? หาย + ของสีปกติ -> ขยับเมาส์กดเฉพาะ {', '.join(names) if names else 'ทุกไอเท็ม'})  "
             f"dry_run={dry_run}  กดค้าง {config.CLICK_HOLD_MS}ms  {tuned}  กด {ck.STOP_KEY_NAME} ค้าง/Ctrl+C เพื่อหยุด")
    rounds, total, errors = 0, 0, 0
    while True:
        try:
            n = random_round(screen, names, dry_run)
            rounds += 1
            total += n
            log.info(f"[random] จบรอบที่ {rounds}  คลิก {n}  (รวม {total})")
            wait_closed(screen)
        except ClickAborted as e:
            log.info(f"[random] หยุดทำงาน ({e})  {rounds} รอบ  คลิกรวม {total}  error {errors} ครั้ง")
            break
        except KeyboardInterrupt:
            log.info(f"[random] หยุดทำงาน (Ctrl+C)  {rounds} รอบ  คลิกรวม {total}  error {errors} ครั้ง")
            break
        except Exception:
            errors += 1
            log.info(f"[random] เกิด error ครั้งที่ {errors} -> ทำงานต่อใน {config.ERROR_RETRY_DELAY:.0f} วินาที\n"
                     + traceback.format_exc())
            try:
                time.sleep(config.ERROR_RETRY_DELAY)
                screen.close()
                screen = make_screen()
            except KeyboardInterrupt:
                break
