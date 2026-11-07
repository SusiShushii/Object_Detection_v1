"""
collector.py - โหมด assist (python main.py assist)

assist : คุณคุมเมาส์ (หรือใส่ชื่อไอเท็มให้บอทขยับเมาส์ให้) บอทกด 2 อย่างคู่กัน: ตามเวลา CLICK_AT_MS + ตามสีช่อง (ทึบ->พร้อม / GET!)
ตั้งค่าที่ config.py (CLICK_AT_MS, MOUSE_JUMP, RECORD, ...)  บันทึกภาพ loot (ทึบ + สีปกติ) ลง output/loot ทุกรอบ
(โหมดแยกแบบเดิม เช่น กดตามเวลาอย่างเดียว / collect อยู่ใน test_assist_and_react/)
"""

import random

import cv2
import time
import threading
import traceback

import config

from . import clicker as ck
from .clicker import (BG_READY_V, GET_MATCH, LOCATE_EVERY, _get_score, _slot_bg_v, locate_header, GRID_ORIGIN, GRID_PITCH, GRID_SIZE, HEADER_MATCH, SLOT_WH, ClickAborted, _header_score,
                      _hover_point, _is_empty_slot, _loot_log, _loot_state, esc_pressed, move_to, real_move,
                      tune_realtime)
from .detection import COLOUR_CHECK, _color_top, get_scale, item_name, load_template, log, same_colour
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


SLOT_SEARCH_MARGIN = 8     # ค้นหาไอเท็มรอบช่อง ± เท่านี้ px (ที่จอ 2K) เร็วกว่าเทียบทั้งภาพหลายเท่า ผลเท่ากัน


def _detect_slots(img, names, scale, rects, threshold=0.75):
    """เหมือน ck._detect_all (คืน [(template, (x, y, w, h, score))]) แต่เทียบ template เฉพาะรอบ 21 ช่อง ไม่เทียบทั้งภาพ
    ~10-15 เท่าเร็วกว่า (ต่อ template ~0.5 ms แทน ~6 ms)  ใช้ตอนต้องตรวจถี่ๆ ทุกเฟรม"""
    H, W = img.shape[:2]
    m = max(1, int(round(SLOT_SEARCH_MARGIN * scale)))
    targets = []
    for name in names:
        tpl_full = load_template(name, scale)[0]
        th, tw = tpl_full.shape[:2]
        top = _color_top(name, scale)
        topt = top.shape[0]
        for (sx, sy, sw, sh, _) in rects:
            x1, y1 = max(0, sx - m), max(0, sy - m)
            x2, y2 = min(W, sx + sw + m), min(H, sy + max(sh, th) + m)
            win = img[y1:y2, x1:x2]
            if win.shape[0] < topt or win.shape[1] < tw:
                continue
            res = cv2.matchTemplate(win, top, cv2.TM_CCOEFF_NORMED)
            res = res[:max(1, H - th + 1 - y1)]                      # กรอบเต็มต้องไม่ล้นภาพ (เหมือน match_map)
            _, best, _, loc = cv2.minMaxLoc(res)
            if not (best >= threshold):
                continue
            x, y = x1 + loc[0], y1 + loc[1]
            if COLOUR_CHECK and not same_colour(img[y:y + th, x:x + tw], tpl_full):
                continue
            item = (int(x), int(y), tw, th, float(best))
            overlap = next((t for t in targets if abs(t[1][0] - item[0]) <= item[2] // 2
                            and abs(t[1][1] - item[1]) <= item[3] // 2), None)
            if overlap is None:
                targets.append((name, item))
            elif item[4] > overlap[1][4]:
                targets[targets.index(overlap)] = (name, item)
    return targets


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
#   1) กดตามเวลา CLICK_AT_MS (config.py) 1 ครั้งตรงที่เมาส์อยู่   2) กดตามสีช่องใต้เมาส์ (ทึบ->พร้อม หรือ GET!)
# ---------------------------------------------------------------------------

def click_times():
    """เวลากดตามเวลา (ms จากหัวข้อ) จาก config.CLICK_AT_MS: ตัวเลขเดียว หรือรายการ เช่น [3975, 3980]  None/[] = ปิด  คืน list เรียงน้อย->มาก"""
    raw = config.CLICK_AT_MS
    if raw is None:
        return []
    return sorted(float(x) for x in (raw if isinstance(raw, (list, tuple)) else [raw]))


def _fmt_times(times):
    return "+".join(f"{t:g}" for t in times)


def _go(x, y, step=None):
    """ย้ายเมาส์ไป (x, y): config.MOUSE_JUMP = True วาปทันที  False ลากแบบคน  คืน True ถ้า step สั่งหยุดกลางทาง"""
    if config.MOUSE_JUMP:
        real_move(x, y)
        return False
    stopped = move_to(x, y, on_step=step)
    real_move(x, y)
    return stopped


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
      1) กดตั้งเวลา: ที่ CLICK_AT_MS (ms จากหัวข้อ) กด 1 ครั้งตรงที่เมาส์อยู่  (None = ปิด)
      2) กดตามสี: เฝ้าช่องใต้เมาส์ กดเมื่อเห็นป้าย GET! หรือช่องเป็นสีปกติ (เปลี่ยนจากทึบ/เมาส์มาถึงช่องที่สีปกติแล้ว) และยังไม่ว่าง  (ย้ายเมาส์ไปช่องอื่น = เฝ้าช่องใหม่)
    ไม่มีคลิกสำรอง/กดใหม่  ทำต่อจนกว่า Mission Reward จะหายไป  คืนจำนวนคลิก"""
    scale = get_scale(screen)
    box = screen.get_region("mission_reward")
    rects = slot_rects(scale)
    down, up = ck.MOUSE_FLAGS["left"]
    tnames = ck._resolve_names(names) if names else None
    priority = list(dict.fromkeys(names)) if names else []
    rank = lambda t: (priority.index(item_name(t[0])) if item_name(t[0]) in priority else len(priority), t[1][1], t[1][0])
    queue, tgt, pending = [], {"item": None, "clicked": False, "t": 0.0, "chk": 0.0}, bool(tnames)

    det = {"frame": None, "found": None, "stop": False, "passes": 0, "pass_ms": 0.0, "frame_to_found_ms": 0.0}   # หาไอเท็มในเธรดแยก: ลูปหลัก (กดตามเวลา/ตามสี) ไม่ต้องรอ detect
    def _detect_worker():
        last = None
        while not det["stop"]:
            fr = det["frame"]                                    # (ภาพ, เวลาที่จับได้)
            if fr is None or fr is last:
                time.sleep(0.001)
                continue
            last = fr
            t_a = time.perf_counter()
            found = _detect_slots(fr[0], tnames, scale, rects)
            t_b = time.perf_counter()
            det["passes"] += 1
            det["pass_ms"] = (t_b - t_a) * 1000
            if found:
                det["frame_to_found_ms"] = (t_b - fr[1]) * 1000
                det["found"] = found
                return
    if pending:
        threading.Thread(target=_detect_worker, daemon=True).start()

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
    timed_times = click_times()
    cap0 = getattr(screen, "fallbacks", 0)
    click_info = []                                               # (คลิกที่, ms, วิธี, ช่อง)
    log.info("=" * 60)
    log.info("[assist] เห็น Mission Reward  " + (f"กดตั้งเวลาที่ {_fmt_times(timed_times)}ms + " if timed_times else "")
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
    timed_i = 0                                                   # ลำดับเวลากดตามเวลาที่ถึงคิวต่อไป
    timed_due = t0 + timed_times[0] / 1000 if timed_times else None
    timed_fired = not timed_times

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
        tgt.update(item=item, clicked=False, t=time.perf_counter(), chk=0.0, gone_n=0)
        hx, hy = _hover_point(item, box)
        if not dry_run:
            late = timed_due is not None and not timed_fired and time.perf_counter() > timed_due - 0.4
            if late:
                real_move(hx, hy)                                     # เหลือเวลาน้อย: วาปเลย
            else:
                _go(hx, hy, _esc_step)
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
            if pending:                                                  # ยังไม่เจอไอเท็มเป้าหมาย: ส่งเฟรมล่าสุดให้เธรด detect (ตรวจต่อเนื่องทุกเฟรมที่ว่าง)
                det["frame"] = (img.copy(), now)
                found = sorted(det["found"], key=rank) if det["found"] else None
                if found:
                    pending = False
                    queue.extend(found)
                    log.info(f"  {ms():7.1f}ms  เจอไอเท็มเป้าหมาย {len(found)} ตัว: "
                             + ", ".join(f"{item_name(n)}@({it[0]},{it[1]})" for n, it in found)
                             + f"   [detect: ตรวจ {det['passes']} รอบ  รอบละ ~{det['pass_ms']:.1f}ms  เฟรมที่เจอ→ได้ผล {det['frame_to_found_ms']:.1f}ms]")
                    goto_next()
                    continue
            if tgt["item"] is not None and now - tgt["chk"] >= 0.05:
                tgt["chk"] = now                                       # ชิ้นนี้ว่างแล้ว (เก็บแล้ว หรือคนอื่นเก็บไปก่อนที่เมาส์จะถึง) / นานเกิน -> ไปชิ้นถัดไป
                it = tgt["item"]
                gone = (_get_score(img, ("x", it), scale) < GET_MATCH
                        and _is_empty_slot(img, it[0], it[1], it[2], it[3], scale))
                tgt["gone_n"] = tgt.get("gone_n", 0) + 1 if gone else 0   # ต้องว่างติดกัน 2 ครั้ง (กัน GET!/แอนิเมชันทำให้เข้าใจผิดว่าหาย)
                if tgt["gone_n"] >= 2 or now - tgt["t"] > 4.0:
                    goto_next()
                    continue
            if not timed_fired and now >= timed_due - TIMED_LEAD:       # ถึงเวลากดตั้งเวลา: เช็กหัวข้อสดแล้ว spin รอเวลาเป๊ะ
                batch = [timed_times[timed_i]]                           # เวลาที่ห่างกันไม่เกิน 60 ms กดต่อเนื่องในรอบเดียว (ไม่ log คั่นกลาง)
                while timed_i + len(batch) < len(timed_times) and timed_times[timed_i + len(batch)] - batch[0] <= 60:
                    batch.append(timed_times[timed_i + len(batch)])
                timed_i += len(batch)
                timed_fired = timed_i >= len(timed_times)
                if not timed_fired:
                    timed_due = t0 + timed_times[timed_i] / 1000
                first_due = t0 + batch[0] / 1000
                if now > first_due + 0.05:
                    log.info(f"  {ms():7.1f}ms  เลยเวลากดตั้งเวลา {_fmt_times(batch)}ms ไปแล้ว -> ข้าม (ใช้ตามสีแทน)")
                elif _header_score(screen.grab(box), scale) < HEADER_MATCH:
                    log.info(f"  {ms():7.1f}ms  ไม่เห็นหัวข้อก่อนกดตั้งเวลา -> ไม่กด")
                else:
                    groups += 1
                    fired = []
                    for t_ms in batch:
                        due = t0 + t_ms / 1000
                        while time.perf_counter() < due:
                            time.sleep(0)
                        tt, _ = send_click(groups)
                        fired.append((tt, t_ms, done, slot_at(ck.get_cursor())))
                    for tt, t_ms, n_click, sl in fired:
                        click_info.append((n_click, tt, f"ตั้งเวลา {t_ms:g}", sl))
                        log_click("ตั้งเวลา", tt, n_click, f"(ตั้งไว้ {t_ms:g}ms)")
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
            elif not _is_empty_slot(img, item[0], item[1], item[2], item[3], scale):    # ช่องเป็นสีปกติและมีของ (เห็นเปลี่ยนจากทึบ หรือเมาส์เพิ่งมาถึงช่องที่เป็นสีปกติอยู่แล้ว)
                reason = "ทึบ→พร้อม" if was_dim else "สีปกติ"
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
        det["stop"] = True
        if rec is not None:
            rec.finish(log)
            assist_round.verdicts = rec.verdicts
        assist_round.clicks = click_info
        _summarize_clicks(screen, click_info, rec.verdicts if rec is not None else None, cap0)


def run_assist(argv):
    dry_run = "--dry" in argv
    record = bool(config.RECORD)
    timed_times = click_times()
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
    log.info(f"[assist] เริ่มทำงาน ({'บอทลากเมาส์ไปครอบ ' + ', '.join(names) + ' และ' if names else 'คุณคุมเมาส์ บอท'}กด{f'ที่ {_fmt_times(timed_times)}ms และ' if timed_times else ''}ตามสีช่อง/GET!)  dry_run={dry_run}  กดค้าง {config.CLICK_HOLD_MS}ms  "
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
