"""
run_test.py - ชุดทดสอบ/debug โหมด assist และ react แบบแยกส่วน (ไม่ใช่ production)

production (python main.py assist) = กดตามเวลา CLICK_AT_MS + กดตามสี รวมกันโหมดเดียว ตั้งค่าที่ config.py ที่เดียว
โฟลเดอร์นี้เก็บโหมดเดิมที่แยกกันไว้ เผื่อมาวัด/เทียบ/debug  ใช้ snapshot ของโค้ดเดิมใน legacy_collector.py (ไม่เปลี่ยนตาม production)

    python test_assist_and_react/run_test.py assist 3988                  กดตามเวลาอย่างเดียว (2 ครั้งห่าง RETRY_AFTER_MS, --once = ครั้งเดียว)
    python test_assist_and_react/run_test.py assist --react               กดตามสีอย่างเดียว (มีกดซ้ำกันพลาด)
    python test_assist_and_react/run_test.py assist 3979 --react          ตามเวลา + ตามสี (เหมือน production แต่ตั้งเวลาจากคำสั่ง)
    python test_assist_and_react/run_test.py assist 3979 --react --ab     สลับสุ่มรอบละวิธี แล้วสรุปว่าอันไหนเก็บติด/เร็วกว่า
    python test_assist_and_react/run_test.py assist --first 3975,3979,3985   ทดสอบเวลาคลิกแรก  (--second / --after ดูใน legacy_collector.py)
    python test_assist_and_react/run_test.py collect                      เก็บทุกช่องตามเวลา CLICK_AT_MS ไม่ detect ไอเท็ม
ตัวเลือกร่วม: --record (วิดีโอ+วิเคราะห์)  --dry (ไม่คลิกจริง)  --capture wgc|mss (หรือ --wgc / --mss)  หยุด: F12 ค้าง / Ctrl+C
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (os.path.join(ROOT, "src"), ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

import config  # noqa: E402

if isinstance(config.CLICK_AT_MS, (list, tuple)):          # โหมดเดิมใช้เวลาเดียว: เอาตัวแรกสุด
    config.CLICK_AT_MS = min(config.CLICK_AT_MS) if config.CLICK_AT_MS else None

# ค่าที่เฉพาะโหมดทดสอบ (ไม่อยู่ใน config.py ของ production)
for _k, _v in (("REACT_BACKUP_MS", 20), ("REACT_RETRY_MS", 350), ("COLLECT_GAP_MS", 60), ("COLLECT_MAX_PASSES", 2),
               ("MOUSE_JUMP", False), ("RECORD", False)):
    if not hasattr(config, _k):
        setattr(config, _k, _v)


def pop_capture(rest):
    out, backend, i = [], None, 0
    while i < len(rest):
        a = rest[i]
        if a in ("--capture", "--backend") and i + 1 < len(rest):
            backend, i = rest[i + 1].lower(), i + 2
        elif a.startswith(("--capture=", "--backend=")):
            backend, i = a.split("=", 1)[1].lower(), i + 1
        elif a in ("--wgc", "--mss"):
            backend, i = a[2:], i + 1
        else:
            out.append(a)
            i += 1
    return out, backend


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help", "help") or args[0] not in ("assist", "collect"):
        print(__doc__)
        return
    cmd, rest = args[0], args[1:]
    rest, backend = pop_capture(rest)
    if backend:
        if backend not in ("wgc", "mss"):
            print(f"--capture ต้องเป็น wgc หรือ mss (ได้ '{backend}')")
            return
        config.CAPTURE_BACKEND = backend
    import legacy_collector as lc
    (lc.run_assist if cmd == "assist" else lc.run_collect)(rest)


if __name__ == "__main__":
    main()
