"""
prepare_templates.py - เตรียมรูปไอเท็มที่ครอปเอง (จอ 2K / 1080p / ความละเอียดอื่นปนกันได้) ให้เป็นมาตรฐานเดียวกัน

    1. ใส่รูปครอป 1 รูป = 1 ไอเท็ม ลงโฟลเดอร์ inbox/   ตั้งชื่อไฟล์ = ชื่อไอเท็ม เช่น inbox/blue.png
       (ชื่อก่อนเครื่องหมาย "-" นับเป็นไอเท็มเดียวกัน เช่น blue.png + blue-dim.png)
    2. python main.py prepare             ตรวจ + รายงาน + ภาพสรุป inbox/_report.png (ยังไม่แตะ templates/)
    3. python main.py prepare --apply     ปรับเป็น 62x65 แล้วบันทึกลง templates/  (ไฟล์ต้นฉบับย้ายไป inbox/done/)

    --src 1080     บอกว่ารูปทั้งหมดครอปจากจอสูง 1080 (1440 = 2K, 2160 = 4K) ถ้าไม่ใส่ จะเดาจากขนาดรูป
    --force        ทับไฟล์ที่ชื่อซ้ำ / บันทึกแม้ถูกเตือนว่าซ้ำ-ช่องว่าง-สัดส่วนผิด
    --inbox DIR    ใช้โฟลเดอร์อื่นแทน inbox/      --out DIR   บันทึกที่อื่นแทน templates/

ขนาดมาตรฐาน = ช่องไอเท็มที่จอ 2K (2560x1440) = 62x65 px (1080p ≈ 46x49, 4K ≈ 93x98)
ครอปให้พอดีขอบช่อง (รวมกรอบช่องได้) ไม่ต้องเอาตัวเลขจำนวนมุมขวาล่าง (บอทไม่ใช้ส่วนล่าง 32% อยู่แล้ว)
รูปควรเป็นตอนสีปกติ ไม่มี tooltip / เมาส์ / ตัวหนังสือทับ
"""
import os
import shutil
import sys

import cv2
import numpy as np

from detector import detection as od
from detector import paths
from tools import crop_templates as ct

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

STD_W, STD_H = 62, 65
INBOX = paths.INBOX
EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".webp")
SCREENS = {"4K 3840x2160": 1.5, "2K 2560x1440": 1.0, "1080p 1920x1080": 0.75, "900p 1600x900": 0.625,
           "720p 1280x720": 0.5}
ASPECT_TOL = 0.12          # สัดส่วนกว้าง/สูงเทียบช่องมาตรฐานที่ยอมให้คลาด
MIN_SIDE = 24              # เล็กกว่านี้ถือว่าใช้ไม่ได้
WARN_SIDE = 40             # เล็กกว่านี้ภาพจะเบลอหลังขยาย
FLAT_STD = 12.0            # ความต่างของความสว่างต่ำกว่านี้ = เกือบเรียบ (ช่องว่าง/ครอปผิด?)
BLUR_VAR = 25.0            # ความคมต่ำกว่านี้ = เบลอ


def clean_name(stem):
    return stem.strip().lower().replace(" ", "_")


def root(name):
    """ชื่อไอเท็มจริง (ก่อน "-"): blue-dim -> blue"""
    return name.split("-")[0]


def flatten(img):
    """รูปมีช่องโปร่งใส -> แปะบนสีพื้นช่อง (เฉลี่ยจากรูปช่องว่าง)"""
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:
        ref = cv2.imread(os.path.join(paths.MARKERS, "empty_slot.png"))
        bg = ref.reshape(-1, 3).mean(0) if ref is not None else np.array([60, 70, 90.0])
        a = img[..., 3:4].astype(np.float32) / 255
        return (img[..., :3] * a + bg * (1 - a)).astype(np.uint8)
    return img


def guess_scale(w, h, forced=None):
    """คืน (scale, ข้อความบอกที่มา, สัดส่วนผิดปกติไหม)"""
    sx, sy = w / STD_W, h / STD_H
    aspect_bad = abs(sx - sy) / max(sx, sy) > ASPECT_TOL
    if forced:
        s = forced / 1440
        return s, f"ระบุ --src {forced:g} (scale {s:.3f})", aspect_bad
    s = (sx + sy) / 2
    name, ref = min(SCREENS.items(), key=lambda kv: abs(kv[1] - s))
    if abs(ref - s) / ref <= 0.07:
        return s, f"น่าจะครอปจากจอ {name} (scale {s:.2f})", aspect_bad
    return s, f"ขนาดไม่ตรงจอมาตรฐาน (scale ≈ {s:.2f})", aspect_bad


def standardize(img):
    h, w = img.shape[:2]
    interp = cv2.INTER_AREA if (w > STD_W or h > STD_H) else cv2.INTER_CUBIC
    return cv2.resize(img, (STD_W, STD_H), interpolation=interp)


EMPTY_WARN = 0.72          # คะแนนเทียบรูปช่องว่าง (markers/empty_slot.png) ที่เตือนว่าอาจเป็นช่องว่าง (บอทใช้ 0.85 ตอนรัน)


def empty_score(std):
    ref = cv2.imread(os.path.join(paths.MARKERS, "empty_slot.png"), cv2.IMREAD_GRAYSCALE)
    if ref is None:
        return 0.0
    pad = cv2.copyMakeBorder(cv2.cvtColor(std, cv2.COLOR_BGR2GRAY), 6, 6, 6, 6, cv2.BORDER_REPLICATE)
    if pad.shape[0] < ref.shape[0] or pad.shape[1] < ref.shape[1]:
        return 0.0
    v = float(cv2.matchTemplate(pad, ref, cv2.TM_CCOEFF_NORMED).max())
    return 0.0 if np.isnan(v) else v


def existing_templates(out_dir):
    items = []
    if os.path.isdir(out_dir):
        for f in sorted(os.listdir(out_dir)):
            if f.lower().endswith(EXTS):
                im = cv2.imread(os.path.join(out_dir, f), cv2.IMREAD_COLOR)
                if im is not None:
                    items.append((f, standardize(im)))
    return items


def check(path, forced, existing, out_dir, force):
    """ตรวจรูปหนึ่งรูป  คืน dict: name, std, orig, info(list ข้อความ), warns, errors, dup"""
    stem = os.path.splitext(os.path.basename(path))[0]
    name = clean_name(stem)
    r = dict(file=os.path.basename(path), name=name, warns=[], errors=[], info=[], std=None, orig=None, dup=None,
             junk=False, skip=False)
    raw = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if raw is None:
        r["errors"].append("เปิดรูปไม่ได้")
        return r
    if raw.ndim == 3 and raw.shape[2] == 4:
        r["warns"].append("มีพื้นหลังโปร่งใส -> แปะบนสีพื้นช่อง (ควรครอปจากภาพจริงในเกม)")
    img = flatten(raw)
    r["orig"] = img
    h, w = img.shape[:2]
    scale, src_txt, aspect_bad = guess_scale(w, h, forced)
    r["info"].append(f"{w}x{h} px  {src_txt}")
    if min(w, h) < MIN_SIDE:
        r["errors"].append(f"เล็กเกินไป ({min(w, h)} px) ใช้ไม่ได้ ครอปใหม่ให้ใหญ่ขึ้น")
    elif min(w, h) < WARN_SIDE:
        r["warns"].append(f"เล็ก ({min(w, h)} px) ภาพจะเบลอหลังขยายเป็น {STD_W}x{STD_H}")
    if aspect_bad:
        r["skip"] = True
        r["warns"].append(f"สัดส่วนไม่ใกล้ช่องไอเท็ม ({w / h:.2f} เทียบ {STD_W / STD_H:.2f}) "
                          f"ครอปเกิน/ขาด? จะถูกบีบให้เป็น {STD_W}x{STD_H}")
    if r["errors"]:
        return r
    std = standardize(img)
    r["std"] = std
    gray = cv2.cvtColor(std, cv2.COLOR_BGR2GRAY)
    if float(gray.std()) < FLAT_STD:
        r["warns"].append("เกือบเรียบสีเดียว อาจเป็นช่องว่างหรือครอปผิดที่")
        r["junk"] = r["skip"] = True
    elif empty_score(std) >= EMPTY_WARN:
        r["warns"].append(f"ดูเหมือนช่องว่าง (ไม่มีไอเท็ม) ความเหมือนช่องว่าง {empty_score(std):.2f}")
        r["junk"] = r["skip"] = True
    elif float(cv2.Laplacian(gray, cv2.CV_64F).var()) < BLUR_VAR:
        r["warns"].append("เบลอ/คอนทราสต์ต่ำ -> ทดสอบแล้วทำให้เจอผิดช่อง/คะแนนต่ำ")
        r["skip"] = True
    # ซ้ำกับของเดิม?
    best = (0.0, "")
    for f, other in existing:
        if root(clean_name(os.path.splitext(f)[0])) == root(name):          # ไอเท็มเดียวกัน (หลายรูป) ไม่นับซ้ำ
            continue
        s = ct.similarity(std, other)
        if s > best[0]:
            best = (s, f)
    if best[0] >= ct.EXIST_MATCH:
        r["dup"] = best
        r["warns"].append(f"เหมือนไอเท็มที่มีแล้ว {best[1]} (ความเหมือน {best[0]:.2f})")
    target = os.path.join(out_dir, name + ".png")
    if os.path.exists(target) and not force:
        r["errors"].append(f"มี {name}.png ใน {os.path.basename(out_dir)}/ แล้ว (ใช้ --force เพื่อทับ)")
    return r


def sheet(results, path):
    """ภาพสรุป: ต้นฉบับ | มาตรฐาน 62x65  พร้อมขนาดและผล"""
    rows = []
    for r in results:
        if r["orig"] is None:
            continue
        oh = 130
        o = r["orig"]
        o = cv2.resize(o, (max(1, int(o.shape[1] * oh / o.shape[0])), oh), interpolation=cv2.INTER_NEAREST)
        s = cv2.resize(r["std"], (int(STD_W * 2), int(STD_H * 2)), interpolation=cv2.INTER_NEAREST) if r["std"] is not None \
            else np.zeros((oh, 124, 3), np.uint8)
        row = np.full((oh + 8, 420, 3), 40, np.uint8)
        o = o[:, :200]
        row[4:4 + oh, 4:4 + o.shape[1]] = o
        x0 = 210
        row[4:4 + s.shape[0], x0:x0 + s.shape[1]] = s
        col = (0, 0, 255) if r["errors"] else ((0, 200, 255) if r["warns"] else (0, 200, 0))
        cv2.putText(row, r["name"][:20], (x0 + 130, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        cv2.putText(row, "ERROR" if r["errors"] else ("WARN" if r["warns"] else "OK"), (x0 + 130, 50),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)
        rows.append(row)
    if rows:
        cv2.imwrite(path, np.vstack(rows))


def main():
    args = sys.argv[1:]
    apply = "--apply" in args
    force = "--force" in args
    forced, inbox, out_dir = None, INBOX, od.TEMPLATE_DIR
    for flag in ("--src", "--inbox", "--out"):
        if flag in args:
            v = args[args.index(flag) + 1]
            if flag == "--src":
                forced = float(v)
            elif flag == "--inbox":
                inbox = v
            else:
                out_dir = v
    if not os.path.isdir(inbox):
        os.makedirs(inbox, exist_ok=True)
        print(f"สร้างโฟลเดอร์ {inbox} แล้ว: ใส่รูปครอปไอเท็ม (ชื่อไฟล์ = ชื่อไอเท็ม) แล้วรันใหม่")
        return
    files = [os.path.join(inbox, f) for f in sorted(os.listdir(inbox))
             if f.lower().endswith(EXTS) and not f.startswith("_")]
    if not files:
        print(f"ไม่มีรูปใน {inbox}: ใส่รูปครอปไอเท็ม (ชื่อไฟล์ = ชื่อไอเท็ม เช่น blue.png) แล้วรันใหม่")
        return

    existing = existing_templates(out_dir)
    results = [check(p, forced, existing, out_dir, force) for p in files]
    names_seen = {}
    for r in results:                                         # ชื่อซ้ำกันเองใน inbox (ต่างนามสกุล)
        if r["name"] in names_seen and not r["errors"]:
            r["errors"].append(f"ชื่อซ้ำกับ {names_seen[r['name']]} ใน inbox")
        names_seen.setdefault(r["name"], r["file"])

    for i, a in enumerate(results):                           # ไอเท็มคนละชื่อแต่เหมือนกันเองใน inbox
        for b in results[:i]:
            if a["std"] is not None and b["std"] is not None and not a["junk"] and not b["junk"]                     and root(a["name"]) != root(b["name"]) and ct.similarity(a["std"], b["std"]) >= ct.EXIST_MATCH:
                a["warns"].append(f"เหมือน {b['file']} ใน inbox (ชื่อต่างกัน แต่เป็นไอเท็มเดียวกัน?)")
                a["dup"] = a["dup"] or (1.0, b["file"])
    todo = []
    for r in results:
        tag = "ผิดพลาด" if r["errors"] else ("เตือน" if r["warns"] else "ผ่าน")
        print(f"[{tag}] {r['file']}  ->  {r['name']}.png")
        for t in r["info"]:
            print(f"        {t}")
        for t in r["warns"]:
            print(f"        เตือน: {t}")
        for t in r["errors"]:
            print(f"        ผิดพลาด: {t}")
        if not r["errors"] and ((not r["dup"] and not r["skip"]) or force):
            todo.append(r)
    sheet(results, os.path.join(inbox, "_report.png"))
    print(f"\nสรุป: ทั้งหมด {len(results)}  พร้อมบันทึก {len(todo)}  "
          f"ข้าม {len(results) - len(todo)} (ผิดพลาด / ซ้ำกับของเดิม / ช่องว่าง-สัดส่วนผิด)   ภาพสรุป: {os.path.join(inbox, '_report.png')}")
    if not apply:
        print("ยังไม่บันทึกอะไร  ถ้าตรวจแล้วโอเค รัน: python main.py prepare --apply")
        return

    os.makedirs(out_dir, exist_ok=True)
    done = os.path.join(inbox, "done")
    os.makedirs(done, exist_ok=True)
    for r in todo:
        cv2.imwrite(os.path.join(out_dir, r["name"] + ".png"), r["std"])
        shutil.move(os.path.join(inbox, r["file"]), os.path.join(done, r["file"]))
        print(f"บันทึก {os.path.join(out_dir, r['name'] + '.png')}  ({STD_W}x{STD_H})")
    print("เสร็จ  ทดสอบ detect ได้ด้วย: python main.py test <รูปที่มีไอเท็มนั้น> <ชื่อไอเท็ม>")


if __name__ == "__main__":
    main()
