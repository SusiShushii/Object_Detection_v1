"""
main.py - จุดเริ่มต้นเดียวของโปรแกรม (รันบอท + เครื่องมือทั้งหมด)

    python main.py                       เมนู: เลือกเลขว่าจะทำอะไร
    python main.py run carrot            รันบอทเก็บของ (ชื่อไอเท็ม/โปรไฟล์ได้หลายชื่อ  --dry = ไม่คลิกจริง)
    python main.py assist [3980]         คุณคุมเมาส์เอง บอทกดคลิกให้ตรงจังหวะ
    python main.py collect               เก็บทุกช่องตามเวลา ไม่ detect ไอเท็ม + บันทึก loot
    python main.py carrot                เหมือน run carrot (พิมพ์ชื่อไอเท็มตรงๆ ได้ เหมือนเดิม)
    python main.py wgc --check [--record] เช็ก WGC อ่านภาพจาก process เกมได้ไหม + fps จริง + record ค้างไว้
    python main.py region [shot.png]     เช็กกรอบจับภาพตรงหน้า Mission Reward ไหม + วาดกรอบ
    python main.py prepare [--apply]     เตรียมรูปไอเท็มที่ครอปเอง  data/inbox -> assets/templates
    python main.py crop                  ครอปรูปไอเท็มจาก output/loot
    python main.py test รูป.png ชื่อ      ทดสอบ detect บนรูปภาพ
    python main.py timing                จับเวลาหน้า Mission Reward / ช่องเปลี่ยนสี (ไม่คลิก)
    python main.py analyze               สรุปสถิติจาก timing.log
    python main.py clickat 3988 --slot 1 ทดสอบกดที่เวลา X ms
    python main.py fps                   วัดรอบอัปเดตจอ / ความเร็วจับภาพ
    ตัวเลือกวิธีจับภาพ (ใช้กับ run / assist / collect):  --capture wgc | --capture mss   (หรือ --wgc / --mss)
                                  เช่น python main.py assist --react --record --capture mss
    python main.py <คำสั่ง> --help        วิธีใช้ของแต่ละคำสั่ง   (python main.py help = รายการนี้)

ตั้งค่าจังหวะกด / ปุ่มหยุด ที่ config.py   ผลลัพธ์ทั้งหมดอยู่ที่ output/ (log ที่ output/logs/)
"""

import os
import runpy
import shlex
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.join(ROOT, "src"), ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

# คำสั่ง -> (คำอธิบาย, โมดูล (None = บอทหลัก), คำใบ้การใส่ตัวเลือกในเมนู)
COMMANDS = {
    "run": ("รันบอทเก็บของหน้า Mission Result", None,
            "ชื่อไอเท็ม/โปรไฟล์ เช่น  carrot  หรือ  water_dungeon  (เว้นว่าง = ทุกไอเท็ม)   เพิ่ม --dry = ไม่คลิกจริง"),
    "collect": ("เก็บทุกช่องตามเวลา CLICK_AT_MS โดยไม่ detect ไอเท็ม (ไม่มี false negative) + บันทึกภาพ loot", None,
                "เว้นว่าง = รันจริง   --dry = ไม่คลิกจริง   (ตั้งเวลา/กดค้างใน config.py)"),
    "assist": ("คุณคุมเมาส์/เลือกช่องเอง บอทแค่กดคลิกให้ตรงจังหวะ (ที่เวลา CLICK_AT_MS หรือที่ระบุ)", None,
               "เวลา(ms) เช่น  3980   (เว้นว่าง = ค่าใน config.py)   --once = กดครั้งเดียว   --react = กดตามสีช่อง/GET! (เมาส์คุณเอง)   --record = บันทึกวิดีโอ+วิเคราะห์   --first 3995,4000,4005 = ทดสอบเวลาคลิกแรก   --second 4990,5000 = ทดสอบเวลาคลิกที่ 2 (นับจากหัวข้อ)   --after 1002,1012 = ทดสอบคลิกที่ 2 ห่างจากคลิกแรก (ms)   --dry"),
    "region": ("เช็กว่ากรอบจับภาพตรงหน้า Mission Reward ไหม + วาดกรอบให้ดู (ไม่คลิก)", "tools.check_region",
               "เว้นว่าง = จับจอตอนนี้ (เปิดหน้า Mission Result ไว้)   หรือใส่ไฟล์ภาพแคปจอ เช่น  shot.png"),
    "wgc": ("เช็กว่า WGC อ่านภาพจาก process เกมได้ไหม + วัด fps จริง + record ค้างไว้ (ไม่คลิก)", "tools.wgc_check",
            "--check   (+ --record = วิดีโอกรอบบอท  --full = ทั้งหน้าต่างเกม  --seconds 30 = เช็กแค่ 30 วิ)"),
    "prepare": ("เตรียมรูปไอเท็มที่ครอปเอง (จอ 2K/1080p ปนกัน) ใส่ data/inbox แล้วตรวจ+ปรับเป็น 62x65", "tools.prepare_templates",
                "เว้นว่าง = ตรวจรูปใน data/inbox   --apply = บันทึกลง assets/templates   --src 1080 / --force"),
    "crop": ("ครอปรูปไอเท็มอัตโนมัติจากภาพที่บอทบันทึก (output/loot)", "tools.crop_templates",
             "เว้นว่าง = ครอปจาก output/loot   --min 0.8 = เกณฑ์ว่าเป็นไอเท็มเดียวกัน"),
    "test": ("ทดสอบ detect ไอเท็มบนรูปภาพ (ไม่แตะเมาส์/จอ)", "tools.test_detect",
             "รูป ตามด้วยชื่อไอเท็ม เช่น  output/loot/xxx.png blue"),
    "timing": ("จับเวลา Mission Reward ขึ้น / ช่องเปลี่ยนสี + วิดีโอ monitor (ไม่คลิก)", "tools.timing",
               "เว้นว่าง = เริ่มจับเวลา (F12 หยุด)   --no-window  --no-video"),
    "analyze": ("สรุปสถิติเวลาเปลี่ยนสีจาก output/logs/timing.log (แนะนำค่า CLICK_AT_MS)", "tools.analyze_timing",
                "เว้นว่าง = อ่าน output/logs/timing.log"),
    "clickat": ("ทดสอบกดที่เวลา X ms หลังหัวข้อขึ้น ว่ากดเข้าไหม", "tools.click_at",
                "เวลา(ms) เช่น  3988 --slot 1 --hold 2   หลายเวลา: 3985,3988,3992"),
    "fps": ("วัดรอบอัปเดตจอ (Hz) / ความเร็วจับภาพ", "tools.fps_probe", "เว้นว่าง"),
}


def pop_capture(rest):
    """แยกตัวเลือกวิธีจับภาพออกจากอาร์กิวเมนต์: --capture wgc|mss  หรือ  --wgc / --mss  คืน (อาร์กิวเมนต์ที่เหลือ, วิธีที่เลือก หรือ None)"""
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


def run_command(cmd, rest):
    desc, module, _ = COMMANDS[cmd]
    rest, backend = pop_capture(rest)
    if backend is not None:
        if backend not in ("wgc", "mss"):
            print(f"--capture ต้องเป็น wgc หรือ mss (ได้ '{backend}')")
            return
        import config
        config.CAPTURE_BACKEND = backend            # ใช้เฉพาะรอบนี้ ไม่แก้ไฟล์ config.py
        print(f"[main] วิธีจับภาพรอบนี้: {backend}")
    if cmd == "assist":
        if "--help" in rest or "-h" in rest:
            print(COMMANDS["assist"][2])
            return
        from detector.collector import run_assist
        run_assist(rest)
        return
    if cmd == "collect":
        if "--help" in rest or "-h" in rest:
            print(COMMANDS["collect"][2])
            return
        from detector.collector import run_collect
        run_collect(rest)
        return
    if cmd == "run":
        if "--help" in rest or "-h" in rest:
            print(COMMANDS["run"][2])
            return
        from detector.app import run
        run(rest)
        return
    sys.argv = [f"main.py {cmd}"] + rest
    runpy.run_module(module, run_name="__main__")


def show_help():
    print(__doc__)
    print("คำสั่ง:")
    for name, (desc, _, hint) in COMMANDS.items():
        print(f"  {name:<8} {desc}")
        print(f"           {hint}")


def menu():
    names = list(COMMANDS)
    while True:
        print("\n=== Object Detection ===")
        for i, name in enumerate(names, 1):
            print(f"  {i}. {name:<8} {COMMANDS[name][0]}")
        print("  0. ออก")
        try:
            pick = input("เลือกเลข: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return
        if pick in ("0", "q", "quit", "exit", ""):
            return
        if pick.isdigit() and 1 <= int(pick) <= len(names):
            cmd = names[int(pick) - 1]
        elif pick in COMMANDS:
            cmd = pick
        else:
            print("เลือกเลข 0-%d หรือพิมพ์ชื่อคำสั่ง" % len(names))
            continue
        print(f"\n[{cmd}] {COMMANDS[cmd][0]}\n  ตัวเลือก: {COMMANDS[cmd][2]}")
        try:
            line = input("ใส่ตัวเลือก (กด Enter ถ้าไม่ใส่): ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        try:
            rest = [a.strip('"') for a in shlex.split(line, posix=False)] if line else []
            run_command(cmd, rest)
        except KeyboardInterrupt:
            print("\n(หยุด)")
        except SystemExit:
            pass


def main():
    args = sys.argv[1:]
    if not args:
        menu()
        return
    first = args[0]
    if first in ("help", "-h", "--help"):
        show_help()
    elif first in COMMANDS:
        run_command(first, args[1:])
    else:                                  # python main.py carrot  = run carrot (เหมือนเดิม)
        run_command("run", args)


if __name__ == "__main__":
    main()
