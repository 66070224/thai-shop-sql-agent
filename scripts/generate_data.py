"""Generate the synthetic Thai Shop dataset as 8 CSV files.

Standard library only. Every random draw goes through one ``random.Random(seed)``
so running with the same seed and arguments reproduces every file byte for byte.

Dirty data is injected on purpose (rates are configurable) and every rule is
documented in ``data/schema_docs.md``:

- cancelled / refunded orders must be excluded from sales
- NULL shipped_at / delivered_at for orders not yet shipped or cancelled;
  some products have no category; some reviews have no comment
- ~3% duplicate customers (new customer_id, same email)
- Bangkok spelled four ways
- several payment rows per order (split payments, failed attempt before success)

Trends: order spikes on 11.11 and 12.12, and category popularity that differs by region.
"""

import argparse
import bisect
import csv
import random
from datetime import date, datetime, timedelta
from pathlib import Path

START = datetime(2024, 1, 1)
END = datetime(2025, 12, 31, 23, 59, 59)

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "data" / "csv"

# (category_id, name_th, name_en, base popularity, rating adjustment, price range)
CATEGORIES = [
    (1, "โทรศัพท์มือถือ", "Mobile Phones", 6, 0.0, (3990, 39900)),
    (2, "คอมพิวเตอร์และแล็ปท็อป", "Computers & Laptops", 4, 0.1, (5990, 59900)),
    (3, "อุปกรณ์เสริมมือถือ", "Mobile Accessories", 9, -0.7, (99, 1290)),
    (4, "เครื่องใช้ไฟฟ้า", "Home Appliances", 5, 0.0, (590, 15900)),
    (5, "เสื้อผ้าผู้หญิง", "Women's Fashion", 10, -0.2, (199, 1590)),
    (6, "เสื้อผ้าผู้ชาย", "Men's Fashion", 7, -0.1, (199, 1490)),
    (7, "รองเท้า", "Shoes", 6, 0.0, (390, 3990)),
    (8, "กระเป๋า", "Bags", 5, 0.1, (290, 4990)),
    (9, "ความงามและเครื่องสำอาง", "Beauty & Cosmetics", 10, 0.1, (89, 1890)),
    (10, "สุขภาพ", "Health", 5, 0.2, (150, 2490)),
    (11, "อาหารและเครื่องดื่ม", "Food & Beverages", 7, 0.2, (35, 690)),
    (12, "แม่และเด็ก", "Mother & Baby", 5, 0.3, (120, 3490)),
    (13, "ของเล่น", "Toys", 4, 0.0, (99, 2990)),
    (14, "กีฬาและกิจกรรมกลางแจ้ง", "Sports & Outdoors", 4, 0.1, (190, 6990)),
    (15, "บ้านและสวน", "Home & Garden", 5, 0.1, (79, 3990)),
    (16, "เครื่องครัว", "Kitchenware", 4, 0.2, (59, 2490)),
    (17, "สัตว์เลี้ยง", "Pet Supplies", 3, 0.3, (49, 1990)),
    (18, "หนังสือและเครื่องเขียน", "Books & Stationery", 3, 0.5, (29, 890)),
    (19, "ยานยนต์", "Automotive", 2, -0.1, (99, 5990)),
    (20, "เกมและอุปกรณ์เกม", "Gaming", 3, -0.3, (290, 19900)),
]

PRODUCT_NOUNS = {
    1: ["สมาร์ทโฟน", "มือถือจอพับ", "มือถือเกมมิ่ง", "มือถือปุ่มกด"],
    2: ["โน้ตบุ๊ก", "คอมพิวเตอร์ตั้งโต๊ะ", "แท็บเล็ต", "จอมอนิเตอร์", "คีย์บอร์ดไร้สาย"],
    3: ["เคสโทรศัพท์", "ฟิล์มกระจก", "สายชาร์จ USB-C", "หัวชาร์จเร็ว", "พาวเวอร์แบงก์", "หูฟังไร้สาย"],
    4: ["หม้อทอดไร้น้ำมัน", "พัดลมตั้งพื้น", "เครื่องฟอกอากาศ", "ไมโครเวฟ", "เตารีดไอน้ำ", "เครื่องดูดฝุ่น"],
    5: ["เดรสลายดอก", "เสื้อครอป", "กางเกงยีนส์ขาม้า", "กระโปรงพลีท", "เสื้อเชิ้ตผ้าลินิน"],
    6: ["เสื้อโปโล", "กางเกงชิโน่", "เสื้อยืดคอกลม", "แจ็กเก็ตยีนส์", "กางเกงขาสั้น"],
    7: ["รองเท้าผ้าใบ", "รองเท้าแตะ", "รองเท้าวิ่ง", "รองเท้าหนัง", "รองเท้าส้นสูง"],
    8: ["กระเป๋าเป้", "กระเป๋าสะพายข้าง", "กระเป๋าสตางค์", "กระเป๋าเดินทาง", "กระเป๋าผ้า"],
    9: ["ลิปสติก", "ครีมกันแดด", "เซรั่มบำรุงผิว", "คุชชั่น", "มาสคาร่า", "โฟมล้างหน้า"],
    10: ["วิตามินซี", "คอลลาเจน", "เครื่องวัดความดัน", "หน้ากากอนามัย", "ยาดมสมุนไพร"],
    11: ["กาแฟดริป", "ชาไทยปรุงสำเร็จ", "ข้าวหอมมะลิ", "น้ำพริกเผา", "ขนมทองม้วน", "มาม่าคัพ"],
    12: ["ผ้าอ้อมสำเร็จรูป", "ขวดนม", "รถเข็นเด็ก", "นมผงเด็ก", "ผ้าห่อตัวเด็ก"],
    13: ["ตัวต่อเลโก้", "ตุ๊กตาหมี", "รถบังคับ", "จิ๊กซอว์", "บอร์ดเกม"],
    14: ["เสื่อโยคะ", "ดัมเบล", "เต็นท์แคมป์ปิ้ง", "จักรยานพับ", "ไม้แบดมินตัน", "ชุดว่ายน้ำ"],
    15: ["กระถางต้นไม้", "ผ้าม่าน", "โคมไฟตั้งโต๊ะ", "ชั้นวางของ", "ปุ๋ยอินทรีย์"],
    16: ["กระทะเทฟลอน", "หม้อหุงข้าว", "ชุดมีดทำครัว", "กล่องถนอมอาหาร", "แก้วเก็บความเย็น"],
    17: ["อาหารแมว", "อาหารสุนัข", "ทรายแมว", "ที่นอนสัตว์เลี้ยง", "ปลอกคอ"],
    18: ["สมุดโน้ต", "ปากกาเจล", "นิยาย", "หนังสือสอนทำอาหาร", "สีไม้"],
    19: ["ที่วางมือถือในรถ", "น้ำยาเคลือบสี", "กล้องติดรถยนต์", "ผ้าคลุมรถ", "ยางปัดน้ำฝน"],
    20: ["จอยเกม", "เมาส์เกมมิ่ง", "หูฟังเกมมิ่ง", "เก้าอี้เกมมิ่ง", "แผ่นเกม"],
}
PRODUCT_VARIANTS = ["รุ่นมาตรฐาน", "รุ่นโปร", "รุ่นพรีเมียม", "รุ่นมินิ", "รุ่นพลัส", "สีดำ", "สีขาว",
                    "สีชมพู", "สีน้ำเงิน", "แพ็คคู่", "ไซซ์ใหญ่", "รุ่นประหยัด", "รุ่น 2025"]

# (canonical name, weight, region, delivery days range)
PROVINCES = [
    ("กรุงเทพมหานคร", 34, "bkk", (1, 2)),
    ("นนทบุรี", 6, "bkk", (1, 2)),
    ("ปทุมธานี", 5, "bkk", (1, 2)),
    ("สมุทรปราการ", 5, "bkk", (1, 2)),
    ("นครปฐม", 3, "central", (1, 3)),
    ("เชียงใหม่", 7, "north", (2, 4)),
    ("เชียงราย", 3, "north", (2, 5)),
    ("ขอนแก่น", 5, "northeast", (2, 4)),
    ("นครราชสีมา", 5, "northeast", (2, 4)),
    ("อุดรธานี", 4, "northeast", (2, 5)),
    ("ชลบุรี", 6, "east", (1, 3)),
    ("ระยอง", 4, "east", (1, 3)),
    ("ภูเก็ต", 5, "south", (2, 4)),
    ("สงขลา", 4, "south", (2, 5)),
    ("สุราษฎร์ธานี", 4, "south", (2, 5)),
]
BANGKOK_ALIASES = ["กรุงเทพ", "กทม.", "Bangkok"]

# Category popularity multipliers per region (category_id -> multiplier).
REGION_BOOST = {
    "bkk": {1: 1.8, 2: 2.0, 9: 1.8, 20: 2.2},
    "central": {17: 3.0, 16: 2.0},
    "north": {15: 3.0, 14: 2.0, 18: 2.5},
    "northeast": {11: 3.0, 12: 2.5, 4: 2.0},
    "east": {19: 4.0, 7: 2.0},
    "south": {14: 3.0, 10: 2.0, 5: 1.6},
}

FIRST_NAMES = [
    ("สมชาย", "somchai"), ("สมหญิง", "somying"), ("ณัฐพล", "nattapon"), ("ปิยะนุช", "piyanuch"),
    ("ธนากร", "thanakorn"), ("กมลวรรณ", "kamonwan"), ("อนุชา", "anucha"), ("วิภาวดี", "wipawadee"),
    ("ศุภชัย", "supachai"), ("พรทิพย์", "porntip"), ("กิตติ", "kitti"), ("สุนิสา", "sunisa"),
    ("ชยพล", "chayapon"), ("อรอุมา", "onuma"), ("ภานุวัฒน์", "panuwat"), ("จิราพร", "jiraporn"),
    ("วีรยุทธ", "weerayut"), ("นภัสสร", "napatsorn"), ("ปกรณ์", "pakorn"), ("ธิดารัตน์", "thidarat"),
    ("เอกชัย", "ekkachai"), ("มณีรัตน์", "maneerat"), ("ศักดิ์ดา", "sakda"), ("พิมพ์ชนก", "pimchanok"),
    ("ธีรวัฒน์", "teerawat"), ("รัชนี", "ratchanee"), ("อภิชาติ", "apichart"), ("ชนิดา", "chanida"),
]
LAST_NAMES = [
    ("ใจดี", "jaidee"), ("สุขสวัสดิ์", "suksawat"), ("ทองคำ", "thongkham"), ("ศรีสุข", "srisuk"),
    ("วงศ์ใหญ่", "wongyai"), ("แก้วมณี", "kaewmanee"), ("บุญมา", "boonma"), ("รุ่งเรือง", "rungruang"),
    ("พรหมมา", "prommma"), ("สายทอง", "saithong"), ("เพชรรัตน์", "petcharat"), ("นาคสวัสดิ์", "naksawat"),
    ("มั่นคง", "mankong"), ("ศรีวงศ์", "sriwong"), ("อินทร์แก้ว", "inkaew"), ("ชัยมงคล", "chaimongkol"),
]
EMAIL_DOMAINS = ["gmail.com", "hotmail.com", "yahoo.co.th", "outlook.com"]

CHANNELS = [("app", 45), ("web", 30), ("line", 15), ("facebook", 10)]
PAY_METHODS = [("PromptPay", 35), ("card", 30), ("transfer", 15), ("COD", 20)]

REVIEW_COMMENTS = {
    5: ["สินค้าดีมาก ส่งไว", "คุ้มค่ามาก จะกลับมาซื้ออีก", "ประทับใจมากค่ะ", "ของตรงปก แพ็คมาดี"],
    4: ["สินค้าดี แต่ส่งช้านิดหน่อย", "โดยรวมดี คุ้มราคา", "ใช้งานได้ดีครับ"],
    3: ["พอใช้ได้", "ก็โอเค ตามราคา", "สินค้าไม่ค่อยตรงปกเท่าไหร่"],
    2: ["คุณภาพไม่ค่อยดี", "ส่งช้ามาก", "สีไม่ตรงกับรูป"],
    1: ["แย่มาก ไม่แนะนำ", "สินค้าเสียหาย", "ได้ของไม่ครบ"],
}

# (code, discount_type, value, valid_from, valid_to)
SPECIAL_COUPONS = [
    ("1111SALE", "percent", 15, "2024-11-10", "2024-11-12"),
    ("1111MEGA", "fixed", 300, "2025-11-10", "2025-11-12"),
    ("1111DEAL", "percent", 20, "2025-11-11", "2025-11-11"),
    ("1212SALE", "percent", 12, "2024-12-11", "2024-12-13"),
    ("1212MEGA", "fixed", 250, "2025-12-11", "2025-12-13"),
    ("NEWYEAR24", "percent", 10, "2024-01-01", "2024-01-15"),
    ("NEWYEAR25", "percent", 10, "2025-01-01", "2025-01-15"),
    ("SONGKRAN24", "fixed", 100, "2024-04-10", "2024-04-17"),
    ("SONGKRAN25", "fixed", 100, "2025-04-10", "2025-04-17"),
    ("WELCOME50", "fixed", 50, "2024-01-01", "2025-12-31"),
]


def ts(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else None


def money(x: float) -> str:
    return f"{x:.2f}"


def gen_categories() -> list[dict]:
    return [{"category_id": c[0], "name_th": c[1], "name_en": c[2]} for c in CATEGORIES]


def gen_products(rng: random.Random, n: int, null_category_rate: float) -> tuple[list[dict], dict[int, int]]:
    """Return product rows and product_id -> true category (used even when the row's category is NULL)."""
    rows, true_cat = [], {}
    per_cat = [n // len(CATEGORIES) + (1 if i < n % len(CATEGORIES) else 0) for i in range(len(CATEGORIES))]
    pid = 1
    for (cat_id, _, _, _, _, (lo, hi)), count in zip(CATEGORIES, per_cat):
        combos = [f"{noun} {var}" for noun in PRODUCT_NOUNS[cat_id] for var in PRODUCT_VARIANTS]
        for name in rng.sample(combos, count):
            # log-uniform price snapped to a "...9" ending
            price = round(lo * (hi / lo) ** rng.random(), -1) - 1
            price = max(price, lo)
            cost = round(price * rng.uniform(0.5, 0.8), 2)
            category = None if rng.random() < null_category_rate else cat_id
            rows.append({"product_id": pid, "name": name, "category_id": category,
                         "price": money(price), "cost": money(cost)})
            true_cat[pid] = cat_id
            pid += 1
    return rows, true_cat


def gen_customers(rng: random.Random, n: int, dup_rate: float, bkk_alias_rate: float) -> list[dict]:
    n_dup = round(n * dup_rate)
    n_unique = n - n_dup
    signup_start, signup_end = date(2023, 1, 1), date(2025, 12, 15)
    span = (signup_end - signup_start).days
    prov_names = [p[0] for p in PROVINCES]
    prov_weights = [p[1] for p in PROVINCES]

    def province_label(canonical: str) -> str:
        if canonical == "กรุงเทพมหานคร" and rng.random() < bkk_alias_rate:
            return rng.choice(BANGKOK_ALIASES)
        return canonical

    people = []
    for i in range(n_unique):
        (fth, fen), (lth, len_) = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        canonical = rng.choices(prov_names, prov_weights)[0]
        people.append({
            "name": f"{fth} {lth}",
            "email": f"{fen}.{len_}{i + 1}@{rng.choice(EMAIL_DOMAINS)}",
            "phone": f"0{rng.choice('689')}{rng.randrange(10_000_000, 100_000_000)}",
            "canonical": canonical,
            "province": province_label(canonical),
            "signup_date": signup_start + timedelta(days=rng.randrange(span)),
            "member_status": rng.choices(["none", "silver", "gold"], [60, 25, 15])[0],
            "activity": min(rng.paretovariate(1.6), 10.0),
            "seq": i,
        })
    # Duplicates: same person registers again later with the same email; phone changes half the time.
    for j, orig in enumerate(rng.sample(people[:], n_dup)):
        later = orig["signup_date"] + timedelta(days=rng.randrange(30, 400))
        people.append({
            **orig,
            "phone": orig["phone"] if rng.random() < 0.5
            else f"0{rng.choice('689')}{rng.randrange(10_000_000, 100_000_000)}",
            "province": province_label(orig["canonical"]),
            "signup_date": min(later, signup_end),
            "member_status": "none",
            "seq": n_unique + j,
        })
    people.sort(key=lambda p: (p["signup_date"], p["seq"]))
    for cid, p in enumerate(people, start=1):
        p["customer_id"] = cid
    return people


def gen_coupons(rng: random.Random, n: int) -> list[dict]:
    rows = []
    for code, dtype, value, vfrom, vto in SPECIAL_COUPONS[:n]:
        rows.append({"code": code, "discount_type": dtype, "discount_value": value,
                     "valid_from": vfrom, "valid_to": vto})
    k = 1
    while len(rows) < n:
        start = date(2024, 1, 1) + timedelta(days=rng.randrange(0, 700))
        end = min(start + timedelta(days=rng.randrange(7, 45)), date(2025, 12, 31))
        if rng.random() < 0.5:
            dtype, value, prefix = "percent", rng.choice([5, 8, 10, 15]), "SAVE"
        else:
            dtype, value, prefix = "fixed", rng.choice([30, 50, 80, 100, 150]), "DISC"
        rows.append({"code": f"{prefix}{value}-{k:02d}", "discount_type": dtype, "discount_value": value,
                     "valid_from": start.isoformat(), "valid_to": end.isoformat()})
        k += 1
    rows.sort(key=lambda r: (r["valid_from"], r["code"]))
    for cid, r in enumerate(rows, start=1):
        r["coupon_id"] = cid
    return rows


def day_weight(d: date) -> float:
    progress = (d - START.date()).days / (END.date() - START.date()).days
    w = 1.0 + 0.35 * progress  # business grows over the two years
    if d.weekday() >= 5:
        w *= 1.15
    if d.day in (25, 26, 27, 28):  # payday
        w *= 1.2
    if d.month == 12:
        w *= 1.15
    if (d.month, d.day) == (11, 11):
        w *= 9.0
    elif (d.month, d.day) == (12, 12):
        w *= 7.0
    elif (d.month, d.day) in ((11, 10), (11, 12), (12, 11), (12, 13)):
        w *= 2.0
    return w


def gen_order_times(rng: random.Random, n: int) -> list[datetime]:
    days = [START.date() + timedelta(days=i) for i in range((END.date() - START.date()).days + 1)]
    cum, acc = [], 0.0
    for d in days:
        acc += day_weight(d)
        cum.append(acc)
    hour_w = [3, 1, 1, 1, 1, 1, 2, 3, 4, 5, 6, 7, 9, 8, 6, 6, 6, 7, 8, 10, 12, 13, 11, 6]
    out = []
    for d in rng.choices(days, cum_weights=cum, k=n):
        if (d.month, d.day) in ((11, 11), (12, 12)) and rng.random() < 0.3:
            hour = 0  # midnight flash-sale rush
        else:
            hour = rng.choices(range(24), hour_w)[0]
        out.append(datetime(d.year, d.month, d.day, hour, rng.randrange(60), rng.randrange(60)))
    out.sort()
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--orders", type=int, default=10_000)
    ap.add_argument("--customers", type=int, default=3_000)
    ap.add_argument("--products", type=int, default=500)
    ap.add_argument("--coupons", type=int, default=30)
    ap.add_argument("--dup-rate", type=float, default=0.03, help="share of customers that are duplicates")
    ap.add_argument("--bkk-alias-rate", type=float, default=0.5,
                    help="share of Bangkok customers whose province is not spelled 'กรุงเทพมหานคร'")
    ap.add_argument("--null-category-rate", type=float, default=0.03)
    ap.add_argument("--cancel-rate", type=float, default=0.07)
    ap.add_argument("--refund-rate", type=float, default=0.04)
    ap.add_argument("--failed-payment-rate", type=float, default=0.06)
    ap.add_argument("--split-payment-rate", type=float, default=0.04)
    ap.add_argument("--review-rate", type=float, default=0.45)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    categories = gen_categories()
    products, true_cat = gen_products(rng, args.products, args.null_category_rate)
    customers = gen_customers(rng, args.customers, args.dup_rate, args.bkk_alias_rate)
    coupons = gen_coupons(rng, args.coupons)

    price_of = {p["product_id"]: float(p["price"]) for p in products}
    products_by_cat: dict[int, list[int]] = {}
    for pid, cat in true_cat.items():
        products_by_cat.setdefault(cat, []).append(pid)
    cat_ids = [c[0] for c in CATEGORIES]
    region_of = {p[0]: p[2] for p in PROVINCES}
    delivery_days = {p[0]: p[3] for p in PROVINCES}
    cat_weights = {
        region: [c[3] * boost.get(c[0], 1.0) for c in CATEGORIES] for region, boost in REGION_BOOST.items()
    }
    rating_adj = {c[0]: c[4] for c in CATEGORIES}
    signups = [c["signup_date"] for c in customers]
    pay_names, pay_w = [m[0] for m in PAY_METHODS], [m[1] for m in PAY_METHODS]
    online_methods = ["PromptPay", "card", "transfer"]

    orders, items, payments, reviews = [], [], [], []
    for order_id, created in enumerate(gen_order_times(rng, args.orders), start=1):
        # customer who had signed up by then, biased toward active buyers
        k = bisect.bisect_right(signups, created.date())
        while True:
            cust = customers[rng.randrange(k)]
            if rng.random() * 10.0 < cust["activity"]:
                break
        region = region_of[cust["canonical"]]
        channel = rng.choices([c[0] for c in CHANNELS], [c[1] for c in CHANNELS])[0]

        valid = [c for c in coupons if c["valid_from"] <= created.date().isoformat() <= c["valid_to"]]
        is_sale_day = (created.month, created.day) in ((11, 10), (11, 11), (11, 12), (12, 11), (12, 12), (12, 13))
        coupon = rng.choice(valid) if valid and rng.random() < (0.5 if is_sale_day else 0.15) else None

        n_items = rng.choices([1, 2, 3, 4, 5], [35, 30, 19, 10, 6])[0]
        lines = []
        used = []
        for _ in range(n_items):
            cat = rng.choices(cat_ids, cat_weights[region])[0]
            pid = rng.choice(products_by_cat[cat])
            if pid in used:
                continue
            used.append(pid)
            price = price_of[pid]
            unit_price = price if created.year == 2025 else round(price * 0.95, 2)
            qty = 1 if price > 3000 else rng.choices([1, 2, 3, 4], [70, 20, 7, 3])[0]
            line_total = qty * unit_price
            discount = round(line_total * rng.uniform(0.05, 0.2), 2) if rng.random() < 0.08 else 0.0
            lines.append([pid, qty, unit_price, discount])
        if coupon:
            if coupon["discount_type"] == "percent":
                for ln in lines:
                    ln[3] = round(ln[3] + (ln[1] * ln[2] - ln[3]) * coupon["discount_value"] / 100, 2)
            else:
                top = max(lines, key=lambda ln: ln[1] * ln[2])
                top[3] = round(top[3] + min(coupon["discount_value"], top[1] * top[2] - top[3]), 2)
        total = round(sum(ln[1] * ln[2] - ln[3] for ln in lines), 2)
        for pid, qty, unit_price, discount in lines:
            items.append({"order_item_id": len(items) + 1, "order_id": order_id, "product_id": pid,
                          "quantity": qty, "unit_price": money(unit_price), "discount": money(discount)})

        method = rng.choices(pay_names, pay_w)[0]
        fate = rng.random()
        paid_at = shipped_at = delivered_at = None
        lo_d, hi_d = delivery_days[cust["canonical"]]
        ship_delay = timedelta(hours=rng.uniform(4, 72))
        deliver_delay = timedelta(days=rng.randint(lo_d, hi_d), hours=rng.uniform(0, 10))
        if method == "COD":
            shipped_at = created + ship_delay
            delivered_at = shipped_at + deliver_delay
            paid_at = delivered_at
        else:
            pay_wait = timedelta(minutes=rng.uniform(1, 30)) if method != "transfer" \
                else timedelta(minutes=rng.uniform(10, 1440))
            paid_at = created + pay_wait
            shipped_at = paid_at + ship_delay
            delivered_at = shipped_at + deliver_delay

        if fate < args.cancel_rate:
            status = "cancelled"
            failed_attempt = method != "COD" and rng.random() < 0.4
            paid_at = shipped_at = delivered_at = None
            if failed_attempt:
                payments.append({"order_id": order_id, "method": method, "amount": money(total),
                                 "status": "failed", "created_at": ts(created + timedelta(minutes=rng.uniform(1, 20)))})
        elif fate < args.cancel_rate + args.refund_rate and delivered_at <= END:
            status = "refunded"
            payments.append({"order_id": order_id, "method": method, "amount": money(total),
                             "status": "refunded", "created_at": ts(paid_at)})
        else:
            if paid_at > END:
                status, paid_at, shipped_at, delivered_at = "pending", None, None, None
            elif shipped_at > END:
                status, shipped_at, delivered_at = "paid", None, None
            elif delivered_at > END:
                status, delivered_at = "shipped", None
            else:
                status = "delivered"
            if method == "COD" and status != "delivered":
                # COD is collected on delivery: nothing paid yet
                status = "pending" if shipped_at is None else "shipped"
                paid_at = None
            if paid_at is not None:
                r = rng.random()
                if method != "COD" and r < args.failed_payment_rate:
                    first = rng.choice(online_methods)
                    payments.append({"order_id": order_id, "method": first, "amount": money(total),
                                     "status": "failed",
                                     "created_at": ts(paid_at - timedelta(minutes=rng.uniform(1, 15)))})
                    payments.append({"order_id": order_id, "method": method, "amount": money(total),
                                     "status": "success", "created_at": ts(paid_at)})
                elif method != "COD" and r < args.failed_payment_rate + args.split_payment_rate:
                    part = round(total * rng.uniform(0.3, 0.7), 2)
                    payments.append({"order_id": order_id, "method": rng.choice(online_methods),
                                     "amount": money(part), "status": "success",
                                     "created_at": ts(paid_at - timedelta(minutes=rng.uniform(1, 30)))})
                    payments.append({"order_id": order_id, "method": method, "amount": money(round(total - part, 2)),
                                     "status": "success", "created_at": ts(paid_at)})
                else:
                    payments.append({"order_id": order_id, "method": method, "amount": money(total),
                                     "status": "success", "created_at": ts(paid_at)})

        orders.append({"order_id": order_id, "customer_id": cust["customer_id"], "status": status,
                       "channel": channel, "coupon_id": coupon["coupon_id"] if coupon else None,
                       "created_at": ts(created), "paid_at": ts(paid_at), "shipped_at": ts(shipped_at),
                       "delivered_at": ts(delivered_at)})

        if status in ("delivered", "refunded"):
            p_review = 0.75 if status == "refunded" else args.review_rate
            if rng.random() < p_review:
                review_at = delivered_at + timedelta(hours=rng.uniform(2, 24 * 14))
                if review_at <= END:
                    late = (delivered_at - created).days >= 5
                    mean = 4.1 + sum(rating_adj[true_cat[ln[0]]] for ln in lines) / len(lines)
                    mean -= 0.8 if late else 0.0
                    mean -= 2.0 if status == "refunded" else 0.0
                    rating = min(5, max(1, round(rng.gauss(mean, 0.9))))
                    comment = None if rng.random() < 0.15 else rng.choice(REVIEW_COMMENTS[rating])
                    reviews.append({"order_id": order_id, "rating": rating, "comment": comment,
                                    "created_at": ts(review_at)})

    payments.sort(key=lambda p: (p["created_at"], p["order_id"]))
    for i, p in enumerate(payments, start=1):
        p["payment_id"] = i
    reviews.sort(key=lambda r: (r["created_at"], r["order_id"]))
    for i, r in enumerate(reviews, start=1):
        r["review_id"] = i

    tables = {
        "categories": (categories, ["category_id", "name_th", "name_en"]),
        "products": (products, ["product_id", "name", "category_id", "price", "cost"]),
        "customers": (customers, ["customer_id", "name", "email", "phone", "province", "signup_date",
                                  "member_status"]),
        "coupons": (coupons, ["coupon_id", "code", "discount_type", "discount_value", "valid_from", "valid_to"]),
        "orders": (orders, ["order_id", "customer_id", "status", "channel", "coupon_id", "created_at", "paid_at",
                            "shipped_at", "delivered_at"]),
        "order_items": (items, ["order_item_id", "order_id", "product_id", "quantity", "unit_price", "discount"]),
        "payments": (payments, ["payment_id", "order_id", "method", "amount", "status", "created_at"]),
        "reviews": (reviews, ["review_id", "order_id", "rating", "comment", "created_at"]),
    }
    args.out.mkdir(parents=True, exist_ok=True)
    for name, (rows, cols) in tables.items():
        path = args.out / f"{name}.csv"
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(cols)
            for row in rows:
                w.writerow(["" if row[c] is None else row[c] for c in cols])
        print(f"{name:<12} {len(rows):>6} rows -> {path}")


if __name__ == "__main__":
    main()
