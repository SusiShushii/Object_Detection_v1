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


# ---------------------------------------------------------------------------
# โหมด assist (python main.py assist): คุณคุมเมาส์ บอทกด 2 อย่างคู่กัน
#   1) กดตามเวลา ASSIST_CLICK_AT_MS (config.py) 1 ครั้งตรงที่เมาส์อยู่   2) กดตามสีช่องใต้เมาส์ (ทึบ->พร้อม หรือ GET!)
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


def assist_round(screen, dry_run=False, record=False, round_no=1, names=None):
    """1 รอบ: รอหัวข้อ Mission Reward แล้วทำ 2 อย่างพร้อมกัน (ไม่ระบุ names = เมาส์เป็นของคุณ บอทไม่ขยับ)
    names = ชื่อไอเท็มที่ต้องการ (assist carrot): บอท detect แล้ว"ลากเมาส์ไปครอบไอเท็ม"ให้ (ตามลำดับความสำคัญ)
            เก็บชิ้นหนึ่งว่างแล้วย้ายไปชิ้นถัดไป  การกดยังเป็น 2 อย่างเดิม (ตามเวลา + ตามสี)
      1) กดตั้งเวลา: ที่ ASSIST_CLICK_AT_MS (ms จากหัวข้อ) กด 1 ครั้งตรงที่เมาส์อยู่  (None = ปิด)
      2) กดตามสี: เฝ้าช่องใต้เมาส์ กดเมื่อพื้นหลังช่องเปลี่ยน ทึบ->พร้อม หรือเห็นป้าย GET!  (ย้ายเมาส์ไปช่องอื่น = เฝ้าช่องใหม่)
    ไม่มีคลิกสำรอง/กดใหม่  ทำต่อจนกว่า Mission Reward จะหายไป  คืนจำนวนคลิก"""
    scale = get_scale(screen)
    box = screen.get_region("mission_reward")
    rects = slot_rects(scale)
    down, up = ck.MOUSE_FLAGS["left"]
    tnames = ck._resolve_names(names) if names else None
    priority = list(dict.fromkeys(names)) if names else []
    rank = lambda t: (priority.index(item_name(t[0])) if item_name(t[0]) in priority else len(priority), t[1][1], t[1][0])
    queue, tgt, pending, last_detect = [], {"item": None, "clicked": False, "t": 0.0, "chk": 0.0}, bool(tnames), 0.0

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
    assist_round.verdicts = []
    timed_ms = getattr(config, "ASSIST_CLICK_AT_MS", None)
    cap0 = getattr(screen, "fallbacks", 0)
    click_info = []                                               # (คลิกที่, ms, วิธี, ช่อง)
    log.info("=" * 60)
    log.info("[assist] เห็น Mission Reward  " + (f"กดตั้งเวลาที่ {timed_ms:g}ms + " if timed_ms is not None else "")
             + "เฝ้าช่องใต้เมาส์: กดทันทีเมื่อ (ทึบ→พร้อม) หรือ (เห็น GET!)"
             + f"  จับภาพ={screen.backend}"
             + f"  กดค้าง {config.CLICK_HOLD_MS}ms  (" + (f"บอทลากเมาส์ไปครอบ {', '.join(priority)}" if names else "เมาส์คุณเอง ไม่ขยับให้") + ")"
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
        if tgt["item"] is not None:
            tgt["clicked"], tgt["t"] = True, tp
        if rec is not None:
            rec.mark_click(done, t, grp)
        return t, tp

    def log_click(label, t, n_click, reason):
        x, y = ck.get_cursor()
        log.info(f"  {t:7.1f}ms  กด {label} (คลิกที่ {n_click}) {reason}  screen=({x}, {y})")

    done, groups = 0, 0
    timed_due = None if timed_ms is None else t0 + timed_ms / 1000
    timed_fired = timed_due is None

    def _esc_step():
        if esc_pressed():
            raise ClickAborted(f"กด {ck.STOP_KEY_NAME} -> หยุด")
        return False

    def goto_next():
        """ลากเมาส์ไปครอบไอเท็มเป้าหมายตัวถัดไปในคิว (ไม่มีแล้ว = ปล่อยเมาส์ไว้)"""
        if not queue:
            tgt["item"] = None
            return
        name, item = queue.pop(0)
        tgt.update(item=item, clicked=False, t=time.perf_counter(), chk=0.0)
        hx, hy = _hover_point(item, box)
        if not dry_run:
            late = timed_due is not None and not timed_fired and time.perf_counter() > timed_due - 0.4
            if not late:
                move_to(hx, hy, on_step=_esc_step)
            real_move(hx, hy)
        log.info(f"  {ms():7.1f}ms  ลากเมาส์ไปครอบ {item_name(name)} ช่อง {slot_at((hx, hy)) + 1 if slot_at((hx, hy)) is not None else '-'}"
                 f"  screen=({hx}, {hy})  (คิวเหลือ {len(queue)})")

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
            if pending and now - last_detect >= 0.05:                    # ยังไม่เจอไอเท็มเป้าหมาย: ลอง detect ถี่ๆ
                last_detect = now
                found = sorted(ck._detect_all(img, tnames, scale), key=rank)
                if found:
                    pending = False
                    queue.extend(found)
                    log.info(f"  {ms():7.1f}ms  เจอไอเท็มเป้าหมาย {len(found)} ตัว: "
                             + ", ".join(f"{item_name(n)}@({it[0]},{it[1]})" for n, it in found))
                    goto_next()
                    continue
            if tgt["item"] is not None and tgt["clicked"] and now - tgt["chk"] >= 0.05:
                tgt["chk"] = now                                       # เก็บชิ้นนี้แล้ว (ช่องว่าง/นานเกิน) -> ไปชิ้นถัดไป
                it = tgt["item"]
                if _is_empty_slot(img, it[0], it[1], it[2], it[3], scale) or now - tgt["t"] > 1.5:
                    goto_next()
                    continue
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
            log_click(f"ชิ้นที่ {groups}", t1, n1, f"[{reason}] ช่อง {idx + 1} V={v:.0f} (ช่องพร้อมมาแล้ว {lat:.1f}ms)")
    finally:
        if rec is not None:
            rec.finish(log)
            assist_round.verdicts = rec.verdicts
        assist_round.clicks = click_info
        _summarize_clicks(screen, click_info, rec.verdicts if rec is not None else None, cap0)


def run_assist(argv):
    dry_run = "--dry" in argv
    record = bool(getattr(config, "ASSIST_RECORD", False))
    timed_ms = getattr(config, "ASSIST_CLICK_AT_MS", None)
    names = None
    args = [a for a in argv if not a.startswith("--")]
    if args:                                                 # assist carrot / assist example_dungeon = บอทลากเมาส์ไปครอบไอเท็มให้
        from .profiles import list_profiles, resolve
        info = resolve(args)
        for e in info["errors"]:
            print(f"ผิดพลาด: {e}")
        if info["errors"]:
            return
        if info["missing"]:
            print(f"ไม่พบรูปไอเท็ม {info['missing']} (ต้องมีไฟล์ชื่อนี้.png ใน assets/templates/ หรือโฟลเดอร์โปรไฟล์)")
            print(f"ที่มี: {info['available']}   โปรไฟล์ที่มี: {list_profiles() or '(ยังไม่มี)'}")
            if not info["names"]:
                return
            print("-> ใช้เฉพาะที่หารูปเจอต่อไป")
        names = info["names"]
    screen = make_screen()
    tuned = tune_realtime()
    log.info("#" * 60)
    log.info(f"[assist] เริ่มทำงาน ({'บอทลากเมาส์ไปครอบ ' + ', '.join(names) + ' และ' if names else 'คุณคุมเมาส์ บอท'}กด{f'ที่ {timed_ms:g}ms และ' if timed_ms is not None else ''}ตามสีช่อง/GET!)  dry_run={dry_run}  กดค้าง {config.CLICK_HOLD_MS}ms  "
             f"ไม่มีคลิกสำรอง  จับภาพ={screen.backend}  {tuned}  กด {ck.STOP_KEY_NAME} ค้าง/Ctrl+C เพื่อหยุด")
    rounds, total, errors = 0, 0, 0
    while True:
        try:
            n = assist_round(screen, dry_run, record, rounds + 1, names)
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
# โหมด random loot (python main.py random blue_seaweed ...)
# ช่องเป็นกล่อง "?" -> รอกล่องหายหมด + ของเป็นสีปกติ -> ขยับเมาส์ไปกดเฉพาะไอเท็มที่ระบุ (ตามลำดับความสำคัญ)
# ---------------------------------------------------------------------------
RANDOM_BOX_GONE_FRAMES = 2     # ไม่เห็นกล่อง "?" ติดกันกี่เฟรมถึงถือว่ากล่องหายหมด
RANDOM_NO_BOX_WAIT = 0.6       # ไอเท็มขึ้นแล้ว (~3 วินาทีหลังหัวข้อ) แต่ไม่เจอกล่อง "?" นานเท่านี้ (วินาที) = ไม่ใช่ด่าน random loot -> ข้ามรอบนี้
RANDOM_NOTHING_WAIT = 10.0     # เห็นหัวข้อแล้วช่องยังว่างทั้งหมดนานเท่านี้ (วินาที) = เลิกรอบนี้
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
    seen_box, gone, items_since = False, 0, None
    rects = slot_rects(scale)
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
        else:
            now = time.perf_counter()
            if _occupied(img, scale, rects):                           # ไอเท็มขึ้นแล้วแต่ยังไม่เห็นกล่อง ?
                items_since = items_since or now
                if now - items_since > RANDOM_NO_BOX_WAIT:
                    log.info(f"  {ms():7.0f}ms  ไอเท็มขึ้นแล้วแต่ไม่เจอกล่อง ? -> ไม่ใช่ด่าน random loot ข้ามรอบนี้ (ใช้ run แทน)")
                    return clicked
            elif ms() > RANDOM_NOTHING_WAIT * 1000:
                log.info(f"  {ms():7.0f}ms  ช่องยังว่างอยู่นานเกินไป -> เลิกรอบนี้")
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
    from .profiles import list_profiles, resolve
    args = [a for a in argv if not a.startswith("--")]               # random blue_seaweed carrot  หรือ  random example_dungeon
    info = resolve(args)
    if "--list" in argv:
        print(f"รูปไอเท็มที่มี: {info['available']}")
        print(f"โปรไฟล์ที่มี: {list_profiles() or '(ยังไม่มี)'}")
        return
    for e in info["errors"]:
        print(f"ผิดพลาด: {e}")
    if info["errors"]:
        return
    if info["missing"]:
        print(f"ไม่พบรูปไอเท็ม {info['missing']} (ต้องมีไฟล์ชื่อนี้.png ใน assets/templates/ หรือโฟลเดอร์โปรไฟล์)")
        print(f"ที่มี: {info['available']}   โปรไฟล์ที่มี: {list_profiles() or '(ยังไม่มี)'}")
        if not info["names"]:
            return
        print("-> เก็บเฉพาะที่หารูปเจอต่อไป")
    names = info["names"]
    if not names:                                                   # ไม่ระบุอะไรเลย = ไม่ทำงาน (กันกดทุกไอเท็มโดยไม่ตั้งใจ)
        print("ต้องระบุชื่อไอเท็มหรือโปรไฟล์ เช่น  python main.py random blue_seaweed  หรือ  python main.py random example_dungeon"
              f"  (ดูรายชื่อ: python main.py random --list)  รูปที่มี: {info['available']}")
        return
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
