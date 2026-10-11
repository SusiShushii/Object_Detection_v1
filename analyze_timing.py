"""
analyze_timing.py - สรุปจาก timing.log: เวลาที่ช่องเปลี่ยนจากทึบเป็นสีปกติ "ครั้งแรกของแต่ละรอบ" (ไม่นับช่องหลังจากนั้น)

    python analyze_timing.py [timing.log]

นับเฉพาะรอบที่เห็นช่องทึบก่อน แล้วเปลี่ยนเป็นปกติ (รอบที่ปกติตั้งแต่เฟรมแรก / รอบทดสอบปลอมที่เร็วกว่า 1.5 วินาที ไม่นับ)
ข้อมูลมาจาก timing.py ซึ่งไม่คลิก (ไม่ให้การคลิกไปรบกวนเวลา)  ควรเก็บอย่างน้อย 10 รอบ
"""
import os
import re
import statistics
import sys

path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "timing.log")
for _s in (sys.stdout,):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

rounds, cur = [], None
for line in open(path, encoding="utf-8", errors="replace"):
    if "=== รอบ" in line:
        cur = {"t": line[:8], "dim": None, "ready": None, "frame": None}
        rounds.append(cur)
    elif cur is not None:
        m = re.search(r"(\d+(?:\.\d+)?)ms f(\d+)\s+แถว\d+ คอลัมน์\d+ ขึ้น\s+\(V=\d+ ทึบ", line)
        if m and cur["dim"] is None:
            cur["dim"] = float(m.group(1))
        m = re.search(r"(\d+(?:\.\d+)?)ms f(\d+)\s+แถว\d+ คอลัมน์\d+ เปลี่ยนเป็นสีปกติ", line)
        if m and cur["ready"] is None:                       # ครั้งแรกของรอบเท่านั้น
            cur["ready"], cur["frame"] = float(m.group(1)), int(m.group(2))

use = [r for r in rounds if r["dim"] is not None and r["ready"] is not None and r["ready"] >= 1500]
print(f"รอบทั้งหมดใน log {len(rounds)}  ใช้ได้ {len(use)} (เห็นทึบแล้วเปลี่ยนเป็นปกติ)")
if not use:
    print("ยังไม่มีข้อมูล: รัน python timing.py ก่อนด่านจบ ให้ผลรางวัลขึ้นแบบทึบเอง แล้วปล่อยให้เปลี่ยนสี")
    sys.exit()
print(f"\n{'เวลา':8} {'ทึบขึ้น':>9} {'เปลี่ยนสี':>10} {'ทึบนาน':>8}  เฟรม")
for r in use:
    print(f"{r['t']:8} {r['dim']:9.1f} {r['ready']:10.1f} {r['ready'] - r['dim']:8.1f}  {r['frame']}")
ready = sorted(r["ready"] for r in use)
dur = [r["ready"] - r["dim"] for r in use]


def pct(a, p):
    return a[min(len(a) - 1, int(round(p * (len(a) - 1))))]


print(f"\nเปลี่ยนเป็นปกติ (นับจากเห็น Mission Reward): n={len(ready)}  ต่ำสุด {ready[0]:.0f}  เฉลี่ย "
      f"{statistics.mean(ready):.0f}  สูงสุด {ready[-1]:.0f} ms" + (f"  SD {statistics.stdev(ready):.1f} ms" if len(ready) > 1 else ""))
print(f"ทึบนาน (นับจากไอเท็มขึ้น):                 ต่ำสุด {min(dur):.0f}  เฉลี่ย {statistics.mean(dur):.0f}  สูงสุด {max(dur):.0f} ms"
      + (f"  SD {statistics.stdev(dur):.1f} ms" if len(dur) > 1 else ""))
spread = ready[-1] - ready[0]
print(f"ช่วงกว้าง (สูงสุด-ต่ำสุด) = {spread:.0f} ms\n")
print("คำแนะนำ:")
print("  - กดตามสีเปลี่ยน (main.py) ไม่ต้องตั้งเวลา: ช้ากว่าเกม ~1 เฟรม + ส่งคลิก ≈ 5-10 ms")
print(f"  - ถ้ากดตามเวลา ต้องตั้งไม่ต่ำกว่า {ready[-1]:.0f} ms (ค่าสูงสุดที่เห็น) + เผื่อ ~20 ms = {ready[-1] + 20:.0f} ms ถึงจะเข้าเกือบทุกรอบ")
print(f"    ช้ากว่ากดตามสีเฉลี่ย ~{ready[-1] + 20 - statistics.mean(ready):.0f} ms")
print("  - กดล่วงหน้าก่อนสีเปลี่ยนไม่ได้ผล: เกมไม่รับคลิกตอนยังทึบ (ผลทดสอบ click_at)")
