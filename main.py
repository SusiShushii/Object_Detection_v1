"""
main.py - เก็บของหน้า Mission Result อัตโนมัติ (รันไปเรื่อยๆ จนกด F12 ค้าง หรือ Ctrl+C)

    python main.py carrot                 เก็บเฉพาะไอเท็มที่ระบุ (ใส่ได้หลายชื่อ ชื่อแรกสำคัญสุด)
    python main.py water_dungeon          ใช้โปรไฟล์ profiles/Water_Dungeon/priority.txt
    python main.py                        เก็บทุกไอเท็มใน templates/ (หรือตาม TARGETS ใน config.py)
    python main.py carrot --dry           ไม่คลิกจริง แค่ log

ตั้งค่าจังหวะกด / กดค้าง / ปุ่มหยุด ที่ config.py   ผล log: detection.log
"""

import gc
import sys
import time
import traceback

import config
from click import STOP_KEY_NAME, ClickAborted, tune_realtime, wait_and_click, wait_gone
from object_detection import log
from profiles import list_profiles, resolve
from screen import Screen


def main():
    dry_run = "--dry" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")] or config.TARGETS or []

    info = resolve(args)                     # ชื่อโปรไฟล์ -> อ่าน priority.txt  นอกนั้นเป็นชื่อไอเท็ม
    for e in info["errors"]:
        print(f"ผิดพลาด: {e}")
    if info["errors"]:
        return
    if info["missing"]:
        print(f"ไม่พบรูปไอเท็ม {info['missing']} (ต้องมีไฟล์ชื่อนี้.png ใน templates/ หรือโฟลเดอร์โปรไฟล์)")
        print(f"ที่มี: {info['available']}")
        print(f"โปรไฟล์ที่มี: {list_profiles() or '(ยังไม่มี)'}")
        if not info["names"]:
            return
        print("-> เก็บเฉพาะที่หารูปเจอต่อไป")
    targets = info["names"] or None          # None = ทุกไอเท็มใน templates/

    screen = Screen()
    tuned = tune_realtime()
    rounds, total_clicked, errors = 0, 0, 0

    w, h = screen.get_screen_rect()[2:4]
    timing = (f"กดที่ {config.CLICK_AT_MS}ms หลังหัวข้อขึ้น" if config.CLICK_AT_MS is not None else "กดตามสีเปลี่ยน")
    log.info("#" * 60)
    log.info(f"[main] เริ่มทำงาน  dry_run={dry_run}  จอ={w}x{h}  {timing}  กดค้าง {config.CLICK_HOLD_MS}ms")
    if info["profiles"]:
        log.info(f"[main] โปรไฟล์: {', '.join(info['profiles'])}")
    log.info(f"[main] ลำดับความสำคัญ: {' > '.join(targets) if targets else 'ทุกไอเท็มใน templates/'}")
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
                screen = Screen()            # สร้างตัวจับภาพจอใหม่ (เผื่อ error มาจากจอ)
            except KeyboardInterrupt:
                log.info(f"[main] หยุดทำงาน (Ctrl+C)  {rounds} รอบ  คลิกรวม {total_clicked}  error {errors} ครั้ง")
                break


if __name__ == "__main__":
    main()
