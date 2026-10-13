"""
paths.py - ที่อยู่โฟลเดอร์/ไฟล์ทั้งหมดของโปรเจกต์ (แก้ที่เดียว)

อ้างอิงจากตำแหน่งของไฟล์นี้ (ไม่ใช่โฟลเดอร์ที่สั่งรัน) -> ย้ายโปรเจกต์ไปไว้ที่ไหน/รันจากโฟลเดอร์ไหนก็ใช้ได้
    ROOT/
      main.py  config.py          จุดเริ่มต้น / ตั้งค่า
      src/detector/               โค้ดหลักของบอท
      tools/                      เครื่องมือ (เรียกผ่าน main.py)
      assets/  templates/ markers/ profiles/     ข้อมูลที่โปรแกรมใช้
      data/    inbox/             ข้อมูลที่ผู้ใช้ใส่
      output/  logs/ debug/ loot/ crops/ ...     สิ่งที่โปรแกรมสร้าง (ลบได้)
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _p(*parts):
    return os.path.join(ROOT, *parts)


ASSETS = _p("assets")
TEMPLATES = _p("assets", "templates")
MARKERS = _p("assets", "markers")
PROFILES = _p("assets", "profiles")

INBOX = _p("data", "inbox")

OUTPUT = _p("output")
LOGS = _p("output", "logs")
DEBUG = _p("output", "debug")
LOOT = _p("output", "loot")
CROPS = _p("output", "crops")
TEST_OUT = _p("output", "test_out")
TIMING_OUT = _p("output", "timing_out")

DETECTION_LOG = os.path.join(LOGS, "detection.log")
TIMING_LOG = os.path.join(LOGS, "timing.log")
FPS_PROBE_TXT = os.path.join(LOGS, "fps_probe.txt")
