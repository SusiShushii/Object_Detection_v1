"""variant_round.py - สร้างอัตโนมัติจาก production assist_round ด้วย make_variant.py (ห้ามแก้ตรงนี้ แก้ make_variant.py แล้วรันใหม่)"""
import re
import threading

import detector.collector as _prod

globals().update({k: v for k, v in vars(_prod).items() if not k.startswith("__")})   # ใช้ชื่อ/ตัวช่วยเดียวกับ production (ck, log, _detect_slots, ...)


def assist_round_variant(screen, dry_run=False, record=False, round_no=1, names=None, mode="new", single=False, times=None):
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

    m = {"found_ms": None}
    last_detect = 0.0
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
    if pending and mode != "old":
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
    assist_round_variant.verdicts = []
    timed_times = sorted(float(t) for t in times) if times else click_times()
    cap0 = getattr(screen, "fallbacks", 0)
    click_info = []                                               # (คลิกที่, ms, วิธี, ช่อง)
    log.info("=" * 60)
    log.info("[assist] เห็น Mission Reward  " + (f"กดตั้งเวลาที่ {_fmt_times(timed_times)}ms + " if timed_times else "")
             + "เฝ้าช่องใต้เมาส์: กดทันทีเมื่อ (ทึบ→พร้อม) หรือ (เห็น GET!)"
             + f"  จับภาพ={screen.backend}"
             + f"  กดค้าง {config.CLICK_HOLD_MS}ms  (" + (f"บอทลากเมาส์ไปครอบ {', '.join(priority)}" if names else "เมาส์คุณเอง ไม่ขยับให้") + ")"
             + ("  [dry_run]" if dry_run else "") + ("  [บันทึกวิดีโอ]" if record else ""))

    from detector.recorder import RoundRecorder
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
                if mode == "old":
                    if now - last_detect >= 0.05:
                        last_detect = now
                        t_a = time.perf_counter()
                        res = ck._detect_all(img, tnames, scale)
                        t_b = time.perf_counter()
                        det["passes"] += 1
                        det["pass_ms"] = (t_b - t_a) * 1000
                        if res:
                            det["frame_to_found_ms"] = (t_b - now) * 1000
                            det["found"] = res
                else:
                    det["frame"] = (img.copy(), now)
                found = sorted(det["found"], key=rank) if det["found"] else None
                if found:
                    pending = False
                    m["found_ms"] = ms()
                    queue.extend(found[:1] if single else found)
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
                elif _header_score(img if mode == "new_fast" else screen.grab(box), scale) < HEADER_MATCH:
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
            if single and timed_fired:
                continue                                         # ms เท่านั้น: กดตามเวลาแล้วไม่ทำอะไรอีก
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
            assist_round_variant.verdicts = rec.verdicts
        assist_round_variant.clicks = click_info
        v1 = next((v for v in (rec.verdicts if rec is not None else []) if v[0] == 1), None)
        mm = re.search(r"ช่องว่างที่ \+(\d+)ms", v1[3]) if v1 else None
        first = click_info[0] if click_info else None
        assist_round_variant.metrics = {
            "mode": mode, "single": single, "found_ms": m["found_ms"], "passes": det["passes"], "pass_ms": det["pass_ms"],
            "frame_to_found_ms": det["frame_to_found_ms"], "timed": list(timed_times),
            "first_click_ms": first[1] if first else None, "first_method": first[2] if first else None,
            "first_ok": (v1[2] if v1 else None), "first_empty_after_ms": float(mm.group(1)) if mm else None,
        }
        _summarize_clicks(screen, click_info, rec.verdicts if rec is not None else None, cap0)
