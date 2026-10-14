"""
check_region.py - เช็กว่ากรอบจับภาพของบอทตรงกับหน้า Mission Reward ไหม แล้ววาดกรอบให้ดู (ไม่คลิก ไม่ขยับเมาส์)

    python main.py region                 จับจอตอนนี้ (ต้องเปิดหน้า Mission Result ไว้) -> วาดกรอบ -> output/debug/region_check.png
    python main.py region shot.png        ใช้ภาพแคปหน้าจอที่เซฟไว้แทน (ภาพทั้งจอ 16:9 ขนาดไหนก็ได้)

กรอบที่วาด:
    น้ำเงิน  = กรอบเริ่มต้นที่บอทคาดว่าหน้ารางวัลอยู่
    เหลือง   = พื้นที่ที่บอทค้นหาหัวข้อเพิ่มเมื่อหน้าต่างเลื่อน (กรอบเริ่มต้น ± LOCATE_SEARCH)
    เขียวหนา = กรอบจับภาพจริงหลังหาตำแหน่งเจอ (บอทใช้กรอบนี้)   เขียวบาง = 21 ช่องไอเท็ม
    แดง      = ตำแหน่งหัวข้อ "Mission Reward" ที่เจอ
    ฟ้า      = กรอบเผื่อรอบกรอบจับภาพ 40 px (ไว้ดูว่ากรอบพอดีไหม)
"""
import os
import sys
import time

import cv2
import numpy as np

from detector import clicker as ck
from detector import paths
from detector.collector import slot_rects
from detector.detection import get_scale
from detector.screen import Screen

MARGIN = 40


class ImageScreen(Screen):
    """ใช้ภาพแคปทั้งจอแทนจอจริง (ปรับเป็นสัดส่วน 16:9 ตามความกว้างภาพ)"""

    def __init__(self, img):
        self.window_title, self.monitor, self.region_offset = None, 1, (0, 0)
        self.img = img

    def get_screen_rect(self):
        h, w = self.img.shape[:2]
        return 0, 0, w, h

    def grab(self, box=None):
        if box is None:
            return self.img.copy()
        x1, y1, x2, y2 = box
        return self.img[max(0, y1):y2, max(0, x1):x2].copy()


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args:
        img = cv2.imread(args[0])
        if img is None:
            print(f"เปิดรูปไม่ได้: {args[0]}")
            return
        screen = ImageScreen(img)
        source = f"ภาพ {args[0]} ({img.shape[1]}x{img.shape[0]})"
    else:
        screen = Screen()
        img = screen.grab()
        source = "จอจริงตอนนี้"
    scale = get_scale(screen)
    left, top, w, h = screen.get_screen_rect()
    print(f"แหล่งภาพ: {source}  จอ {w}x{h}  scale={scale:.3f}")

    box0 = screen.get_region("mission_reward")
    reg0 = img[max(0, box0[1] - top):box0[3] - top, max(0, box0[0] - left):box0[2] - left]
    s0 = ck._header_score(reg0, scale)
    print(f"กรอบเริ่มต้น (น้ำเงิน) = {box0}  คะแนนหัวข้อในกรอบนี้ {s0:.3f} (ต้อง >= {ck.HEADER_MATCH}) "
          f"{'-> เห็นหัวข้อ' if s0 >= ck.HEADER_MATCH else '-> ไม่เห็นหัวข้อในกรอบนี้'}")

    moved = None
    if s0 < ck.HEADER_MATCH:
        moved = ck.locate_header(screen, scale)
        print(f"ค้นหาหัวข้อในพื้นที่กว้างขึ้น: " + (f"เจอ เลื่อน dx={moved[0]} dy={moved[1]} px -> กรอบใหม่" if moved else "ไม่เจอ"))
    box1 = screen.get_region("mission_reward")
    reg1 = img[max(0, box1[1] - top):box1[3] - top, max(0, box1[0] - left):box1[2] - left]
    s1 = ck._header_score(reg1, scale)
    print(f"กรอบที่บอทใช้ (เขียวหนา) = {box1}  คะแนนหัวข้อ {s1:.3f}  ออฟเซ็ต {screen.region_offset}")

    # วาด
    out = img.copy()
    sx, sy = int(ck.LOCATE_SEARCH[0] * scale), int(ck.LOCATE_SEARCH[1] * scale)
    rect = lambda b, col, t: cv2.rectangle(out, (b[0] - left, b[1] - top), (b[2] - left, b[3] - top), col, t)
    rect((box0[0] - sx, box0[1] - sy, box0[2] + sx, box0[3] + sy), (0, 220, 255), 2)       # เหลือง: พื้นที่ค้นหา
    rect((box1[0] - MARGIN, box1[1] - MARGIN, box1[2] + MARGIN, box1[3] + MARGIN), (255, 200, 0), 1)   # ฟ้า: กรอบเผื่อ
    rect(box0, (255, 0, 0), 2)                                                              # น้ำเงิน
    rect(box1, (0, 220, 0), 3)                                                              # เขียวหนา
    for (x, y, sw, sh, _) in slot_rects(scale):                                               # ช่องไอเท็ม
        cv2.rectangle(out, (box1[0] - left + x, box1[1] - top + y), (box1[0] - left + x + sw, box1[1] - top + y + sh), (0, 200, 0), 1)
    marker = ck._header_cache.get(round(scale, 3))
    if marker is not None:
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        pad = 200
        y0, y1 = max(0, box1[1] - top - pad), min(h, box1[3] - top + pad)
        x0, x1 = max(0, box1[0] - left - pad), min(w, box1[2] - left + pad)
        m = cv2.matchTemplate(g[y0:y1, x0:x1], marker, cv2.TM_CCOEFF_NORMED)
        fy, fx = np.unravel_index(int(np.nanargmax(m)), m.shape)
        if float(m[fy, fx]) >= 0.6:
            cv2.rectangle(out, (x0 + fx, y0 + fy), (x0 + fx + marker.shape[1], y0 + fy + marker.shape[0]), (0, 0, 255), 2)
            print(f"หัวข้อ Mission Reward อยู่ที่ ({x0 + fx}, {y0 + fy}) บนจอ (คะแนน {float(m[fy, fx]):.2f}) "
                  f"= ในกรอบที่ใช้อยู่ที่ ({x0 + fx - (box1[0] - left)}, {y0 + fy - (box1[1] - top)}) (ปกติ ~({int(ck.HEADER_POS[0] * scale)}, {int(ck.HEADER_POS[1] * scale)}))")
    os.makedirs(paths.DEBUG, exist_ok=True)
    full = os.path.join(paths.DEBUG, "region_check.png")
    cv2.imwrite(full, out)
    # ภาพย่อ ครอปเฉพาะบริเวณหน้าต่างให้ดูง่าย
    cx0, cy0 = max(0, box0[0] - left - 300), max(0, box0[1] - top - 400)
    cx1, cy1 = min(w, box0[2] - left + 300), min(h, box0[3] - top + 250)
    cv2.imwrite(os.path.join(paths.DEBUG, "region_check_crop.png"), out[cy0:cy1, cx0:cx1])
    print(f"บันทึก {os.path.relpath(full, paths.ROOT)} (ทั้งจอ) และ region_check_crop.png (เฉพาะบริเวณหน้าต่าง)")
    print("สรุป: " + ("ตรงตำแหน่งเดิม ใช้ได้" if s0 >= ck.HEADER_MATCH else
                      ("หน้าต่างเลื่อน แต่บอทหาเจอและปรับกรอบให้เองแล้ว (ใช้ได้)" if s1 >= ck.HEADER_MATCH
                       else "หาหัวข้อไม่เจอ: ตรวจว่าเปิดหน้า Mission Result อยู่หรือไม่ / เกมอยู่ในจอที่ถูกต้องไหม")))


if __name__ == "__main__":
    main()
