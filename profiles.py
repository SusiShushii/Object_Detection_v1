"""
profiles.py - โปรไฟล์ต่อดัน: รวมไอเท็มที่จะเก็บ + ลำดับความสำคัญไว้ในไฟล์เดียว

    profiles/Water_Dungeon/priority.txt

        # เรียงจากสำคัญที่สุดลงมา (ชื่อ = ชื่อไฟล์รูป ไม่ต้องมี .png)
        Water_Ore
        Chopper
        Water_Bow

รัน:  python main.py water_dungeon        (ชื่อโปรไฟล์ ไม่สนตัวพิมพ์เล็ก/ใหญ่)
      python main.py carrot               (ชื่อไอเท็มโดยตรง ใช้ได้เหมือนเดิม)
      python main.py water_dungeon carrot (ผสมได้ ลำดับตามที่พิมพ์ โปรไฟล์ก่อน/หลังก็ได้)

รูปไอเท็ม: ค้นในโฟลเดอร์โปรไฟล์ก่อน แล้วค่อย templates/ (ใส่รูปไว้ข้างๆ priority.txt ได้ถ้าอยากแยกตามดัน)
รูปใน templates/ ที่ไม่อยู่ใน priority.txt จะไม่ถูกเก็บ
"""

import os
import re

from object_detection import item_name, list_templates, use_template_dirs

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILES_DIR = os.path.join(BASE_DIR, "profiles")
PRIORITY_FILE = "priority.txt"

_NUMBER_PREFIX = re.compile(r"^\s*\d+\s*[.):]\s*|^\s*\d+\s+")      # "1 Water_Ore" / "2. Chopper" / "3) Bow"


def _key(name):
    return name.strip().lower().replace(" ", "_")


def list_profiles():
    if not os.path.isdir(PROFILES_DIR):
        return []
    return sorted(d for d in os.listdir(PROFILES_DIR) if os.path.isdir(os.path.join(PROFILES_DIR, d)))


def find_profile(arg):
    """ชื่อโปรไฟล์ที่ตรงกับ arg (ไม่สนตัวพิมพ์เล็ก/ใหญ่ ช่องว่าง = _) หรือ None"""
    for name in list_profiles():
        if _key(name) == _key(arg):
            return name
    return None


def parse_priority(path):
    """อ่าน priority.txt -> รายชื่อไอเท็มตามลำดับบรรทัด (ข้างบนสำคัญกว่า)"""
    names = []
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()           # ตัดคอมเมนต์
            if not line:
                continue
            line = _NUMBER_PREFIX.sub("", line, count=1).strip()
            if line.lower().endswith(".png"):
                line = line[:-4]
            if line:
                names.append(line)
    return names


def resolve(args):
    """แปลง arguments จาก command line เป็นรายชื่อไอเท็มตามลำดับความสำคัญ

    arg ที่ตรงกับชื่อโฟลเดอร์ใน profiles/ = โปรไฟล์ (อ่าน priority.txt)  นอกนั้น = ชื่อไอเท็ม
    คืน dict: names (ชื่อไอเท็มจริงตามลำดับ), missing (ชื่อที่หารูปไม่เจอ), profiles (ชื่อโปรไฟล์ที่ใช้),
             errors (ข้อความผิดพลาดที่ต้องหยุด)
    """
    requested, profiles, profile_dirs, errors = [], [], [], []
    for arg in args:
        prof = find_profile(arg)
        if not prof:
            requested.append(arg)
            continue
        pdir = os.path.join(PROFILES_DIR, prof)
        pfile = os.path.join(pdir, PRIORITY_FILE)
        profiles.append(prof)
        profile_dirs.append(pdir)
        if not os.path.isfile(pfile):
            errors.append(f"โปรไฟล์ '{prof}' ไม่มีไฟล์ {PRIORITY_FILE} (ต้องวางไว้ที่ {pfile})")
            continue
        listed = parse_priority(pfile)
        if not listed:
            errors.append(f"{PRIORITY_FILE} ของโปรไฟล์ '{prof}' ว่างเปล่า (เขียนชื่อไอเท็มบรรทัดละ 1 ชื่อ)")
        requested.extend(listed)

    use_template_dirs(profile_dirs)                         # รูปในโฟลเดอร์โปรไฟล์ใช้ได้ด้วย

    available = {_key(item_name(t)): item_name(t) for t in list_templates()}
    names, missing = [], []
    for r in requested:
        actual = available.get(_key(r))
        if actual is None:
            if r not in missing:
                missing.append(r)
        elif actual not in names:
            names.append(actual)
    return {"names": names, "missing": missing, "profiles": profiles, "errors": errors,
            "available": sorted(set(available.values()))}
