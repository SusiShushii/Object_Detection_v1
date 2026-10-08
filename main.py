"""
main.py - จุดเริ่มต้นโปรแกรม (รันไปเรื่อยๆ ไม่มี timeout)

ขั้นตอนแต่ละรอบ:
    1. รอหน้า Mission Result -> เจอไอเท็ม
    2. hover รอ -> ไอเท็มพร้อมกด -> คลิก (เก็บทีละช่องตามลำดับความสำคัญ)
    3. รอหน้า Mission Result ปิด แล้วเริ่มรอบใหม่

ถ้าเกิด error กลางทาง (เช่น จับภาพจอไม่ได้ชั่วคราว) -> บันทึกลง detection.log แล้วทำงานต่อเอง

หยุด: กด F12 ค้าง หรือ Ctrl+C
    python main.py water_dungeon         -> ใช้โปรไฟล์ profiles/Water_Dungeon/priority.txt (ลำดับตามไฟล์)
    python main.py carrot                -> เก็บเฉพาะไอเท็มที่ระบุ (ใส่ได้หลายชื่อ ชื่อแรกสำคัญสุด)
    python main.py water_dungeon carrot  -> ผสมกันได้ (ลำดับตามที่พิมพ์)
    python main.py water_dungeon --dry   -> ไม่คลิก แค่ log
    python main.py                       -> คลิกทุกไอเท็มใน templates/
"""

import sys
import time
import traceback

from click import STOP_KEY_NAME, ClickAborted, wait_and_click, wait_gone
from object_detection import log
from profiles import list_profiles, resolve
from screen import Screen

# ไอเท็มที่ต้องการคลิก: None = ทุกไฟล์ในโฟลเดอร์ templates/
# หรือระบุเอง เช่น ["carrot", "skybug_spike"]
TARGETS = None

# เกิด error แล้วรอกี่วินาทีก่อนทำงานต่อ
ERROR_RETRY_DELAY = 2.0


def main():
    dry_run = "--dry" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")] or TARGETS or []

    # ชื่อโปรไฟล์ (โฟลเดอร์ใน profiles/) -> อ่าน priority.txt  นอกนั้นเป็นชื่อไอเท็ม
    info = resolve(args)
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
    targets = info["names"] or None             # None = ทุกไอเท็มใน templates/ (ไม่ได้ระบุอะไรเลย)

    screen = Screen()
    rounds, total_clicked, errors = 0, 0, 0

    log.info("#" * 60)
    log.info(f"[main] เริ่มทำงาน  dry_run={dry_run}  จอ={screen.get_screen_rect()[2]}x{screen.get_screen_rect()[3]}")
    if info["profiles"]:
        log.info(f"[main] โปรไฟล์: {', '.join(info['profiles'])}")
    log.info(f"[main] ลำดับความสำคัญ: {' > '.join(targets) if targets else 'ทุกไอเท็มใน templates/'}")
    if info["missing"]:
        log.info(f"[main] หมายเหตุ: หารูปไม่เจอ ข้ามไป: {', '.join(info['missing'])}")
    log.info(f"[main] กด {STOP_KEY_NAME} ค้าง หรือ Ctrl+C เพื่อหยุด")

    while True:
        try:
            clicked = wait_and_click(screen, targets, timeout=None, dry_run=dry_run)
            rounds += 1
            total_clicked += clicked
            log.info(f"[main] จบรอบที่ {rounds}  คลิก {clicked}  (รวมทั้งหมด {total_clicked})")
            wait_gone(screen, targets)
        except ClickAborted as e:
            log.info(f"[main] หยุดทำงาน ({e})  {rounds} รอบ  คลิกรวม {total_clicked}  error {errors} ครั้ง")
            break
        except KeyboardInterrupt:
            log.info(f"[main] หยุดทำงาน (Ctrl+C)  {rounds} รอบ  คลิกรวม {total_clicked}  error {errors} ครั้ง")
            break
        except Exception:
            # error กลางทาง -> บันทึกรายละเอียด แล้วเริ่มใหม่ (ไม่ปิดโปรแกรม)
            errors += 1
            log.info(f"[main] เกิด error ครั้งที่ {errors} -> ทำงานต่อใน {ERROR_RETRY_DELAY:.0f} วินาที\n"
                     + traceback.format_exc())
            try:
                time.sleep(ERROR_RETRY_DELAY)
                screen = Screen()       # สร้างตัวจับภาพจอใหม่ (เผื่อ error มาจากจอ)
            except KeyboardInterrupt:
                log.info(f"[main] หยุดทำงาน (Ctrl+C)  {rounds} รอบ  คลิกรวม {total_clicked}  error {errors} ครั้ง")
                break


if __name__ == "__main__":
    main()
