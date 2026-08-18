#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
====================================================================
 ระบบเงินเดือนพนักงานรายวัน ครบวงจร (ตามกฎหมายแรงงานไทย)
 ไฟล์เดียวจบ : SQLite + คำนวณค่าแรง/OT/ลา/หัก + สลิป + รายงาน
 ไม่ต้องติดตั้งไลบรารีเพิ่ม (ใช้เฉพาะ Standard Library)

 วิธีใช้  : python payroll_app.py
 แนะนำ    : เข้าเมนู 8 สร้างข้อมูลตัวอย่าง แล้วลองเมนู 5, 6, 7
====================================================================
"""

import sqlite3
import csv
import os
from datetime import datetime, date, timedelta

# ====================== ตั้งค่ากิจการ (แก้ไขได้) ======================
COMPANY_NAME    = "บริษัท ตัวอย่าง จำกัด"
COMPANY_ADDRESS = "123 ถนนสุขุมวิท กรุงเทพมหานคร 10110"
COMPANY_TAX_ID  = "0105555555555"
COMPANY_TEL     = "02-000-0000"

DEFAULT_DAILY_RATE = 372.00   # ค่าแรง/วัน
WORK_HOURS_PER_DAY = 8.0      # ชม.ทำงานปกติ/วัน
SSO_RATE           = 0.05     # ประกันสังคม 5%
SSO_MAX_MONTH      = 750.0    # เพดาน ประกันสังคม/เดือน
SICK_PAID_DAYS     = 30       # ลาป่วยได้ค่าจ้าง/ปี
PERSONAL_PAID_DAYS = 3        # ลากิจได้ค่าจ้าง/ปี
ANNUAL_LEAVE_DAYS  = 6        # ลาพักร้อน/ปี

DB_FILE = "payroll.db"
OUT_DIR = "output"

# วันหยุดนักขัตฤกษ์ 2026 (13 วัน) - ⚠️ ปรับตามประกาศราชการแต่ละปี
HOLIDAYS = {
    "2026-01-01": "วันขึ้นปีใหม่",
    "2026-03-03": "วันมาฆบูชา",
    "2026-04-06": "วันจักรี",
    "2026-04-13": "วันสงกรานต์",
    "2026-04-14": "วันสงกรานต์",
    "2026-04-15": "วันสงกรานต์",
    "2026-05-01": "วันแรงงานแห่งชาติ",
    "2026-05-04": "วันฉัตรมงคล",
    "2026-05-31": "วันวิสาขบูชา",
    "2026-07-29": "วันอาสาฬหบูชา",
    "2026-08-12": "วันแม่แห่งชาติ",
    "2026-10-23": "วันปิยมหาราช",
    "2026-12-10": "วันรัฐธรรมนูญ",
}

THAI_MONTHS = ["มกราคม","กุมภาพันธ์","มีนาคม","เมษายน","พฤษภาคม","มิถุนายน",
               "กรกฎาคม","สิงหาคม","กันยายน","ตุลาคม","พฤศจิกายน","ธันวาคม"]

# ============================ ส่วนช่วย ============================
def get_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def fmt_b(x):
    return f"{x:,.2f}"

def thai_date(d):
    return f"{d.day} {THAI_MONTHS[d.month-1]} {d.year+543}"

def thai_period(s, e):
    return f"{thai_date(s)} - {thai_date(e)}"

def init_db():
    conn = get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS employees (
        emp_id TEXT PRIMARY KEY, name TEXT, position TEXT, department TEXT,
        daily_rate REAL DEFAULT 372, start_date TEXT,
        bank_name TEXT, bank_account TEXT, sso_number TEXT,
        status TEXT DEFAULT 'active');
    CREATE TABLE IF NOT EXISTS attendance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        emp_id TEXT, work_date TEXT, ot_hours REAL DEFAULT 0,
        day_type TEXT DEFAULT 'normal',      -- normal/holiday/special
        leave_type TEXT DEFAULT 'none',      -- none/sick/personal/annual/unpaid
        status TEXT DEFAULT 'present',       -- present/absent/leave
        UNIQUE(emp_id, work_date));
    CREATE TABLE IF NOT EXISTS adjustments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        emp_id TEXT, period_start TEXT, period_end TEXT,
        special REAL DEFAULT 0, advance REAL DEFAULT 0,
        loan REAL DEFAULT 0, other_ded REAL DEFAULT 0,
        UNIQUE(emp_id, period_start, period_end));
    CREATE TABLE IF NOT EXISTS payroll (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        emp_id TEXT, period_start TEXT, period_end TEXT,
        work_days INT DEFAULT 0, absent_days INT DEFAULT 0,
        sick_days INT DEFAULT 0, personal_days INT DEFAULT 0,
        annual_days INT DEFAULT 0, holiday_days INT DEFAULT 0,
        ot_hours REAL DEFAULT 0,
        base_pay REAL DEFAULT 0, ot_pay REAL DEFAULT 0,
        holiday_extra REAL DEFAULT 0, special REAL DEFAULT 0,
        gross REAL DEFAULT 0, sso REAL DEFAULT 0,
        advance REAL DEFAULT 0, loan REAL DEFAULT 0, other_ded REAL DEFAULT 0,
        total_ded REAL DEFAULT 0, net REAL DEFAULT 0,
        UNIQUE(emp_id, period_start, period_end));
    """)
    conn.commit()
    conn.close()
    os.makedirs(OUT_DIR, exist_ok=True)

# ============================ เมนู 1: พนักงาน ============================
def add_employee():
    conn = get_conn()
    emp_id = input("รหัสพนักงาน: ").strip()
    name = input("ชื่อ-นามสกุล: ").strip()
    position = input("ตำแหน่ง: ").strip()
    department = input("แผนก: ").strip()
    rate = float(input(f"ค่าแรง/วัน [{DEFAULT_DAILY_RATE}]: ").strip() or DEFAULT_DAILY_RATE)
    bank = input("ธนาคาร: ").strip()
    acc = input("เลขบัญชี: ").strip()
    conn.execute("""INSERT OR REPLACE INTO employees
        (emp_id,name,position,department,daily_rate,start_date,bank_name,bank_account,status)
        VALUES (?,?,?,?,?,?,?,?, 'active')""",
        (emp_id, name, position, department, rate, date.today().isoformat(), bank, acc))
    conn.commit(); conn.close()
    print(f"✔ บันทึกพนักงาน {emp_id} แล้ว")

def list_employees():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM employees ORDER BY emp_id").fetchall()
    conn.close()
    if not rows:
        print("(ยังไม่มีพนักงาน - เพิ่มที่เมนู 1 หรือเมนู 8)"); return
    print(f"\n{'รหัส':<8}{'ชื่อ':<22}{'ตำแหน่ง':<16}{'แผนก':<14}{'ค่าแรง/วัน':>10}")
    print("-"*72)
    for r in rows:
        print(f"{r['emp_id']:<8}{r['name']:<22}{r['position']:<16}{r['department']:<14}{r['daily_rate']:>10,.2f}")

# ============================ เมนู 2: เวลาทำงาน ============================
def add_attendance():
    emp_id = input("รหัสพนักงาน: ").strip()
    d = input("วันที่ (YYYY-MM-DD): ").strip()
    print("สถานะ: present / absent / leave")
    st = input("สถานะ [present]: ").strip() or "present"
    lt, dt, ot = "none", "normal", 0.0
    if st == "leave":
        lt = input("ประเภทลา sick/personal/annual/unpaid [sick]: ").strip() or "sick"
    if st == "present":
        if d in HOLIDAYS:
            print(f"ℹ วันนั้นคือ {HOLIDAYS[d]}")
        dt = input("ประเภทวัน normal/holiday(วันหยุดweekly)/special(นักขัตฤกษ์) [normal]: ").strip() or "normal"
        ot = float(input("ชั่วโมง OT [0]: ").strip() or 0)
    conn = get_conn()
    conn.execute("""INSERT OR REPLACE INTO attendance
        (emp_id,work_date,ot_hours,day_type,leave_type,status) VALUES (?,?,?,?,?,?)""",
        (emp_id, d, ot, dt, lt, st))
    conn.commit(); conn.close()
    print("✔ บันทึกเวลาทำงานแล้ว")

def import_attendance_csv():
    path = input("พาธไฟล์ CSV: ").strip()
    if not os.path.exists(path):
        print("✗ ไม่พบไฟล์"); return
    conn = get_conn(); n = 0
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            conn.execute("""INSERT OR REPLACE INTO attendance
                (emp_id,work_date,ot_hours,day_type,leave_type,status) VALUES (?,?,?,?,?,?)""",
                (row["emp_id"], row["work_date"],
                 float(row.get("ot_hours") or 0),
                 row.get("day_type") or "normal",
                 row.get("leave_type") or "none",
                 row.get("status") or "present"))
            n += 1
    conn.commit(); conn.close()
    print(f"✔ นำเข้า {n} รายการ")

# ============================ เมนู 4: รายการพิเศษ ============================
def add_adjustment():
    emp_id = input("รหัสพนักงาน: ").strip()
    s, e = choose_period()
    special = float(input("เบี้ยเลี้ยง/ค่าพิเศษ [0]: ").strip() or 0)
    advance = float(input("หักเบิกล่วงหน้า [0]: ").strip() or 0)
    loan = float(input("หักเงินกู้ [0]: ").strip() or 0)
    other = float(input("หักอื่น ๆ [0]: ").strip() or 0)
    conn = get_conn()
    conn.execute("""INSERT INTO adjustments (emp_id,period_start,period_end,special,advance,loan,other_ded)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(emp_id,period_start,period_end) DO UPDATE SET
        special=excluded.special, advance=excluded.advance,
        loan=excluded.loan, other_ded=excluded.other_ded""",
        (emp_id, s.isoformat(), e.isoformat(), special, advance, loan, other))
    conn.commit(); conn.close()
    print("✔ บันทึกรายการพิเศษแล้ว")

# ============================ เลือกงวด ============================
def choose_period():
    print("\nเลือกงวดจ่าย: 1) 1-15  2) 16-สิ้นเดือน  3) ทั้งเดือน  4) กำหนดเอง")
    ch = input("เลือก [1]: ").strip() or "1"
    ym = input("เดือน/ปี (เช่น 2026-08) [2026-08]: ").strip() or "2026-08"
    y, m = map(int, ym.split("-"))
    first = date(y, m, 1)
    last = date(y, 12, 31) if m == 12 else date(y, m+1, 1) - timedelta(days=1)
    if ch == "2": return date(y, m, 16), last
    if ch == "3": return first, last
    if ch == "4":
        return (date.fromisoformat(input("วันเริ่ม (YYYY-MM-DD): ").strip()),
                date.fromisoformat(input("วันสิ้นสุด (YYYY-MM-DD): ").strip()))
    return first, date(y, m, 15)

# ============================ เมนู 5: คำนวณเงินเดือน ============================
def calc_employee(conn, emp_id, s, e):
    emp = conn.execute("SELECT * FROM employees WHERE emp_id=?", (emp_id,)).fetchone()
    if not emp: return None
    rate = emp["daily_rate"]
    hourly = rate / WORK_HOURS_PER_DAY
    rows = conn.execute("""SELECT * FROM attendance
        WHERE emp_id=? AND work_date BETWEEN ? AND ? ORDER BY work_date""",
        (emp_id, s, e)).fetchall()

    work_days = absent = sick = personal = annual = holiday_days = 0
    base = ot_pay = holiday_extra = ot_total = 0.0

    for r in rows:
        st, dt, lt, ot = r["status"], r["day_type"], r["leave_type"], r["ot_hours"] or 0
        if st == "present":
            work_days += 1; base += rate
            if dt in ("holiday", "special"):
                holiday_days += 1; holiday_extra += rate   # ทำงานวันหยุด เพิ่มอีก 1 เท่า
            if ot > 0:
                ot_total += ot
                mult = 1.5 if dt == "normal" else (2.0 if dt == "holiday" else 3.0)
                ot_pay += ot * hourly * mult
        elif st == "absent":
            absent += 1
        elif st == "leave":
            if lt == "sick":
                sick += 1
                if sick <= SICK_PAID_DAYS: base += rate
            elif lt == "personal":
                personal += 1
                if personal <= PERSONAL_PAID_DAYS: base += rate
            elif lt == "annual":
                annual += 1; base += rate

    adj = conn.execute("""SELECT * FROM adjustments
        WHERE emp_id=? AND period_start=? AND period_end=?""", (emp_id, s, e)).fetchone()
    special   = adj["special"]   if adj else 0.0
    advance   = adj["advance"]   if adj else 0.0
    loan      = adj["loan"]      if adj else 0.0
    other_ded = adj["other_ded"] if adj else 0.0

    gross = base + ot_pay + holiday_extra + special
    days = (date.fromisoformat(e) - date.fromisoformat(s)).days + 1
    sso = round(min(gross * SSO_RATE, SSO_MAX_MONTH * days / 30.0), 2)
    total_ded = round(sso + advance + loan + other_ded, 2)
    net = round(gross - total_ded, 2)

    conn.execute("""INSERT INTO payroll
        (emp_id,period_start,period_end,work_days,absent_days,sick_days,personal_days,
         annual_days,holiday_days,ot_hours,base_pay,ot_pay,holiday_extra,special,
         gross,sso,advance,loan,other_ded,total_ded,net)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(emp_id,period_start,period_end) DO UPDATE SET
        work_days=excluded.work_days, absent_days=excluded.absent_days,
        sick_days=excluded.sick_days, personal_days=excluded.personal_days,
        annual_days=excluded.annual_days, holiday_days=excluded.holiday_days,
        ot_hours=excluded.ot_hours, base_pay=excluded.base_pay, ot_pay=excluded.ot_pay,
        holiday_extra=excluded.holiday_extra, special=excluded.special,
        gross=excluded.gross, sso=excluded.sso, advance=excluded.advance,
        loan=excluded.loan, other_ded=excluded.other_ded,
        total_ded=excluded.total_ded, net=excluded.net""",
        (emp_id, s, e, work_days, absent, sick, personal, annual, holiday_days,
         ot_total, round(base,2), round(ot_pay,2), round(holiday_extra,2), special,
         round(gross,2), sso, advance, loan, other_ded, total_ded, net))
    conn.commit()

    return {"emp_id": emp_id, "name": emp["name"], "work_days": work_days,
            "absent": absent, "gross": round(gross,2), "total_ded": total_ded, "net": net}

def run_payroll():
    s, e = choose_period()
    conn = get_conn()
    emps = conn.execute("SELECT emp_id FROM employees WHERE status='active' ORDER BY emp_id").fetchall()
    print(f"\nคำนวณงวด {thai_period(s, e)}")
    print("-"*78)
    for row in emps:
        r = calc_employee(conn, row["emp_id"], s.isoformat(), e.isoformat())
        if r:
            print(f"{r['emp_id']} {r['name']:<20} ทำงาน {r['work_days']:>2} วัน | "
                  f"รายได้ {r['gross']:>9,
