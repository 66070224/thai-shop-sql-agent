# Thai Shop schema docs

ฐานข้อมูล SQLite ของร้านค้าออนไลน์ไทย (ข้อมูลสังเคราะห์) ช่วง 2024-01-01 ถึง 2025-12-31 มี 8 ตาราง
แต่ละหัวข้อ `##` คือ 1 chunk สำหรับ retrieval จึงเขียนให้อ่านเข้าใจได้ในตัวเอง

## Table: orders (คำสั่งซื้อ)

One row per order (~10,000 rows). คำสั่งซื้อ 1 รายการต่อแถว

- `order_id` INTEGER PK
- `customer_id` INTEGER → `customers.customer_id`
- `status` TEXT: `pending` (รอชำระ), `paid` (ชำระแล้ว รอส่ง), `shipped` (จัดส่งแล้ว), `delivered` (ถึงมือลูกค้า), `cancelled` (ยกเลิก), `refunded` (คืนเงิน)
- `channel` TEXT: ช่องทางที่สั่ง `app`, `web`, `line`, `facebook`
- `coupon_id` INTEGER → `coupons.coupon_id`, NULL ถ้าไม่ได้ใช้คูปอง
- `created_at` TEXT `YYYY-MM-DD HH:MM:SS`: เวลาที่สั่งซื้อ (วันที่สั่ง / order date)
- `paid_at` TEXT: เวลาที่ชำระครบ, NULL ถ้ายังไม่ชำระหรือยกเลิก (COD ชำระตอนส่งถึง จึงเท่ากับ `delivered_at`)
- `shipped_at` TEXT: เวลาที่ส่งออกจากร้าน, NULL ถ้ายังไม่ส่งหรือยกเลิก
- `delivered_at` TEXT: เวลาที่ถึงมือลูกค้า, NULL ถ้ายังไม่ถึงหรือยกเลิก

ยอดเงินของคำสั่งซื้อไม่ได้เก็บใน `orders` ต้องคำนวณจาก `order_items` (ดูหัวข้อ Rule: sales / revenue)

## Table: order_items (รายการสินค้าในคำสั่งซื้อ)

One row per product line in an order (~22,000 rows).

- `order_item_id` INTEGER PK
- `order_id` INTEGER → `orders.order_id`
- `product_id` INTEGER → `products.product_id`
- `quantity` INTEGER: จำนวนชิ้น
- `unit_price` REAL: ราคาต่อหน่วย ณ วันที่สั่ง (บาท)
- `discount` REAL: ส่วนลดรวมของบรรทัดนี้เป็นบาท (รวมส่วนลดจากคูปองแล้ว) ไม่ใช่ส่วนลดต่อชิ้น

มูลค่าบรรทัด = `quantity * unit_price - discount`

## Table: products (สินค้า)

~500 products with Thai names. สินค้า

- `product_id` INTEGER PK
- `name` TEXT: ชื่อสินค้าภาษาไทย เช่น `หูฟังไร้สาย รุ่นโปร`
- `category_id` INTEGER → `categories.category_id`, **อาจเป็น NULL** (สินค้าบางชิ้นไม่มีหมวด)
- `price` REAL: ราคาขายปัจจุบัน (บาท)
- `cost` REAL: ต้นทุนต่อชิ้น (บาท)

กำไรขั้นต้นต่อบรรทัด = `quantity * unit_price - discount - quantity * products.cost`

## Table: categories (หมวดสินค้า)

20 product categories. หมวดหมู่สินค้า

- `category_id` INTEGER PK
- `name_th` TEXT: ชื่อหมวดภาษาไทย เช่น `ความงามและเครื่องสำอาง`
- `name_en` TEXT: ชื่อหมวดภาษาอังกฤษ เช่น `Beauty & Cosmetics`

หมวดทั้งหมด: โทรศัพท์มือถือ (Mobile Phones), คอมพิวเตอร์และแล็ปท็อป (Computers & Laptops), อุปกรณ์เสริมมือถือ (Mobile Accessories), เครื่องใช้ไฟฟ้า (Home Appliances), เสื้อผ้าผู้หญิง (Women's Fashion), เสื้อผ้าผู้ชาย (Men's Fashion), รองเท้า (Shoes), กระเป๋า (Bags), ความงามและเครื่องสำอาง (Beauty & Cosmetics), สุขภาพ (Health), อาหารและเครื่องดื่ม (Food & Beverages), แม่และเด็ก (Mother & Baby), ของเล่น (Toys), กีฬาและกิจกรรมกลางแจ้ง (Sports & Outdoors), บ้านและสวน (Home & Garden), เครื่องครัว (Kitchenware), สัตว์เลี้ยง (Pet Supplies), หนังสือและเครื่องเขียน (Books & Stationery), ยานยนต์ (Automotive), เกมและอุปกรณ์เกม (Gaming)

## Table: customers (ลูกค้า)

~3,000 customer records. ข้อมูลลูกค้า

- `customer_id` INTEGER PK
- `name` TEXT: ชื่อ-นามสกุลภาษาไทย
- `email` TEXT: อีเมล (ตัวพิมพ์เล็ก) ใช้ระบุตัวคนจริง
- `phone` TEXT: เบอร์โทร 10 หลัก เช่น `0812345678`
- `province` TEXT: จังหวัด (กรุงเทพฯ สะกดได้หลายแบบ ดูหัวข้อ Rule: province names)
- `signup_date` TEXT `YYYY-MM-DD`: วันที่สมัครสมาชิก (มีตั้งแต่ปี 2023)
- `member_status` TEXT: ระดับสมาชิก `none` (ไม่ใช่สมาชิก), `silver`, `gold`

customer record หนึ่งคนอาจซ้ำกันหลาย `customer_id` (ดูหัวข้อ Rule: duplicate customers)

## Table: payments (การชำระเงิน)

~10,000 payment attempts. การชำระเงิน 1 แถวต่อ 1 ครั้งที่พยายามจ่าย

- `payment_id` INTEGER PK
- `order_id` INTEGER → `orders.order_id`
- `method` TEXT: `PromptPay` (พร้อมเพย์), `card` (บัตรเครดิต/เดบิต), `transfer` (โอนผ่านธนาคาร), `COD` (เก็บเงินปลายทาง)
- `amount` REAL: จำนวนเงินของรายการนี้ (บาท)
- `status` TEXT: `success` (สำเร็จ), `failed` (ล้มเหลว), `refunded` (จ่ายแล้วแต่คืนเงิน)
- `created_at` TEXT: เวลาที่ทำรายการ

คำสั่งซื้อหนึ่งรายการมีได้ 0, 1 หรือหลายแถวใน `payments` (ดูหัวข้อ Rule: multiple payments per order)

## Table: reviews (รีวิว)

~4,000 reviews. รีวิวหลังได้รับสินค้า 1 รีวิวต่อคำสั่งซื้อ

- `review_id` INTEGER PK
- `order_id` INTEGER → `orders.order_id` (รีวิวผูกกับคำสั่งซื้อ ไม่ได้ผูกกับสินค้า)
- `rating` INTEGER 1–5 (คะแนน / ดาว)
- `comment` TEXT: ความเห็นภาษาไทย, อาจเป็น NULL
- `created_at` TEXT: เวลาที่รีวิว

คะแนนรีวิวของสินค้าหรือหมวด: join `reviews → order_items (order_id) → products → categories` คะแนนของคำสั่งซื้อจะนับให้ทุกสินค้าในคำสั่งซื้อนั้น

## Table: coupons (คูปองส่วนลด)

30 coupons. คูปองส่วนลด

- `coupon_id` INTEGER PK
- `code` TEXT: รหัสคูปอง เช่น `1111SALE`, `WELCOME50`
- `discount_type` TEXT: `percent` (ลดเป็น %) หรือ `fixed` (ลดเป็นบาท)
- `discount_value` REAL: มูลค่าส่วนลด (เปอร์เซ็นต์ หรือ บาท ตาม `discount_type`)
- `valid_from`, `valid_to` TEXT `YYYY-MM-DD`: ช่วงที่ใช้ได้ (รวมวันสุดท้าย)

คำสั่งซื้อที่ใช้คูปอง: `orders.coupon_id IS NOT NULL` ส่วนลดที่เกิดจริงอยู่ใน `order_items.discount`

## Relationships (join paths)

- `orders.customer_id = customers.customer_id`
- `order_items.order_id = orders.order_id`
- `order_items.product_id = products.product_id`
- `products.category_id = categories.category_id` (ใช้ LEFT JOIN ถ้าต้องการเก็บสินค้าที่ไม่มีหมวด)
- `payments.order_id = orders.order_id`
- `reviews.order_id = orders.order_id`
- `orders.coupon_id = coupons.coupon_id`

ยอดขายตามหมวด: `orders → order_items → products → categories`; ยอดขายตามจังหวัด: `orders → customers` + `order_items`

## Rule: sales / revenue definition (นิยามยอดขาย)

ยอดขาย (sales, revenue, รายได้, ยอดขายรวม, GMV) = `SUM(order_items.quantity * order_items.unit_price - order_items.discount)`
โดยนับเฉพาะคำสั่งซื้อที่ `orders.status NOT IN ('cancelled', 'refunded')`

```sql
SELECT SUM(oi.quantity * oi.unit_price - oi.discount) AS sales
FROM orders o JOIN order_items oi ON oi.order_id = o.order_id
WHERE o.status NOT IN ('cancelled', 'refunded');
```

- ใช้ `order_items.unit_price` (ราคา ณ วันที่ขาย) ไม่ใช่ `products.price` (ราคาปัจจุบัน ซึ่งปี 2024 ต่ำกว่าราคาปัจจุบันประมาณ 5%)
- จำนวนคำสั่งซื้อ (number of orders) ทั่วไปก็ไม่นับ `cancelled` และ `refunded` เว้นแต่คำถามถามถึงคำสั่งซื้อทุกสถานะหรือสถานะนั้นโดยตรง
- ยอดขายเฉลี่ยต่อคำสั่งซื้อ (AOV) = ยอดขาย / จำนวนคำสั่งซื้อที่นับ

## Rule: cancelled and refunded orders (คำสั่งซื้อที่ยกเลิกหรือคืนเงิน)

`orders.status = 'cancelled'` คือยกเลิกก่อนส่ง, `orders.status = 'refunded'` คือส่งถึงแล้วแต่คืนเงิน
ทั้งสองสถานะ**ไม่นับเป็นยอดขาย** และไม่นับเป็นจำนวนคำสั่งซื้อที่ขายได้ แต่แถวใน `order_items` ยังอยู่ จึงต้องกรองที่ `orders.status` ทุกครั้ง
อัตรายกเลิก (cancellation rate) = จำนวน `cancelled` / จำนวนคำสั่งซื้อทั้งหมด

## Rule: NULL timestamps and missing categories (ค่า NULL)

- `orders.paid_at`, `shipped_at`, `delivered_at` เป็น NULL เมื่อขั้นนั้นยังไม่เกิด (สถานะ `pending`, `paid`, `shipped`) หรือคำสั่งซื้อถูกยกเลิก
- ระยะเวลาจัดส่ง (delivery time) = `julianday(delivered_at) - julianday(created_at)` เป็นวัน; ใช้เฉพาะแถวที่ `delivered_at IS NOT NULL`
- `products.category_id` เป็น NULL สำหรับสินค้าบางชิ้น การ `JOIN categories` แบบ INNER จะตัดสินค้ากลุ่มนี้ทิ้ง ใช้ `LEFT JOIN` ถ้าต้องการนับครบ
- `reviews.comment` อาจเป็น NULL (ให้คะแนนแต่ไม่เขียนความเห็น)

## Rule: duplicate customers (ลูกค้าซ้ำ)

ประมาณ 3% ของ `customers` เป็นคนเดิมที่สมัครซ้ำ: `customer_id` ต่างกันแต่ `email` เดียวกัน (เบอร์โทรอาจเหมือนหรือต่างก็ได้)
ดังนั้นคำถาม "ลูกค้ากี่คน" / "how many customers" / "unique customers" ให้นับ `COUNT(DISTINCT customers.email)` ไม่ใช่ `COUNT(*)` หรือ `COUNT(DISTINCT customer_id)`
เมื่อจัดอันดับหรือรวมยอดต่อลูกค้า ให้ group by `email`

## Rule: province names (ชื่อจังหวัดไม่สม่ำเสมอ)

`customers.province` ของกรุงเทพฯ สะกดได้ 4 แบบ ซึ่งหมายถึงที่เดียวกัน: `กรุงเทพมหานคร`, `กรุงเทพ`, `กทม.`, `Bangkok`
กรองกรุงเทพฯ ด้วย `province IN ('กรุงเทพมหานคร', 'กรุงเทพ', 'กทม.', 'Bangkok')`
และเมื่อ group by จังหวัด ให้รวมเป็นค่าเดียว:

```sql
CASE WHEN province IN ('กรุงเทพ', 'กทม.', 'Bangkok') THEN 'กรุงเทพมหานคร' ELSE province END
```

จังหวัดอื่นสะกดเป็นภาษาไทยแบบเดียว: นนทบุรี, ปทุมธานี, สมุทรปราการ, นครปฐม, เชียงใหม่, เชียงราย, ขอนแก่น, นครราชสีมา, อุดรธานี, ชลบุรี, ระยอง, ภูเก็ต, สงขลา, สุราษฎร์ธานี

## Rule: multiple payments per order (การชำระเงินหลายรายการ)

- บางคำสั่งซื้อมีรายการ `failed` ก่อนรายการ `success` (เช่น บัตรถูกปฏิเสธแล้วจ่ายด้วย PromptPay)
- บางคำสั่งซื้อแบ่งจ่าย 2 รายการ `success` ที่รวมกันเท่ากับยอดของคำสั่งซื้อ
- คำสั่งซื้อที่คืนเงินมีรายการ `refunded`; คำสั่งซื้อที่ยกเลิกหรือยังไม่จ่ายอาจไม่มีแถวใน `payments` เลย
- เงินที่รับจริง = `SUM(amount) WHERE payments.status = 'success'`; ห้าม SUM ทุกแถวเพราะจะนับรายการ failed ซ้ำ
- วิธีชำระของคำสั่งซื้อ = `method` ของรายการ `success`; เมื่อนับจำนวนคำสั่งซื้อตามวิธีชำระ ใช้ `COUNT(DISTINCT order_id)`

## Rule: dates and time periods (วันที่และช่วงเวลา)

- timestamp ทุกคอลัมน์เป็น TEXT รูปแบบ `YYYY-MM-DD HH:MM:SS` (เวลาไทย) ใช้ `strftime('%Y', ...)`, `strftime('%Y-%m', ...)`, `date(...)` ได้
- คำถามที่ระบุแค่ปีหรือเดือน (เช่น "ปี 2025", "in March 2024") ให้ใช้ `orders.created_at` (วันที่สั่งซื้อ)
- ช่วงข้อมูล: 2024-01-01 ถึง 2025-12-31
- ไตรมาส (quarter): `(CAST(strftime('%m', created_at) AS INTEGER) + 2) / 3`
- เทศกาลลดราคา: 11.11 คือวันที่ 11 พฤศจิกายน, 12.12 คือวันที่ 12 ธันวาคม ของแต่ละปี ยอดขายสูงกว่าวันปกติหลายเท่า
