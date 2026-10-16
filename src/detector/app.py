"""
app.py - ลูปหลักของบอท (เรียกผ่าน  python main.py run ...)

ขั้นตอนแต่ละรอบ: รอหน้า Mission Result -> เจอไอเท็ม -> กดตามจังหวะที่ตั้งใน config.py -> รอหน้าปิด -> เริ่มรอบใหม่
ถ้าเกิด error กลางทาง (เช่น จับภาพจอไม่ได้ชั่วคราว) -> บันทึกลง log แล้วทำงานต่อเอง
หยุด: กด F12 ค้าง (ตาม config.py) หรือ Ctrl+C
"""

import gc
import time
import traceback

import config

from .clicker import STOP_KEY_NAME, ClickAborted, tune_realtime, wait_and_click, wait_gone
from .detection import log
from .profiles import list_profiles, resolve
from .wgc import make_screen


def run(argv):
    """argv: ชื่อไอเท็ม/โปรไฟล์ + --dry"""
    dry_run = "--dry" in argv
    args = [a for a in argv if not a.startswith("--")] or config.TARGETS or []

    info = resolve(args)                     # ชื่อโปรไฟล์ -> อ่าน priority.txt  นอกนั้นเป็นชื่อไอเท็ม
    for e in info["errors"]:
        print(f"ผิดพลาด: {e}")
    if info["errors"]:
        return
    if info["missing"]:
        print(f"ไม่พบรูปไอเท็ม {info['missing']} (ต้องมีไฟล์ชื่อนี้.png ใน assets/templates/ หรือโฟลเดอร์โปรไฟล์)")
        print(f"ที่มี: {info['available']}")
        print(f"โปรไฟล์ที่มี: {list_profiles() or '(ยังไม่มี)'}")
        if not info["names"]:
            return
        print("-> เก็บเฉพาะที่หารูปเจอต่อไป")
    targets = info["names"] or None          # None = ทุกไอเท็มใน assets/templates/

    screen = make_screen()
    tuned = tune_realtime()
    rounds, total_clicked, errors = 0, 0, 0

    w, h = screen.get_screen_rect()[2:4]
    timing = (f"กดที่ {config.CLICK_AT_MS}ms หลังหัวข้อขึ้น" if config.CLICK_AT_MS is not None else "กดตามสีเปลี่ยน")
    log.info("#" * 60)
    log.info(f"[main] เริ่มทำงาน  dry_run={dry_run}  จอ={w}x{h}  {timing}  กดค้าง {config.CLICK_HOLD_MS}ms")
    if info["profiles"]:
        log.info(f"[main] โปรไฟล์: {', '.join(info['profiles'])}")
    log.info(f"[main] ลำดับความสำคัญ: {' > '.join(targets) if targets else 'ทุกไอเท็มใน assets/templates/'}")
    if info["missing"]:
        log.info(f"[main] หมายเหตุ: หารูปไม่เจอ ข้ามไป: {', '.join(info['missing'])}")
    log.info(f"[main] ลดความแกว่งของเวลา: {tuned}  กด {STOP_KEY_NAME} ค้าง หรือ Ctrl+C เพื่อหยุด")

    while True:
        try:
            clicked = wait_and_click(screen, targets, timeout=None, dry_run=dry_run)
            rounds += 1
            total_clicked += clicked
            log.info(f"[main] จบรอบที่ {rounds}  คลิก {clicked}  (รวมทั้งหมด {total_clicked})")
            wait_gone(screen, targets)
            gc.collect()                     # GC ปิดอยู่ตอนกด -> เก็บกวาดตอนจบรอบ (ไม่กระทบจังหวะกด)
        except ClickAborted as e:
            log.info(f"[main] หยุดทำงาน ({e})  {rounds} รอบ  คลิกรวม {total_clicked}  error {errors} ครั้ง")
            break
        except KeyboardInterrupt:
            log.info(f"[main] หยุดทำงาน (Ctrl+C)  {rounds} รอบ  คลิกรวม {total_clicked}  error {errors} ครั้ง")
            break
        except Exception:
            # error กลางทาง -> บันทึกรายละเอียด แล้วเริ่มใหม่ (ไม่ปิดโปรแกรม)
            errors += 1
            log.info(f"[main] เกิด error ครั้งที่ {errors} -> ทำงานต่อใน {config.ERROR_RETRY_DELAY:.0f} วินาที\n"
                     + traceback.format_exc())
            try:
                time.sleep(config.ERROR_RETRY_DELAY)
                screen.close()
                screen = make_screen()            # สร้างตัวจับภาพจอใหม่ (เผื่อ error มาจากจอ)
            except KeyboardInterrupt:
                log.info(f"[main] หยุดทำงาน (Ctrl+C)  {rounds} รอบ  คลิกรวม {total_clicked}  error {errors} ครั้ง")
                break
