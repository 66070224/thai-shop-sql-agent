# Error analysis — config `full`

ที่มา: `eval/results/full_20261023-120433.jsonl` และ `full_20261023-120549.jsonl` (ค่า default: k_schema=5, k_examples=1,
min_score=0.35, rule-table DDL on, max_steps=5) ทั้งสองรอบได้คะแนนเหมือนกันทุกข้อ และ pred SQL เหมือนกันทุกข้อ
EX 0.867 (26/30), EX-strict 0.733 (22/30), valid SQL 1.000, ทุกข้อจบแบบ `answered` (ไม่มีข้อไหนชน max_steps หรือ no-progress)

SQL ที่ยกมาด้านล่างคือ `pred_sql` จริงจากไฟล์ผล (ตัดช่องว่างให้สั้นลง) ไม่ได้แก้

> หัวข้อ "ผิด EX" ถึง "Demo" ด้านล่างเป็นผลก่อนปักกฎ (ค่า default เดิม, `pinned_rules=()`) ซึ่งเป็นที่มาของการทดลอง
> ผลของค่า default ปัจจุบันอยู่ที่หัวข้อ **"หลังปักกฎ"** ท้ายไฟล์

## ผิด EX (4 ข้อ)

| กลุ่ม | ข้อ | หมายเหตุ |
|---|---|---|
| จังหวัดไม่ตรง | q12, q26 | ไม่ได้รวมชื่อกรุงเทพฯ ทั้ง 4 แบบ |
| ใช้ชื่อหมวดผิดภาษา (name_th กับคำถามอังกฤษ) | q26, q28 | ตามที่ตัดสินไว้: นับเป็นความผิดของ model |
| นิยามยอดขายผิด (ราคา / ส่วนลด) | q26 | ใช้ `products.price` และไม่หักส่วนลด |
| ลืมตัด cancelled/refunded | q26 | ไม่มีเงื่อนไข `status` |
| อื่นๆ: ลำดับ AND/OR | q25 | ไม่ใส่วงเล็บครอบ OR |
| join ผิด / timestamp ผิด / นับลูกค้าซ้ำ | – | ไม่พบใน `full` |

q26 ผิดพร้อมกันหลายกลุ่ม จึงนับซ้ำในตาราง

### q12 — จังหวัดไม่ตรง (medium, en)
> How many customers do we have in Bangkok?

```sql
SELECT COUNT(DISTINCT email) FROM customers WHERE province = 'กรุงเทพฯ' LIMIT 100
```
นับตามอีเมลถูกแล้ว แต่ `'กรุงเทพฯ'` ไม่มีในข้อมูล (ค่าจริงคือ `กรุงเทพมหานคร`, `กรุงเทพ`, `กทม.`, `Bangkok`) ผลจึงเป็น 0
และ agent ตอบ "We have 0 customers in Bangkok." ทันทีโดยไม่ตรวจซ้ำ chunk `rule:province-names` ไม่อยู่ใน context เริ่มต้น
(ได้ `relationships, table:customers, rule:duplicate-customers, table:products, table:reviews, table:orders, ex13`)
และ agent ไม่ได้เรียก `search_schema` ทั้งที่ผลเป็น 0

### q26 — หลายกลุ่มพร้อมกัน (hard, en)
> What are the top 3 product categories by sales among Bangkok customers in 2025?

```sql
SELECT T3.name_th, SUM(T1.price * T2.quantity) AS total_sales
FROM products AS T1 INNER JOIN order_items AS T2 ON T1.product_id = T2.product_id
INNER JOIN categories AS T3 ON T1.category_id = T3.category_id
INNER JOIN orders AS T4 ON T2.order_id = T4.order_id
INNER JOIN customers AS T5 ON T4.customer_id = T5.customer_id
WHERE T5.province LIKE '%กรุงเทพ%' AND STRFTIME('%Y', T4.created_at) = '2025'
GROUP BY T3.category_id ORDER BY total_sales DESC LIMIT 3
```
- จังหวัด: `LIKE '%กรุงเทพ%'` ได้ `กรุงเทพมหานคร`/`กรุงเทพ` แต่ตก `กทม.` และ `Bangkok`
- ยอดขาย: `products.price * quantity` แทน `quantity * unit_price - discount`
- ไม่ตัด `cancelled`/`refunded`
- ตอบ `name_th` กับคำถามภาษาอังกฤษ
context เริ่มต้นไม่มีทั้ง `rule:sales-revenue-definition` และ `rule:province-names` (agent ใช้ 3 steps แต่ไม่ได้เรียก `search_schema`)

### q28 — ชื่อหมวดผิดภาษา (hard, en)
> Which product category sells the most units to customers in Khon Kaen?

```sql
SELECT T2.name_th FROM categories AS T2 INNER JOIN products AS T1 ON T2.category_id = T1.category_id
INNER JOIN order_items AS T3 ON T1.product_id = T3.product_id INNER JOIN orders AS T4 ON T3.order_id = T4.order_id
INNER JOIN customers AS T5 ON T4.customer_id = T5.customer_id
WHERE T5.province = 'ขอนแก่น' AND T4.status NOT IN ('cancelled', 'refunded')
GROUP BY T2.category_id ORDER BY SUM(T3.quantity) DESC LIMIT 1
```
ตรรกะถูกทั้งหมด (จังหวัดภาษาไทย, ตัดสถานะ, นับหน่วย) ต่างจาก gold แค่ `name_th` แทน `name_en`
คำตอบที่ได้คือหมวดเดียวกัน (อาหารและเครื่องดื่ม) แต่ตามกติกาที่ตัดสินไว้นับเป็นผิด

### q25 — ลำดับ AND/OR (hard, th)
> ยอดขายวัน 11.11 ของปี 2024 และปี 2025 แต่ละปีเท่าไหร่

```sql
SELECT strftime('%Y', created_at) AS year, SUM(oi.quantity * oi.unit_price - oi.discount) AS sales
FROM orders o JOIN order_items oi ON oi.order_id = o.order_id
WHERE o.status NOT IN ('cancelled', 'refunded') AND date(o.created_at) = '2024-11-11' OR date(o.created_at) = '2025-11-11'
GROUP BY year
```
`AND` ผูกแน่นกว่า `OR` ทำให้ตัวกรองสถานะใช้กับปี 2024 เท่านั้น ยอดปี 2025 จึงรวม order ที่ยกเลิก/คืนเงิน
(ตีความ 11.11 ถูกแล้ว) — ผิดเงียบเพราะ SQL รันได้และผลดูสมเหตุสมผล agent จึงไม่มีสัญญาณให้แก้

## ผ่าน EX แต่ไม่ผ่าน EX-strict (4 ข้อ)

q10, q16, q17, q23: ตรรกะถูก แต่คืนคอลัมน์ตัวเลขที่ใช้จัดอันดับมาด้วย (เช่น `failed_count`, `total_revenue`, `total_sales`,
`average_rating`) ขณะที่ gold คืนเฉพาะชื่อ ไม่ใช่ความผิดเชิงความหมาย

## ข้อสังเกตจากกลุ่มที่ผิด

- 3 ใน 4 ข้อที่ผิด EX ต้องใช้กฎที่**ไม่ได้อยู่ใน context เริ่มต้น** (province names, sales definition) และ agent ไม่ได้เรียก
  `search_schema` เอง แม้ผลจะเป็น 0 แถว/0 คน ส่วนที่ยังขาดของ agent จึงอยู่ที่การตรวจผลลัพธ์ที่น่าสงสัย ไม่ใช่การแก้ SQL error
  (valid SQL = 1.000 แล้ว)
- ไม่มีข้อไหนผิดเพราะ guard หรือ timeout

## Demo

trace ด้านล่างมาจากการรัน agent แยกต่างหาก (2026-10-23 ~12:10, ค่า default เดียวกัน) เพื่อดูลำดับ event — ไม่ใช่รอบที่ใช้ให้คะแนน
และลำดับขั้นอาจต่างจากรอบ eval ได้ (proxy ไม่ deterministic ทั้งที่ temperature 0)

### สำเร็จ: q22 (medium, en, traps: cancelled_refunded + duplicate_customers)
> Who are our top 5 customers by total spending? Show their email and how much they spent.

1. retrieval: `relationships, rule:duplicate-customers, table:customers, table:orders, table:reviews, table:products, ex30` —
   ไม่มีกฎยอดขาย
2. `search_schema {"query": "sales / revenue"}` → ได้ `rule:sales-revenue-definition`
3. `run_sql`: `SUM(oi.quantity * oi.unit_price - oi.discount)`, `WHERE o.status NOT IN ('cancelled', 'refunded')`,
   `GROUP BY c.email` → 5 แถว
4. answer + citations: tables `customers, order_items, orders`; chunks รวม `rule:sales-revenue-definition` ที่ดึงมาเพิ่ม

แสดงจุดเด่นของ agent ที่ B1/B2 ทำไม่ได้: context เริ่มต้นไม่พอ แล้ว agent ค้นกฎที่ขาดเองก่อนเขียน SQL
(ก่อนปักกฎ B1/B2 ข้อนี้ผิดทั้ง 4 รอบ: B1 ใช้ `quantity * unit_price` ไม่หักส่วนลด, B2 ใช้ `products.price * quantity`;
หลังปักกฎ `rule:sales-revenue-definition` อยู่ใน context เริ่มต้นแล้ว B1/B2 จึงตอบข้อนี้ถูกทุกรอบ และ agent ไม่จำเป็นต้องค้นเอง
— demo นี้จึงแสดงพฤติกรรมการค้นของ agent ได้เฉพาะเมื่อรันด้วย `--no-pin-rules`)

### ล้มเหลว: q26 (hard, en)
1. retrieval: `table:categories, table:products, table:customers, rule:dates-and-time-periods, rule:duplicate-customers, ex37`
2. `run_sql` ด้วย `province = 'กรุงเทพฯ'` → 0 แถว
3. `run_sql "SELECT DISTINCT province FROM customers LIMIT 10"` → เห็น `กรุงเทพมหานคร` และ `Bangkok`
4. `run_sql "SELECT name_th FROM categories"`
5. model ตอบเป็นข้อความว่า "จะปรับ SQL ให้ครอบคลุมทุกชื่อของกรุงเทพฯ" แต่**ไม่ได้เรียก tool** loop จึงหยุดแบบ `answered`
   และ `FinalAnswer.sql` กลายเป็น query สำรวจข้อมูลข้อสุดท้าย (`SELECT name_th FROM categories`)

แสดงข้อจำกัด 2 อย่าง: (1) กฎสำคัญไม่อยู่ใน context เริ่มต้น และ (2) เงื่อนไขหยุด "ไม่เรียก tool = ตอบแล้ว" กับ
"SQL ล่าสุดที่รันสำเร็จ = คำตอบ" ทำให้ query สำรวจข้อมูลถูกใช้เป็นคำตอบได้

## หลังปักกฎ (default ใหม่: `pinned_rules` = sales-revenue-definition + province-names)

ที่มา: `eval/results/full_20261023-125440.jsonl` และ `full_20261023-125651.jsonl` (`--pin-rules`, ค่าอื่นเหมือนเดิม)
EX 0.900 (27/30) ทั้งสองรอบ, EX-strict 0.733, valid SQL 1.000, ทุกข้อจบแบบ `answered`
รายงาน before/after เต็มอยู่ที่ `eval/results/before_after_pinned_rules.md`

| กลุ่ม | ก่อนปัก | หลังปัก |
|---|---|---|
| จังหวัดไม่ตรง | q12, q26 | – |
| นิยามยอดขายผิด / ลืมตัด cancelled/refunded | q26 | – |
| ใช้ชื่อหมวดผิดภาษา (name_th กับคำถามอังกฤษ) | q26, q28 | q26, q28 |
| ลำดับ AND/OR | q25 | q25 |
| join ผิด | – | q28 (รอบ #1 เท่านั้น) |

- **q12 แก้ได้ทั้งสองรอบ:** `WHERE T1.province IN ('กรุงเทพมหานคร', 'กรุงเทพ', 'กทม.', 'Bangkok')`
- **q26 ยังผิด แต่เหลือสาเหตุเดียว:** context เริ่มต้นมีทั้งสองกฎแล้ว
  (`relationships, rule:sales-revenue-definition, rule:province-names, table:categories, ...`) และ SQL ใช้ทั้งสองกฎถูก
  ```sql
  SELECT T3.name_th, SUM(T1.quantity * T1.unit_price - T1.discount) AS sales
  FROM order_items AS T1 INNER JOIN orders AS T2 ON T1.order_id = T2.order_id
  INNER JOIN products AS T4 ON T1.product_id = T4.product_id INNER JOIN categories AS T3 ON T4.category_id = T3.category_id
  INNER JOIN customers AS T5 ON T2.customer_id = T5.customer_id
  WHERE T2.status NOT IN ('cancelled', 'refunded') AND STRFTIME('%Y', T2.created_at) = '2025'
    AND T5.province IN ('กรุงเทพมหานคร', 'กรุงเทพ', 'กทม.', 'Bangkok')
  GROUP BY T3.category_id ORDER BY sales DESC LIMIT 3
  ```
  เหลือแค่ `name_th` กับคำถามภาษาอังกฤษ (ตรวจแล้ว: ถ้าเปลี่ยนเป็น `name_en` จะตรง gold ทั้งสองรอบ — ตรวจเพื่อวิเคราะห์เท่านั้น ไม่ได้นับเป็นคะแนน)
- **q28 ยังผิดทั้งสองรอบ:** รอบ #2 ผิดแค่ `name_th` (เหมือนก่อนปัก) แต่รอบ #1 join ผิดด้วย —
  `FROM customers AS T1 INNER JOIN categories AS T2 ON T1.province = 'ขอนแก่น'` แล้วไม่มีเงื่อนไขเชื่อม `products.category_id`
  กับ `categories` (กลายเป็น cross join) ข้อนี้ผิดทั้งก่อนและหลัง จึงไม่นับเป็น ถูก→ผิด แต่เป็นตัวอย่างว่า prompt ที่ยาวขึ้นเปลี่ยน SQL ข้ออื่นได้
- **q25 เหมือนเดิมทุกตัวอักษร:** `... AND date(o.created_at) = '2024-11-11' OR date(o.created_at) = '2025-11-11'`

ข้อสังเกต: ข้อที่ยังผิดทั้งหมดไม่ใช่ปัญหา "กฎไม่อยู่ใน context" แล้ว แต่เป็นภาษาของชื่อหมวด (q26, q28) และ AND/OR (q25)
ซึ่งเป็นข้อเสนอแยกในรายงาน before/after (ไม่ได้ทำใน phase นี้)

### Demo หลังปักกฎ

trace ล้มเหลวของ q26 ด้านบนมาจากก่อนปักกฎ: ตอนนี้ q26 ยังล้มจริง (ทั้งสองรอบ eval) แต่ล้มเพราะ `name_th` เท่านั้น
ลำดับ event แบบเดิม (query สำรวจกลายเป็นคำตอบ) จึงอาจไม่เกิดซ้ำ ข้อเสนอสำหรับ demo ล้มเหลว (ยังไม่ได้รัน trace ใหม่ รอตัดสิน):
- **q25 (hard, th)** — ผิดเงียบ: SQL รันได้ ผลดูสมเหตุสมผล ใช้กฎยอดขายถูก แต่ AND/OR ทำให้ปี 2025 รวม order ที่ยกเลิก
  ล้มเหมือนกันทั้ง 4 รอบ (ก่อน 2 หลัง 2) จึง reproduce ได้แน่นอนที่สุด
- หรือ **q26** — แสดงว่ากฎถูกใช้ครบแล้ว แต่ยังผิดเพราะภาษาของชื่อหมวด
