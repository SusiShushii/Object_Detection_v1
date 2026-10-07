"""
main.py - จุดเริ่มต้นโปรแกรม (รันไปเรื่อยๆ ไม่มี timeout)

ขั้นตอนแต่ละรอบ:
    1. รอหน้า Mission Result -> เจอไอเท็มตอนสีเทา
    2. hover รอ -> ไอเท็มขึ้นสีปกติ -> คลิก
    3. รอหน้า Mission Result ปิด แล้วเริ่มรอบใหม่

หยุด: กด ESC ค้าง หรือ Ctrl+C
    python main.py                       -> คลิกทุกไอเท็มใน TARGETS
    python main.py skybug_spike          -> คลิกเฉพาะที่ระบุ (ใส่ได้หลายชื่อ)
    python main.py skybug_spike --dry    -> ไม่คลิก แค่ log
"""

import sys

from click import ClickAborted, wait_and_click, wait_gone
from object_detection import item_name, list_templates, log
from screen import Screen

# ไอเท็มที่ต้องการคลิก: None = ทุกไฟล์ในโฟลเดอร์ templates/
# หรือระบุเอง เช่น ["carrot", "skybug_spike"]
TARGETS = None


def main():
    dry_run = "--dry" in sys.argv
    targets = [a for a in sys.argv[1:] if not a.startswith("--")] or TARGETS

    unknown = [t for t in targets or [] if t not in {item_name(n) for n in list_templates()}]
    if unknown:
        print(f"ไม่พบไอเท็ม {unknown} ในโฟลเดอร์ templates/")
        print(f"ที่มี: {sorted({item_name(n) for n in list_templates()})}")
        return

    screen = Screen()
    rounds, total_clicked = 0, 0

    log.info("#" * 60)
    log.info(f"[main] เริ่มทำงาน  targets={targets or 'ทั้งหมด'}  dry_run={dry_run}")
    log.info("[main] กด ESC ค้าง หรือ Ctrl+C เพื่อหยุด")

    try:
        while True:
            clicked = wait_and_click(screen, targets, timeout=None, dry_run=dry_run)
            rounds += 1
            total_clicked += clicked
            log.info(f"[main] จบรอบที่ {rounds}  คลิก {clicked}  (รวมทั้งหมด {total_clicked})")
            wait_gone(screen, targets)
    except (ClickAborted, KeyboardInterrupt):
        log.info(f"[main] หยุดทำงาน  {rounds} รอบ  คลิกรวม {total_clicked}")


if __name__ == "__main__":
    main()
