"""
make_variant.py - สร้าง variant_round.py จาก assist_round ของ production (src/detector/collector.py)

variant_round.py = สำเนา assist_round ที่เพิ่มพารามิเตอร์ mode เพื่อเทียบ 3 แบบในโค้ดชุดเดียวกัน:
    old       หาไอเท็มแบบเก่า: ในลูปหลัก ทุก 50 ms เทียบ template ทั้งภาพ (ck._detect_all)  + เช็กหัวข้อก่อนกดตั้งเวลาด้วยการจับภาพใหม่
    new       แบบ production ตอนนี้: เธรดแยก + เทียบเฉพาะรอบ 21 ช่อง (_detect_slots)       + เช็กหัวข้อก่อนกดตั้งเวลาด้วยการจับภาพใหม่
    new_fast  เหมือน new แต่เช็กหัวข้อก่อนกดตั้งเวลาจากเฟรมที่จับไว้แล้วในลูป (ไม่จับภาพใหม่ -> กดตามเวลาไม่ช้าเพราะรอเฟรม)
production เปลี่ยนแล้วอยากให้สำเนาตามทัน: python test_assist_and_react/detect_ab/make_variant.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(ROOT, "src", "detector", "collector.py")
OUT = os.path.join(HERE, "variant_round.py")


def sub(text, old, new):
    if text.count(old) != 1:
        sys.exit(f"แพตช์ไม่ได้ (เจอ {text.count(old)} ที่): {old[:70]!r}  -> production เปลี่ยนไปแล้ว ต้องแก้ make_variant.py")
    return text.replace(old, new)


def main():
    s = open(SRC, encoding="utf-8").read()
    i = s.index("def assist_round(")
    j = s.index("def run_assist(")
    body = s[i:j].rstrip() + "\n"

    body = sub(body, "def assist_round(screen, dry_run=False, record=False, round_no=1, names=None):",
               'def assist_round_variant(screen, dry_run=False, record=False, round_no=1, names=None, mode="new", single=False, times=None):')
    body = body.replace("assist_round.", "assist_round_variant.")
    body = body.replace("from .recorder import", "from detector.recorder import")        # import แบบ relative ใช้นอกแพ็กเกจไม่ได้
    body = re.sub(r"from \.(\w+) import", r"from detector. import", body)

    # metrics
    body = sub(body, '    det = {"frame": None,', '    m = {"found_ms": None}\n    last_detect = 0.0\n    det = {"frame": None,')
    body = sub(body, "    if pending:\n        threading.Thread(", '    if pending and mode != "old":\n        threading.Thread(')

    # detect: old (ลูปหลัก ทุก 50 ms ทั้งภาพ) / new (เธรดแยก)
    body = sub(body, '                det["frame"] = (img.copy(), now)\n',
               '''                if mode == "old":
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
''')
    body = sub(body, "                    pending = False\n                    queue.extend(found)\n",
               '                    pending = False\n                    m["found_ms"] = ms()\n                    queue.extend(found)\n')

    # เช็กหัวข้อก่อนกดตั้งเวลา
    body = sub(body, "elif _header_score(screen.grab(box), scale) < HEADER_MATCH:",
               'elif _header_score(img if mode == "new_fast" else screen.grab(box), scale) < HEADER_MATCH:')

    # single = ms เท่านั้น: กดตามเวลาที่ระบุ (times) อิงไอเท็มชิ้นเดียว ไม่กดตามสี ไม่ไปชิ้นอื่น
    body = sub(body, "    timed_times = click_times()\n", "    timed_times = sorted(float(t) for t in times) if times else click_times()\n")
    body = sub(body, "                    queue.extend(found)\n", "                    queue.extend(found[:1] if single else found)\n")
    body = sub(body, "            idx = slot_at(cur)\n            if idx != cur_idx:",
               "            if single and timed_fired:\n                continue                                         # ms เท่านั้น: กดตามเวลาแล้วไม่ทำอะไรอีก\n"
               "            idx = slot_at(cur)\n            if idx != cur_idx:")

    # เก็บ metrics ตอนจบรอบ
    body = sub(body, "        assist_round_variant.clicks = click_info\n",
               '''        assist_round_variant.clicks = click_info
        v1 = next((v for v in (rec.verdicts if rec is not None else []) if v[0] == 1), None)
        mm = re.search(r"ช่องว่างที่ \\+(\\d+)ms", v1[3]) if v1 else None
        first = click_info[0] if click_info else None
        assist_round_variant.metrics = {
            "mode": mode, "single": single, "found_ms": m["found_ms"], "passes": det["passes"], "pass_ms": det["pass_ms"],
            "frame_to_found_ms": det["frame_to_found_ms"], "timed": list(timed_times),
            "first_click_ms": first[1] if first else None, "first_method": first[2] if first else None,
            "first_ok": (v1[2] if v1 else None), "first_empty_after_ms": float(mm.group(1)) if mm else None,
        }
''')

    head = '''"""variant_round.py - สร้างอัตโนมัติจาก production assist_round ด้วย make_variant.py (ห้ามแก้ตรงนี้ แก้ make_variant.py แล้วรันใหม่)"""
import re
import threading

import detector.collector as _prod

globals().update({k: v for k, v in vars(_prod).items() if not k.startswith("__")})   # ใช้ชื่อ/ตัวช่วยเดียวกับ production (ck, log, _detect_slots, ...)


'''
    open(OUT, "w", encoding="utf-8", newline="\n").write(head + body)
    print("เขียน", os.path.relpath(OUT, ROOT))


if __name__ == "__main__":
    main()
