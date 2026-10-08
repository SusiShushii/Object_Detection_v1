"""
object_detection.py - หาไอเท็มในภาพด้วย Template Matching (OpenCV)

ภาพ template เก็บไว้ที่ templates/<ชื่อ>.png แคปจากจอ 2560x1440
ถ้าใช้จอความละเอียดอื่น โค้ดจะย่อ/ขยาย template ให้อัตโนมัติ (ตาม scale)
"""

import logging
import os

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# ตั้งค่า
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
LOG_FILE = os.path.join(BASE_DIR, "detection.log")

# ความสูงพื้นที่เกมตอนแคป template (2560x1440)
TEMPLATE_BASE_H = 1440

# ส่วนที่ไม่เอามาเทียบ: ตัวเลขจำนวนไอเท็มมุมขวาล่าง (เปลี่ยนได้ 3, 6, 8, ...)
# (x เริ่ม, y เริ่ม) แบบสัดส่วน 0-1 ของขนาด template
BADGE_START = (0.65, 0.68)

# ความสดของสี (saturation 0-255) ขั้นต่ำที่ถือว่าไอเท็ม "ขึ้นสีปกติ" แล้ว
# วัดจากแครอท: สีเทา ~92  สีปกติ ~149
READY_SATURATION = 120

_cache = {}


# ---------------------------------------------------------------------------
# Log: แสดงบนจอ + บันทึกต่อท้ายไฟล์ detection.log
# ---------------------------------------------------------------------------

log = logging.getLogger("detection")
if not log.handlers:
    log.setLevel(logging.INFO)
    _fmt = logging.Formatter("%(asctime)s  %(message)s", "%Y-%m-%d %H:%M:%S")
    for _handler in (logging.StreamHandler(), logging.FileHandler(LOG_FILE, encoding="utf-8")):
        _handler.setFormatter(_fmt)
        log.addHandler(_handler)


# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------

# โฟลเดอร์รูปเพิ่มเติม (เช่น โฟลเดอร์โปรไฟล์ profiles/Water_Dungeon/) ค้นก่อน templates/
# รูปชื่อเดียวกัน: ในโฟลเดอร์โปรไฟล์ใช้ก่อน
_extra_dirs = []


def use_template_dirs(dirs):
    """ตั้งโฟลเดอร์รูปเพิ่มเติม (ลำดับแรกสุดมาก่อน) แล้วล้าง cache"""
    _extra_dirs[:] = [d for d in dirs if d]
    _cache.clear()
    _gray_cache.clear()


def template_dirs():
    return _extra_dirs + [TEMPLATE_DIR]


def find_template_path(name):
    """path ของรูป name.png ค้นจากโฟลเดอร์โปรไฟล์ก่อน แล้วค่อย templates/"""
    for d in template_dirs():
        path = os.path.join(d, f"{name}.png")
        if os.path.isfile(path):
            return path
    return os.path.join(TEMPLATE_DIR, f"{name}.png")


def list_templates():
    """ชื่อ template ทั้งหมด (ไม่รวม .png) จากโฟลเดอร์โปรไฟล์ + templates/ เช่น ["carrot", ...]"""
    names = set()
    for d in template_dirs():
        if os.path.isdir(d):
            names.update(f[:-4] for f in os.listdir(d) if f.lower().endswith(".png"))
    return sorted(names)


def item_name(template):
    """ชื่อไอเท็มของ template: ตัดส่วนหลัง "-" ออก
    ไอเท็มเดียวมีได้หลายภาพ เช่น skybug_spike.png (สีเทา) + skybug_spike-color.png (สีปกติ) -> "skybug_spike" """
    return template.split("-")[0]


# ตัวคูณขนาดเฉพาะรูป (ปรับให้เองโดย calibrate) เมื่อรูปใน templates/ ครอปมาจากจอคนละความละเอียด
# เช่น ครอปจากจอ 1080 แต่โปรแกรมคิดว่าเป็นจอ 2K -> ต้องขยาย x1.33
_adjust = {}


def set_adjust(name, factor):
    """ตั้งตัวคูณขนาดของรูป name (ล้าง cache ของรูปนั้น) factor=1.0 = ใช้ขนาดมาตรฐาน"""
    if abs(factor - 1.0) < 0.03:
        factor = 1.0
    if _adjust.get(name, 1.0) == factor:
        return
    _adjust[name] = factor
    _cache.clear()
    _gray_cache.clear()


def get_adjust(name):
    return _adjust.get(name, 1.0)


def load_template(name, scale=1.0):
    """โหลด template + mask ที่ปิดตัวเลขมุมขวาล่าง แล้วย่อ/ขยายตาม scale (และตัวคูณเฉพาะรูป)"""
    scale = scale * _adjust.get(name, 1.0)
    key = (name, round(scale, 3))
    if key in _cache:
        return _cache[key]

    path = find_template_path(name)
    tpl = cv2.imread(path, cv2.IMREAD_COLOR)
    if tpl is None:
        raise FileNotFoundError(f"ไม่พบ template: {path}")

    if scale != 1.0:
        tpl = cv2.resize(tpl, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    h, w = tpl.shape[:2]
    mask = np.full((h, w), 255, np.uint8)
    mask[int(h * BADGE_START[1]):, int(w * BADGE_START[0]):] = 0

    _cache[key] = (tpl, mask)
    return tpl, mask


_gray_cache = {}


def _gray_top(name, scale=1.0):
    """template แบบเร็ว: ขาวดำ + ตัดเฉพาะส่วนบน (เหนือตัวเลขมุมขวาล่าง) -> ไม่ต้องใช้ mask
    เร็วกว่าแบบสี + mask ประมาณ 10 เท่า (~2ms ต่อ template)"""
    key = (name, round(scale * _adjust.get(name, 1.0), 3))
    if key not in _gray_cache:
        tpl, _ = load_template(name, scale)
        top = tpl[:int(tpl.shape[0] * BADGE_START[1])]
        _gray_cache[key] = cv2.cvtColor(top, cv2.COLOR_BGR2GRAY)
    return _gray_cache[key]


def calibrate(img, name, scale=1.0, lo=0.5, hi=1.8, accept=0.85):
    """หาขนาดของรูป name ที่ตรงกับภาพ img ที่สุด (ลองย่อ/ขยายหลายขนาด)
    คืน (คะแนนสูงสุด, ตัวคูณขนาดเทียบกับขนาดมาตรฐาน)  ถ้าคะแนน < accept ถือว่าไม่มีรูปนี้ในภาพ"""
    path = find_template_path(name)
    raw = cv2.imread(path, cv2.IMREAD_COLOR)
    if raw is None:
        return -1.0, 1.0
    gray = to_gray(img)

    def score_at(f):
        s = scale * f
        t = cv2.resize(raw, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if abs(s - 1) > 1e-3 else raw
        t = cv2.cvtColor(t[:int(t.shape[0] * BADGE_START[1])], cv2.COLOR_BGR2GRAY)
        if gray.shape[0] < t.shape[0] or gray.shape[1] < t.shape[1] or min(t.shape) < 12:
            return -1.0
        v = float(cv2.matchTemplate(gray, t, cv2.TM_CCOEFF_NORMED).max())
        return -1.0 if np.isnan(v) else v

    best_s, best_f = -1.0, 1.0
    f = lo
    while f <= hi:                                      # หยาบ: ทีละ 5%
        sc = score_at(f)
        if sc > best_s:
            best_s, best_f = sc, f
        f += 0.05
    for df in (-0.04, -0.03, -0.02, -0.01, 0.01, 0.02, 0.03, 0.04):   # ละเอียด: ทีละ 1% รอบค่าที่ดีที่สุด
        sc = score_at(best_f + df)
        if sc > best_s:
            best_s, best_f = sc, best_f + df
    return best_s, round(best_f, 3)


def to_gray(img):
    return img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def match_map(img, name, scale=1.0):
    """แผนที่คะแนน template ในภาพ img (BGR หรือขาวดำ)
    ตัดแถวล่างที่กรอบไอเท็มเต็มขนาดจะล้นภาพออก -> ตำแหน่งที่ได้ใช้กับกรอบเต็มได้เสมอ
    คืน None ถ้าภาพเล็กกว่า template"""
    th, tw = load_template(name, scale)[0].shape[:2]
    gray = to_gray(img)
    if gray.shape[0] < th or gray.shape[1] < tw:
        return None
    result = cv2.matchTemplate(gray, _gray_top(name, scale), cv2.TM_CCOEFF_NORMED)
    result = result[:gray.shape[0] - th + 1]
    return np.nan_to_num(result, nan=-1, posinf=-1, neginf=-1)


def best_score(img, name, scale=1.0):
    """คะแนนสูงสุดของ template ในภาพ (-1 ถ้าภาพเล็กกว่า template)"""
    result = match_map(img, name, scale)
    return -1.0 if result is None else float(result.max())


# ---------------------------------------------------------------------------
# Detect
# ---------------------------------------------------------------------------

def saturation(img, item, mask):
    """ค่าความสดของสีเฉลี่ยในกรอบไอเท็ม (ไม่รวมตัวเลขมุมขวาล่าง)
    ต่ำ = สีเทา (ยังไม่พร้อม)  สูง = สีปกติ"""
    x, y, w, h, _ = item
    hsv = cv2.cvtColor(img[y:y + h, x:x + w], cv2.COLOR_BGR2HSV)
    return float(hsv[..., 1][mask > 0].mean())


def is_ready(img, item, mask, min_sat=READY_SATURATION):
    """True ถ้าไอเท็มขึ้นสีปกติแล้ว"""
    return saturation(img, item, mask) >= min_sat


def detect(img, name, threshold=0.75, scale=1.0, quiet=False):
    """หา template ชื่อ name ในภาพ img (BGR)

    scale     : ขนาดพื้นที่เกม / 1440 (ใช้ get_scale(screen) ช่วยคำนวณ)
    threshold : คะแนนขั้นต่ำ 0-1 ยิ่งสูงยิ่งเข้มงวด
    quiet     : True = ไม่เขียน log (ใช้ตอนเรียกถี่ๆ ใน loop)

    คืน list ของ (x, y, w, h, score) พิกัดเทียบกับมุมซ้ายบนของ img
    เรียงจากคะแนนสูงไปต่ำ
    """
    tpl, mask = load_template(name, scale)
    th, tw = tpl.shape[:2]

    if quiet:
        _log = lambda msg: None
    else:
        _log = log.info

    _log("=" * 60)
    _log(f"[detect] {name}")
    _log(f"  image     : {img.shape[1]}x{img.shape[0]}")
    _log(f"  template  : {tw}x{th}  (scale={scale:.3f})")
    _log(f"  threshold : {threshold}")

    result = match_map(img, name, scale)
    if result is None:
        _log("  ภาพเล็กกว่า template -> ข้าม")
        return []

    _, best, _, best_loc = cv2.minMaxLoc(result)
    _log(f"  best      : {best:.3f} at x={best_loc[0]} y={best_loc[1]}")

    ys, xs = np.where(result >= threshold)
    candidates = sorted(zip(result[ys, xs], xs, ys), reverse=True)

    # ตัดจุดซ้ำที่อยู่ใกล้กัน (ไอเท็มเดียวกันมักเจอหลายพิกเซลติดกัน)
    found = []
    for score, x, y in candidates:
        if all(abs(x - fx) > tw // 2 or abs(y - fy) > th // 2 for fx, fy, *_ in found):
            found.append((int(x), int(y), tw, th, float(score)))

    _log(f"  พิกเซลที่ผ่าน threshold : {len(candidates)}  -> รวมจุดซ้ำแล้วเหลือ {len(found)}")
    _log(f"  เจอ {name} {len(found)} ตำแหน่ง")
    for i, item in enumerate(found, 1):
        x, y, w, h, score = item
        sat = saturation(img, item, mask)
        state = "ready" if sat >= READY_SATURATION else "grey"
        _log(f"    #{i}  x={x:4d} y={y:4d} w={w} h={h} score={score:.3f} sat={sat:5.1f} ({state})")
    return found


def get_scale(screen):
    """คำนวณ scale ของ template จากขนาดพื้นที่เกมจริง"""
    _, _, _, game_h = screen.get_game_rect()
    return game_h / TEMPLATE_BASE_H


def draw(img, found, color=(0, 0, 255)):
    """วาดกรอบ + คะแนนลงบนภาพ (ใช้ debug)"""
    out = img.copy()
    for x, y, w, h, score in found:
        cv2.rectangle(out, (x, y), (x + w, y + h), color, 2)
        cv2.putText(out, f"{score:.2f}", (x, y - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
    return out


# ---------------------------------------------------------------------------
# ทดสอบ
#   python object_detection.py              -> จับภาพจากจอจริง
#   python object_detection.py shot.png     -> ใช้ภาพแคปทั้งจอ (16:9)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    from screen import REGIONS, Screen

    if len(sys.argv) > 1:
        # ภาพแคปจากไฟล์: ปรับเป็น 2560x1440 แล้วตัดเฉพาะ region
        shot = cv2.imread(sys.argv[1])
        shot = cv2.resize(shot, (2560, 1440), interpolation=cv2.INTER_CUBIC)
        nx1, ny1, nx2, ny2 = REGIONS["mission_reward"]
        img = shot[int(ny1 * 1440):int(ny2 * 1440), int(nx1 * 2560):int(nx2 * 2560)]
        scale = 1.0
    else:
        screen = Screen()
        img = screen.grab_region("mission_reward")
        scale = get_scale(screen)

    found = detect(img, "carrot", scale=scale)

    cv2.imwrite("debug_detect.png", draw(img, found))
    log.info("  saved debug_detect.png")
