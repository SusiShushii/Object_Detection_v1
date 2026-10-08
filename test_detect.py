"""
test_detect.py - ทดสอบว่า detect ไอเท็มในรูปได้ไหม (ไม่ใช้เมาส์ ไม่จับหน้าจอ ไม่คลิก)

    python test_detect.py img001.png blue_seaweed
    python test_detect.py img001.png img002.png water_dungeon     (หลายรูป / ชื่อโปรไฟล์ได้)
    python test_detect.py loot\\20261008_202107_2_normal.png        (ไม่ใส่ชื่อ = ทุกไอเท็มใน templates/)

รูปที่ใส่ได้ 2 แบบ (เดาให้เองจากขนาด):
  - รูปกรอบ Mission Reward ที่โปรแกรมบันทึก (loot/*.png, debug/*.png)
  - รูปแคปหน้าจอทั้งจอ (จอ 16:9 ความละเอียดไหนก็ได้)

ผลลัพธ์: ตารางบอกว่าเจอ/ไม่เจอ คะแนน ช่อง สถานะ + ภาพวาดกรอบที่ test_out/<ชื่อรูป>_boxes.png
  กรอบเขียว = ผ่านเกณฑ์ (เหลือง = เจอแต่ไม่ใช่ช่องแรก)  แดง = เกือบเจอแต่ต่ำกว่าเกณฑ์
"""

import logging
import os
import sys

import cv2
import numpy as np

from object_detection import (BADGE_START, calibrate, detect, get_adjust, item_name, list_templates,
                              load_template, saturation, set_adjust)
import click
from profiles import list_profiles, resolve
from screen import GAME_ASPECT, REGIONS

# ไม่เขียน detection.log ตอนทดสอบ
_lg = logging.getLogger("detection")
_lg.handlers = [h for h in _lg.handlers if not isinstance(h, logging.FileHandler)]
_lg.setLevel(logging.WARNING)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, "test_out")
IMG_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".webp")
PAD = 5
REGION_BASE_W = 525        # ความกว้างกรอบที่จอ 2K ก่อนบวก pad


def to_region(img):
    """รูปที่ใส่มา -> (ภาพกรอบ Mission Reward, scale, ชนิดรูป)"""
    h, w = img.shape[:2]
    aspect = w / h
    if 1.7 <= aspect <= 2.3 and w < 1000:               # รูปกรอบที่โปรแกรมบันทึก (ประมาณ 2:1)
        return img, max(0.2, (w - 2 * PAD) / REGION_BASE_W), "รูปกรอบ Mission Reward"
    gw = min(w, h * GAME_ASPECT)                          # รูปแคปทั้งจอ: หาพื้นที่เกม 16:9 กลางภาพ
    gh = gw / GAME_ASPECT
    ox, oy = (w - gw) / 2, (h - gh) / 2
    nx1, ny1, nx2, ny2 = REGIONS["mission_reward"]
    x1, y1 = int(ox + nx1 * gw) - PAD, int(oy + ny1 * gh) - PAD
    x2, y2 = int(ox + nx2 * gw) + PAD, int(oy + ny2 * gh) + PAD
    return img[max(0, y1):y2, max(0, x1):x2].copy(), gh / 1440, f"รูปทั้งจอ {w}x{h}"


def slot_of(x, y, scale):
    c = round(((x - PAD) / scale - click.GRID_ORIGIN[0]) / click.GRID_PITCH[0]) + 1
    r = round(((y - PAD) / scale - click.GRID_ORIGIN[1]) / click.GRID_PITCH[1]) + 1
    return c, r


def test_image(path, names):
    img0 = cv2.imread(path)
    if img0 is None:
        print(f"อ่านรูปไม่ได้: {path}")
        return
    region, scale, kind = to_region(img0)
    base = os.path.splitext(os.path.basename(path))[0]
    print("=" * 78)
    print(f"รูป: {path}   ({kind}  scale={scale:.3f}  กรอบ {region.shape[1]}x{region.shape[0]})")

    hdr = click._header_score(region, scale)
    print(f"หัวข้อ Mission Reward : {'เห็น' if hdr >= click.HEADER_MATCH else 'ไม่เห็น'}  "
          f"(คะแนน {hdr:.2f}, เกณฑ์ {click.HEADER_MATCH})"
          + ("" if hdr >= click.HEADER_MATCH else "   <- โปรแกรมจะไม่ทำงานถ้าไม่เห็นหัวข้อนี้ (รูปครอปผิดที่ / ไม่ใช่หน้า Mission Result?)"))
    loot, lscore = click._is_random_loot(region, scale)
    occ, dim = click._grid_state(region, scale)
    print(f"สถานะกรอบ             : มีไอเท็ม {occ} ช่อง (ทึบ {dim})  โหมด {'random loot (มีกล่อง ?)' if loot else 'ปกติ'}")

    # ช่องที่มีป้าย GET! (เมาส์ hover อยู่) ป้ายจะบังไอคอน จึงหาไอเท็มในช่องนั้นไม่เจอ (ตอนรันจริงเจอก่อน hover)
    (ox, oy), (px, py), (nc, nr), (sw, sh) = click.GRID_ORIGIN, click.GRID_PITCH, click.GRID_SIZE, click.SLOT_WH
    gets = []
    for r in range(nr):
        for c in range(nc):
            x = int(round(PAD + (ox + px * c) * scale))
            y = int(round(PAD + (oy + py * r) * scale))
            if click._get_score(region, ("x", (x, y, int(sw * scale), int(sh * scale), 1)), scale) >= click.GET_MATCH:
                gets.append(f"คอลัมน์ {c + 1} แถว {r + 1}")
    if gets:
        print(f"ช่องที่มีป้าย GET! บังอยู่  : {', '.join(gets)}  (ไอเท็มในช่องนี้หาไม่เจอในรูป เป็นเรื่องปกติ)")

    templates_by_item = {}
    for t in list_templates():
        templates_by_item.setdefault(item_name(t), []).append(t)

    shown, picked_total = [], 0
    for name in names:
        tpls = templates_by_item.get(name, [])
        found = []
        for t in tpls:
            f = detect(region, t, scale=scale, quiet=True)
            if not f:                                           # ไม่เจอ -> ลองปรับขนาดรูป (เหมือนตอนรันจริง)
                sc, fac = calibrate(region, t, scale)
                if sc >= 0.85 and abs(fac - get_adjust(t)) >= 0.03:
                    set_adjust(t, fac)
                    print(f"  [{name}] รูป {t}.png ขนาดไม่ตรงกับบนจอ -> ปรับอัตโนมัติ x{fac:.2f} (คะแนน {sc:.2f})")
                    f = detect(region, t, scale=scale, quiet=True)
            found += [(t, *it) for it in f]
        # รวมผลหลายรูปของไอเท็มเดียวกัน (ช่องเดียวกัน เก็บคะแนนสูงสุด)
        merged = []
        for t, x, y, w, h, s in sorted(found, key=lambda z: -z[5]):
            if all(abs(x - m[1]) > w // 2 or abs(y - m[2]) > h // 2 for m in merged):
                merged.append((t, x, y, w, h, s))
        print(f"\n[{name}]  รูปที่ใช้: {', '.join(tpls) or '(ไม่มีรูป)'}")
        if not merged:
            best = max((click.best_score(region, t, scale * get_adjust(t)) for t in tpls), default=-1)
            print(f"   -> ไม่เจอ  (คะแนนสูงสุด {best:.2f} ต่ำกว่าเกณฑ์ 0.75)")
            hint = ("   ไอเท็มนี้ไม่อยู่ในรูป หรือรูปใน templates/ ไม่ตรงกับในเกม"
                    if best < 0.68 else "   เกือบเจอ: รูป template ครอปไม่พอดี/มีเมาส์/พื้นหลังต่างกัน ลองครอปใหม่จากรูปใน loot/")
            print(hint)
            continue
        print(f"   -> เจอ {len(merged)} ช่อง")
        for i, (t, x, y, w, h, s) in enumerate(merged, 1):
            it = (x, y, w, h, s)
            sat = saturation(region, it, load_template(t, scale)[1])
            bg = click._slot_bg_v(region, it)
            gs = click._get_score(region, (t, it), scale)
            sig = "+".join(n for n, v in (("GET", gs >= click.GET_MATCH), ("bg", bg >= click.BG_READY_V),
                                          ("sat", sat >= click.READY_SATURATION)) if v) or "ทึบ"
            c, r = slot_of(x, y, scale)
            print(f"     #{i}  คอลัมน์ {c} แถว {r}  x={x:3d} y={y:3d}  คะแนน {s:.3f}  "
                  f"พร้อมกด: {sig}   (sat {sat:.0f}, bg {bg:.0f}, GET {gs:.2f})   รูป {t}")
        shown += [t for t, *_ in merged]
        picked_total += len(merged)

    all_tpls = [t for n in names for t in templates_by_item.get(n, [])]
    boxed, summary = click._draw_boxes(region, all_tpls, scale, chosen=None)
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, f"{base}_boxes.png")
    cv2.imwrite(out, boxed)
    print("\n" + "-" * 78)
    print(f"สรุป: เจอรวม {picked_total} ช่อง   ภาพวาดกรอบ: {out}")
    print(f"คะแนนสูงสุดต่อรูป template: {summary}")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    images = [a for a in args if a.lower().endswith(IMG_EXT)]
    items = [a for a in args if not a.lower().endswith(IMG_EXT)]
    if not images:
        print(__doc__)
        return
    info = resolve(items)
    for e in info["errors"]:
        print(f"ผิดพลาด: {e}")
    if info["errors"]:
        return
    if info["missing"]:
        print(f"ไม่พบรูปไอเท็ม {info['missing']} ใน templates/  (ที่มี: {info['available']}  โปรไฟล์: {list_profiles()})")
        if not info["names"] and items:
            return
    names = info["names"] or sorted(set(item_name(t) for t in list_templates()))
    for path in images:
        test_image(path, names)


if __name__ == "__main__":
    main()
