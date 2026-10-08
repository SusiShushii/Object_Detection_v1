"""
crop_templates.py - ครอปรูปไอเท็มจากภาพใน loot/ ให้อัตโนมัติ (ไว้ทำรูปใน templates/)

    python crop_templates.py                  -> อ่านภาพจาก loot/ ใส่ผลลัพธ์ที่ crops/
    python crop_templates.py loot_other       -> อ่านจากโฟลเดอร์อื่น
    python crop_templates.py --min 0.8        -> เกณฑ์ว่า "เป็นไอเท็มเดียวกัน" (ค่าเริ่มต้น 0.75)

ทำอะไร:
  - ครอปทุกช่องที่มีไอเท็มในภาพ loot/ (รู้ความละเอียดจากขนาดภาพ จึงใช้ได้กับภาพจากจอทุกขนาด
    และปรับเป็นขนาดมาตรฐานของจอ 2K = 62x65 ให้เสมอ)
  - ข้ามไอเท็มที่มีใน templates/ แล้ว  ข้ามรูปซ้ำกันเอง (รวมตอนทึบ/ตอนสีปกติของไอเท็มเดียวกัน)
  - ข้ามช่องว่าง, กล่อง "?", ช่องที่มีป้าย GET! ติดมา
  - บันทึกเป็น crops/new_001.png ... + crops/_sheet.png (ภาพรวมให้ดูว่ารูปไหนคืออะไร)

จากนั้นเปลี่ยนชื่อ new_001.png เป็นชื่อไอเท็ม (ไม่มีช่องว่าง ไม่มี "-") แล้วย้ายเข้า templates/
รูปที่ไม่ใช่ไอเท็มที่ต้องการเก็บ ลบทิ้งได้เลย
"""

import glob
import os
import sys

import cv2
import numpy as np

import click
from object_detection import BADGE_START, TEMPLATE_DIR, best_score, list_templates, same_colour

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CROP_DIR = os.path.join(BASE_DIR, "crops")

STD_W, STD_H = click.SLOT_WH                  # ขนาดช่องมาตรฐานที่จอ 2K (62, 65)
REGION_BASE_W = 525                           # ความกว้างกรอบ mission_reward ที่จอ 2K ก่อนบวก pad (695 -> 1220)
PAD = 5                                       # pad ของกรอบ (ไม่คูณ scale)
EXIST_MATCH = 0.85                            # คะแนนเทียบกับ templates/ ที่ถือว่า "มีรูปนี้แล้ว"
LOOT_BOX_MATCH = 0.75
TEXT_MAX = 0.012                              # สัดส่วนพิกเซลสีขาว (ตัวหนังสือ tooltip) ที่ยอมให้มีในช่อง


def _gray(a):
    return cv2.cvtColor(a, cv2.COLOR_BGR2GRAY) if a.ndim == 3 else a


def _top(gray):
    return gray[:int(gray.shape[0] * BADGE_START[1])]


def _edge(bgr):
    g = cv2.GaussianBlur(_gray(bgr), (3, 3), 0)
    m = cv2.magnitude(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))
    return m / (m.max() + 1e-6)


def _match(a, b):
    """a (ตัดส่วนบนไม่รวมตัวเลข) หาใน b (เผื่อขอบ 6px) -> คะแนนสูงสุด"""
    a = a[:int(a.shape[0] * BADGE_START[1])]
    b = cv2.copyMakeBorder(b, 6, 6, 6, 6, cv2.BORDER_REPLICATE)
    v = float(cv2.matchTemplate(b, a, cv2.TM_CCOEFF_NORMED).max())
    return -1.0 if np.isnan(v) else v


def similarity(a_bgr, b_bgr):
    """ความเหมือนของไอคอน 2 ใบ (0-1) เอาค่าสูงสุดของ 2 วิธี:
    - เทียบรูปทรงขาวดำ (ไอคอนเดียวกันความสว่างเท่ากัน)
    - เทียบเส้นขอบ (ไอคอนเดียวกันแต่ตอนทึบกับตอนสีปกติ ความสว่างต่างกัน)
    ถ้าสีคนละทิศทาง ถือว่าไม่เหมือน (รูปทรงคล้ายแต่คนละไอเท็ม เช่น หินสีส้ม vs สีเขียว)"""
    if not same_colour(a_bgr, b_bgr):
        return 0.0
    ga, gb = _gray(a_bgr), _gray(b_bgr)
    ea, eb = _edge(a_bgr), _edge(b_bgr)
    return max(_match(ga, gb), _match(gb, ga), _match(ea, eb), _match(eb, ea))


def image_scale(img):
    """เดาความละเอียดจอจากความกว้างภาพ loot (1.0 = จอ 2K)"""
    return max(0.2, (img.shape[1] - 2 * PAD) / REGION_BASE_W)


def slot_boxes(scale):
    (ox, oy), (px, py), (nc, nr), (sw, sh) = click.GRID_ORIGIN, click.GRID_PITCH, click.GRID_SIZE, click.SLOT_WH
    for r in range(nr):
        for c in range(nc):
            x = int(round(PAD + (ox + px * c) * scale))
            y = int(round(PAD + (oy + py * r) * scale))
            yield c, r, x, y, int(sw * scale), int(sh * scale)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    loot_dir = args[0] if args else os.path.join(BASE_DIR, "loot")
    min_same = 0.75
    if "--min" in sys.argv:
        min_same = float(sys.argv[sys.argv.index("--min") + 1])
        args = [a for a in args if a != sys.argv[sys.argv.index("--min") + 1]]
        loot_dir = args[0] if args else os.path.join(BASE_DIR, "loot")

    files = glob.glob(os.path.join(loot_dir, "*.png"))
    if not files:
        print(f"ไม่พบภาพใน {loot_dir}")
        return
    # ภาพตอนสีปกติก่อน (ละเอียดกว่าตอนทึบ) แล้วค่อยตอนทึบ ใหม่ก่อนเก่า
    files.sort(key=lambda f: ("_2_normal" not in f, -os.path.getmtime(f)))

    box = cv2.imread(os.path.join(BASE_DIR, "markers", "loot_box.png"))
    box = None if box is None else _top(_gray(box))
    existing = list_templates()

    os.makedirs(CROP_DIR, exist_ok=True)
    kept, skipped = [], {"ช่องว่าง": 0, "กล่อง ?": 0, "ป้าย GET!": 0, "มีตัวหนังสือ tooltip บัง": 0, "มีใน templates/ แล้ว": 0, "ซ้ำกับรูปที่ครอปแล้ว": 0}
    known_names = {}

    for f in files:
        img = cv2.imread(f)
        if img is None:
            continue
        scale = image_scale(img)
        for c, r, x, y, w, h in slot_boxes(scale):
            if y + h > img.shape[0] or x + w > img.shape[1]:
                continue
            if click._is_empty_slot(img, x, y, w, h, scale):
                skipped["ช่องว่าง"] += 1
                continue
            slot = img[y:y + h, x:x + w]
            # กล่อง "?" (random loot)
            if box is not None:
                b = box if scale == 1.0 else cv2.resize(box, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                g = _gray(img[max(0, y - 6):y + h + 6, max(0, x - 6):x + w + 6])
                if g.shape[0] >= b.shape[0] and g.shape[1] >= b.shape[1] \
                        and cv2.matchTemplate(g, b, cv2.TM_CCOEFF_NORMED).max() >= LOOT_BOX_MATCH:
                    skipped["กล่อง ?"] += 1
                    continue
            # ป้าย GET!
            if click._get_score(img, ("x", (x, y, w, h, 1)), scale) >= click.GET_MATCH:
                skipped["ป้าย GET!"] += 1
                continue

            # ตัวหนังสือชื่อไอเท็ม (tooltip ตอนเมาส์ hover) ทับช่อง -> รูปไม่สะอาด ข้าม (ไม่นับตัวเลขจำนวนมุมขวาล่าง)
            white = slot.min(axis=2) > 225
            white[int(h * BADGE_START[1]):, int(w * BADGE_START[0]):] = False
            if white.mean() > TEXT_MAX:
                skipped["มีตัวหนังสือ tooltip บัง"] += 1
                continue

            std = cv2.resize(slot, (STD_W, STD_H), interpolation=cv2.INTER_CUBIC if scale < 1 else cv2.INTER_AREA)

            # มีใน templates/ แล้ว?
            padded = cv2.copyMakeBorder(std, 8, 8, 8, 8, cv2.BORDER_REPLICATE)
            hit = next((n for n in existing if best_score(padded, n, 1.0) >= EXIST_MATCH), None)
            if hit:
                skipped["มีใน templates/ แล้ว"] += 1
                known_names[hit] = known_names.get(hit, 0) + 1
                continue
            # ซ้ำกับที่ครอปไว้แล้ว?
            if any(similarity(std, k["img"]) >= min_same for k in kept):
                skipped["ซ้ำกับรูปที่ครอปแล้ว"] += 1
                continue
            kept.append({"img": std, "src": os.path.basename(f), "slot": (c + 1, r + 1), "scale": scale})

    # ลบผลเก่า แล้วบันทึกใหม่
    for old in glob.glob(os.path.join(CROP_DIR, "new_*.png")) + glob.glob(os.path.join(CROP_DIR, "_sheet.png")):
        os.remove(old)
    cells = []
    for i, k in enumerate(kept, 1):
        name = f"new_{i:03d}.png"
        cv2.imwrite(os.path.join(CROP_DIR, name), k["img"])
        cell = cv2.copyMakeBorder(k["img"], 0, 18, 0, 0, cv2.BORDER_CONSTANT, value=(30, 30, 30))
        cv2.putText(cell, f"{i:03d}", (2, STD_H + 13), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
        cells.append(cell)
    if cells:
        per_row = 8
        while len(cells) % per_row:
            cells.append(np.full_like(cells[0], 30))
        rows = [np.hstack(cells[i:i + per_row]) for i in range(0, len(cells), per_row)]
        cv2.imwrite(os.path.join(CROP_DIR, "_sheet.png"), np.vstack(rows))

    print(f"อ่านภาพ {len(files)} ภาพจาก {loot_dir}")
    print(f"ครอปรูปใหม่ได้ {len(kept)} รูป -> {CROP_DIR}")
    for i, k in enumerate(kept, 1):
        print(f"  new_{i:03d}.png   จาก {k['src']}  ช่อง (คอลัมน์ {k['slot'][0]}, แถว {k['slot'][1]})  "
              f"ภาพต้นฉบับ scale {k['scale']:.2f}")
    print("ข้าม: " + ", ".join(f"{k} {v}" for k, v in skipped.items() if v))
    if known_names:
        print("ไอเท็มที่มีใน templates/ แล้ว: " + ", ".join(f"{n} (เจอ {c} ช่อง)" for n, c in known_names.items()))
    if kept:
        print("\nต่อไป: ดู crops/_sheet.png เปลี่ยนชื่อ new_XXX.png เป็นชื่อไอเท็ม แล้วย้ายเข้า templates/")


if __name__ == "__main__":
    main()
