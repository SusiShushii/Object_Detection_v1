"""_setup.py - เตรียมสภาพแวดล้อมให้สคริปต์ทดสอบ: path, UTF-8, เอาค่าจาก test_config.py ไปทับ config.py (ต้องเรียกก่อน import detector)
และแยกผลลัพธ์ทดสอบไว้ที่ output/test/<ชื่อทดสอบ>/<เวลาที่เริ่มรัน>/ (test.log + วิดีโอ/trace ของแต่ละรอบ) ไม่ปนกับ production"""
import logging
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def setup(name=None):
    for p in (os.path.join(ROOT, "src"), ROOT, HERE):
        if p not in sys.path:
            sys.path.insert(0, p)
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    import config
    import test_config
    for k, v in vars(test_config).items():
        if k.isupper():
            setattr(config, k, v)
    _separate_outputs(name or os.environ.get("TEST_NAME") or "legacy")
    return config


def _separate_outputs(name):
    """log + บันทึกวิดีโอของรอบทดสอบ -> output/test/<name>/<เวลา>/  (เลิกเขียนลง output/logs/detection.log ของ production)"""
    from detector import paths
    from detector.detection import log
    from detector.recorder import RoundRecorder
    run_dir = os.path.join(paths.OUTPUT, "test", name, time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(run_dir, exist_ok=True)
    for h in list(log.handlers):
        if isinstance(h, logging.FileHandler):
            log.removeHandler(h)
    fh = logging.FileHandler(os.path.join(run_dir, "test.log"), encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%Y-%m-%d %H:%M:%S"))
    log.addHandler(fh)
    RoundRecorder.base_dir = run_dir
    log.info(f"[test:{name}] ผลลัพธ์ทดสอบอยู่ที่ {os.path.relpath(run_dir, paths.ROOT)}  (test.log + วิดีโอ/trace ต่อรอบ)")
