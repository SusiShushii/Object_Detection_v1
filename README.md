# Object Detection — เก็บของหน้า Mission Result อัตโนมัติ

## ใช้แค่ 2 อย่าง

1. **`main.py`** — ตัวรันจริง
2. **`config.py`** — ตั้งค่าทั้งหมด (จังหวะกด, กดค้าง, ปุ่มหยุด, ไอเท็มเริ่มต้น)

```bash
python main.py carrot            # เก็บเฉพาะแครอท (ใส่หลายชื่อได้ ชื่อแรกสำคัญสุด)
python main.py water_dungeon     # ใช้โปรไฟล์ profiles/Water_Dungeon/priority.txt
python main.py                   # เก็บทุกไอเท็มใน templates/
python main.py carrot --dry      # ไม่คลิกจริง แค่ดู log
```

หยุด: กด **F12** ค้าง หรือ Ctrl+C   ผลบันทึกที่ `detection.log` (ภาพประกอบใน `debug/`, `loot/`)

## ตั้งค่าใน `config.py`

| ค่า | ความหมาย |
|---|---|
| `CLICK_AT_MS` | กดช่องแรกกี่ ms หลังเห็นหัวข้อ Mission Reward (ตอนนี้ 3988) `None` = กดตามสีเปลี่ยนแทน |
| `CLICK_HOLD_MS` | กดค้างกี่ ms (ตอนนี้ 2) |
| `RETRY_AFTER_MS` / `MAX_CLICKS_PER_ITEM` | กดซ้ำเมื่อไม่เข้า / สูงสุดกี่ครั้งต่อช่อง |
| `STOP_KEY_NAME` | ปุ่มหยุด |
| `TARGETS` | ไอเท็มเริ่มต้นเมื่อไม่ได้ใส่ชื่อตอนรัน |

ค่าเวลาวัดมาจากเครื่อง remote (จอ 320 Hz เกม 240 fps) ถ้าเปลี่ยนเครื่อง/จอ/เกม ให้วัดใหม่ด้วยเครื่องมือด้านล่าง

## ไฟล์อื่นๆ เป็นเครื่องมือ (ไม่ต้องรันตอนใช้งานปกติ)

| ไฟล์ | ใช้ทำอะไร |
|---|---|
| `prepare_templates.py` | รูปไอเท็มที่ครอปเองจากจอ 2K/1080p ปนกัน: ใส่ `inbox/` -> ตรวจ + ปรับเป็นขนาดมาตรฐาน 62×65 -> `--apply` ลง `templates/` |
| `crop_templates.py` | ตัดรูปไอเท็มจาก `loot/` ไปไว้ `crops/` (เอาไปเป็น `templates/ชื่อ.png`) |
| `test_detect.py` | ทดสอบ detect ไอเท็มบนรูปภาพ ไม่แตะเมาส์ |
| `timing.py` | จับเวลาหน้า Mission Reward ขึ้น / ช่องเปลี่ยนสี (ไม่คลิก) + วิดีโอ monitor |
| `analyze_timing.py` | สรุปผลจาก `timing.log` (ควรเก็บ 10+ รอบ) แล้วแนะนำค่า `CLICK_AT_MS` |
| `click_at.py` | ทดสอบกดที่เวลา X ms ว่าเข้าไหม เช่น `python click_at.py 3985,3988,3992 --hold 2 --slot 1` |
| `fps_probe.py` | วัดรอบอัปเดตจอ (Hz) และความเร็วจับภาพ |
| `install.bat` / `run.bat` / `venv_cmd.bat` | ติดตั้ง / รัน / เปิด cmd พร้อม venv |

โฟลเดอร์: `templates/` รูปไอเท็ม · `profiles/` ลำดับความสำคัญต่อด่าน · `markers/` รูปอ้างอิง (หัวข้อ, ป้าย GET!, ช่องว่าง)
