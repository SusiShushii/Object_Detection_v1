"""
test_config.py - ตั้งค่าสำหรับ "ชุดทดสอบ" เท่านั้น (แยกจาก config.py ของ production)

ค่าที่ตั้งตรงนี้จะทับค่าชื่อเดียวกันใน config.py ตอนรันสคริปต์ในโฟลเดอร์นี้ (ms_only, react_only, ab, ...)
ค่าที่ไม่ได้ตั้งไว้ที่นี่ใช้ตาม config.py  ลบบรรทัดไหนทิ้ง = กลับไปใช้ค่าใน config.py
แก้แล้วรัน .bat ใหม่ได้เลย  ไม่กระทบ python main.py assist / run / random
"""

# จับภาพ: "wgc" หรือ "mss"
CAPTURE_BACKEND = "wgc"

# เวลากดตามเวลา (ms หลังหัวข้อ Mission Reward ขึ้น)
#   ms_only      : ตัวเลขเดียวหรือรายการ เช่น [3979, 3998]
#   react_only/ab/assist_and_react : โหมดเดิมใช้เวลาเดียว ถ้าเป็นรายการจะใช้ตัวน้อยสุด (หรือใส่เวลาที่คำสั่งใน .bat)
CLICK_AT_MS = [3989]

# กดค้างกี่ ms ก่อนปล่อยปุ่ม
CLICK_HOLD_MS = 1

# บันทึกวิดีโอ + วิเคราะห์ทุกรอบ (output/assist_out)
RECORD = True

# True = วาปเมาส์ทันที  False = ลากแบบคน
MOUSE_JUMP = False

# ---- เฉพาะโหมดเดิม (react_only / ab / assist_and_react) ----
REACT_BACKUP_MS = 20        # กดซ้ำสำรองหลังกดตามสีกี่ ms
REACT_RETRY_MS = 350        # ยังพร้อมค้างหลังกดเท่านี้ = กดใหม่
COLLECT_GAP_MS = 60         # collect: เว้นระหว่างช่อง
COLLECT_MAX_PASSES = 2      # collect: ตรวจซ้ำกี่รอบ
RETRY_AFTER_MS = 300        # กดซ้ำเมื่อไม่เข้า
