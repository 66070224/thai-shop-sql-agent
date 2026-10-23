# Thai Shop SQL Agent

Agent ที่แปลงคำถามภาษาธรรมชาติ (ไทย/อังกฤษ) เป็น SQL บนฐานข้อมูล SQLite ของร้านค้าออนไลน์ไทย (ข้อมูลสังเคราะห์
8 ตาราง ม.ค. 2024 – ธ.ค. 2025 ~50k แถว) แล้วรัน SQL แบบอ่านอย่างเดียว ตอบเป็นข้อความพร้อม SQL ผลลัพธ์ และแหล่งอ้างอิง
Capstone วิชา Special Topic 2 (LLM)

- ดึงเฉพาะ schema / กฎธุรกิจ / ตัวอย่างที่เกี่ยวข้องด้วย embedding (`bge-m3`) แทนการใส่ schema ทั้งหมดใน prompt
- agent เรียก tool แบบ native (`search_schema`, `run_sql`) กับ `gemma-4-E4B-it` แก้ SQL เองเมื่อ error
- SQL ทุกคำสั่งจาก model ผ่าน guard (1 statement, SELECT/WITH เท่านั้น, allow-list ตาราง, LIMIT 100) และรันบน connection read-only
- API stream ขั้นตอนของ agent เป็น Server-Sent Events และมีหน้าเว็บไฟล์เดียวแสดงผลแบบ live
- eval ใช้ agent ตัวเดียวกับ API และวัดผลเทียบ baseline 3 แบบ

## สถาปัตยกรรม

```
question
   │
   ▼
retrieval.py ── bge-m3 index (data/index.json) ─ schema_docs.md 16 chunks + examples.jsonl 44 ข้อ
   │   relationships + กฎยอดขาย/จังหวัด ทุกครั้ง + top-k ตาราง/กฎ + DDL ของตารางที่ถูกดึงและที่กฎอ้างถึง + top-k ตัวอย่าง
   ▼
agent.py ── loop: gemma-4-E4B-it (tools=[search_schema, run_sql]) ≤ 5 รอบ
   │            │
   │            └─ tools.py ─ Pydantic validate ─ sql_guard.py ─ db.py (mode=ro, timeout 10 s, ≤ 20 แถวกลับให้ model)
   ▼
FinalAnswer {answer, sql, columns, rows, citations, steps, stopped_reason, usage}
   │
   ├─ eval/run.py  (อ่าน event สุดท้าย → EX / EX-strict / valid SQL)
   └─ api.py       (POST /ask → SSE ทุก event) ─ web/index.html
```

ชั้นของโค้ด (ชั้นล่างไม่ import ชั้นบน): `config` → `db` / `llm` → `retrieval` → `sql_guard` / `tools` → `baseline` / `agent` → `api` → `web`

| ไฟล์ | หน้าที่ |
|---|---|
| `src/thai_shop_agent/config.py` | model, timeout, ค่า retrieval default (`DEFAULT_RETRIEVAL`: k / min_score จาก sweep, `pinned_rules` จาก before/after) |
| `src/thai_shop_agent/llm.py` | OpenAI client (อ่าน `OPENAI_API_KEY` / `OPENAI_BASE_URL` จาก env), retry rate limit, redact |
| `src/thai_shop_agent/retrieval.py` | chunk, index, cosine (Python ล้วน), ประกอบ context |
| `src/thai_shop_agent/sql_guard.py` | `clean` → `guard` (sqlglot) → `add_limit` |
| `src/thai_shop_agent/tools.py`, `schemas.py` | tool 2 ตัว + Pydantic models |
| `src/thai_shop_agent/baseline.py` | B0 / B1 / B2 (single-shot) |
| `src/thai_shop_agent/agent.py` | agent loop + events |
| `src/thai_shop_agent/api.py`, `web/index.html` | FastAPI + หน้าเว็บ |
| `eval/` | testset, validator, run / sweep / compare, ผลทั้งหมดใน `eval/results/` |

## ติดตั้งและรัน

ต้องมี Python 3.14 และ [uv](https://docs.astral.sh/uv/) และสิทธิ์เข้าถึง proxy ของวิชา (OpenAI-compatible)

```bash
uv sync
```

สร้างไฟล์ `.env` จาก `.env.example` แล้วใส่ค่า `OPENAI_API_KEY` และ `OPENAI_BASE_URL` (ห้าม commit `.env`)

```bash
cp .env.example .env
```

สร้างข้อมูล (seed 42 ได้ไฟล์เหมือนเดิมทุก byte) และฐานข้อมูล `data/shop.db`

```bash
uv run scripts/generate_data.py --seed 42
```

```bash
uv run scripts/build_db.py
```

ตรวจ proxy (embedding 1024 มิติ และ native tool calls) แล้วสร้าง index ของ retrieval (`data/index.json` ไม่อยู่ใน git)

```bash
uv run --env-file .env scripts/check_proxy.py
```

```bash
uv run --env-file .env scripts/build_index.py
```

เปิด API และหน้าเว็บที่ http://127.0.0.1:8000

```bash
uv run --env-file .env uvicorn thai_shop_agent.api:app --reload
```

- `POST /ask` body `{"question": "..."}` (1–500 ตัวอักษร) → `text/event-stream` 1 frame ต่อ event: `retrieval`, `tool_call`, `tool_result`, `answer` หรือ `error`
- `GET /health`, `GET /` (หน้าเว็บ)

ทดสอบ (ไม่เรียก proxy)

```bash
uv run pytest
```

## ตัวอย่างคำถามสำหรับลองบนหน้าเว็บ

คัดจาก testset (`eval/testset.jsonl`) โดยดูผลของ `full` ที่ค่า default ปัจจุบัน 2 รอบ
(`eval/results/full_20261023-125440.jsonl`, `eval/results/full_20261023-125651.jsonl`) "คำตอบที่ถูก" คือผลของ gold SQL บน `data/shop.db`
proxy ไม่ deterministic แม้ temperature 0 ผลบนหน้าเว็บจึงอาจต่างจากนี้ได้บ้าง

### Success (ถูกทั้ง 2 รอบ)

| id | คำถาม (พิมพ์ลงช่องได้เลย) | คำตอบที่ถูก | สิ่งที่ข้อนี้ทดสอบ |
|---|---|---|---|
| q12 | How many customers do we have in Bangkok? | 1,015 คน | ชื่อจังหวัดไม่สม่ำเสมอ (กรุงเทพ / กทม. / Bangkok) — ผิดก่อนปักกฎ |
| q13 | ยอดขายรวมของปี 2025 เท่าไหร่ | 38,294,928.77 | นิยามยอดขาย + ตัด cancelled/refunded |
| q21 | ลูกค้าในกรุงเทพฯ ซื้อของไปรวมเป็นเงินเท่าไหร่ในปี 2025 | 15,764,709.43 | ใช้ทั้งกฎยอดขายและกฎชื่อจังหวัด |
| q24 | Show total sales for each month of 2025, in month order. | 12 แถว 2025-01 = 3,075,054.99 … 2025-12 = 4,829,510.58 | group by เดือน + เรียงลำดับ |
| q30 | What was our gross profit in each year? | 2024 = 9,185,515.59, 2025 = 12,914,688.85 | กำไรขั้นต้น (ใช้ `cost` ของสินค้า) |

### Fail (ผิดทั้ง 2 รอบ)

| id | คำถาม | คำตอบที่ถูก | ที่ agent ตอบ | สาเหตุ |
|---|---|---|---|---|
| q25 | ยอดขายวัน 11.11 ของปี 2024 และปี 2025 แต่ละปีเท่าไหร่ | 2024 = 758,090.44, 2025 = 680,605.66 | 2024 = 758,090.44, **2025 = 775,757.86** | `... AND date(...) = '2024-11-11' OR date(...) = '2025-11-11'` ไม่ใส่วงเล็บครอบ `OR` ตัว `status` จึงกรองแค่ปี 2024 (ผิดเงียบ: SQL รันได้ ตัวเลขดูสมเหตุสมผล) |
| q26 | What are the top 3 product categories by sales among Bangkok customers in 2025? | Computers & Laptops, Mobile Phones, Gaming | คอมพิวเตอร์และแล็ปท็อป, โทรศัพท์มือถือ, เกมและอุปกรณ์เกม | ใช้ `name_th` กับคำถามภาษาอังกฤษ (นับเป็นผิดตามกติกา) |
| q28 | Which product category sells the most units to customers in Khon Kaen? | Food & Beverages | อาหารและเครื่องดื่ม | ใช้ `name_th` กับคำถามภาษาอังกฤษ |

trace ทีละขั้นของ q25 อยู่ใน `eval/error_analysis.md` หัวข้อ "Demo หลังปักกฎ"

## วิธีรัน eval

```bash
uv run eval/validate_testset.py
```

```bash
uv run eval/validate_examples.py
```

```bash
uv run --env-file .env eval/retrieval_eval.py
```

```bash
uv run --env-file .env eval/run.py --config full
```

`--config` เป็น `b0`, `b1`, `b2` หรือ `full`; ค่า retrieval ใช้ `DEFAULT_RETRIEVAL` เว้นแต่ส่ง `--k-schema`, `--k-examples`, `--min-score`;
`--ids q01 q12` รันเฉพาะบางข้อ; `--no-pin-rules` ปิดการปักกฎ (ค่า before ของการทดลอง)

```bash
uv run eval/before_after.py --before <ไฟล์ผล before ...> --after <ไฟล์ผล after ...>
```

คำสั่งเต็มพร้อมชื่อไฟล์อยู่ใน `eval/results/before_after_pinned_rules.md`

```bash
uv run --env-file .env eval/sweep.py
```

```bash
uv run eval/compare.py
```

| config | context | การเรียก model |
|---|---|---|
| B0 | DDL ทั้งหมด + schema docs ทั้งหมด | 1 ครั้ง ไม่มี tool |
| B1 | relationships + กฎที่ปักไว้ 2 ข้อ + top-k ตาราง/กฎ + DDL ที่เกี่ยวข้อง | 1 ครั้ง ไม่มี tool |
| B2 | B1 + top-k ตัวอย่าง (คำถาม → SQL) | 1 ครั้ง ไม่มี tool |
| full | เริ่มจาก context แบบ B2 | agent loop ≤ 5 ครั้ง, tool `search_schema` / `run_sql` |

ตัวชี้วัด (ล็อกก่อนเริ่มสร้างระบบ): **EX** (ผลตรง gold ไม่สนลำดับแถว เว้นแต่ order_matters, ยอมคอลัมน์เกิน), **EX-strict**
(คอลัมน์ต้องตรงจำนวนและลำดับ), **valid SQL** (ผ่าน guard และรันได้), schema recall@k, latency, llm_calls, tokens
ทุกค่าแยกตาม difficulty และ lang — testset 30 ข้อ (th 15 / en 15; easy 10 / medium 12 / hard 8)

## ผลลัพธ์

ตัวเลขทั้งหมดมาจากการรันจริง (`eval/results/comparison.md`, สร้างด้วย `eval/compare.py`) temperature 0;
ค่า retrieval default k_schema=5, k_examples=1, min_score=0.35 (B1 ใช้ k_examples=0) เลือกจาก
`eval/results/sweep_20261023-113639.json` และปักกฎ `rule:sales-revenue-definition` + `rule:province-names` ไว้ใน context ทุกครั้ง
(ดู "Measured improvement" ด้านล่าง); B1, B2, full รันอย่างละ 2 รอบและแสดงทั้งสองรอบ

| run | EX | EX-strict | valid SQL | hard EX | th EX | en EX | latency s | llm_calls | prompt tok | completion tok |
|---|---|---|---|---|---|---|---|---|---|---|
| b0 #1 | 0.900 | 0.800 | 0.967 | 0.625 | 1.000 | 0.800 | 1.06 | 1.00 | 3862 | 80 |
| b1 #1 | 0.867 | 0.767 | 1.000 | 0.625 | 0.867 | 0.867 | 1.28 | 1.00 | 2135 | 80 |
| b1 #2 | 0.900 | 0.800 | 1.000 | 0.750 | 0.933 | 0.867 | 1.25 | 1.00 | 2135 | 81 |
| b2 #1 | 0.900 | 0.767 | 1.000 | 0.750 | 0.933 | 0.867 | 1.35 | 1.00 | 2214 | 72 |
| b2 #2 | 0.900 | 0.767 | 1.000 | 0.625 | 0.933 | 0.867 | 1.24 | 1.00 | 2214 | 72 |
| full #1 | 0.900 | 0.733 | 1.000 | 0.625 | 0.933 | 0.867 | 2.22 | 2.03 | 5311 | 135 |
| full #2 | 0.900 | 0.733 | 1.000 | 0.625 | 0.933 | 0.867 | 2.08 | 2.03 | 5312 | 136 |

แยกตาม difficulty (EX / EX-strict / valid SQL, รอบ #1 ของแต่ละ config; รอบ #2 อยู่ใน `comparison.md`)

| config | easy | medium | hard |
|---|---|---|---|
| b0 | 1.000 / 0.900 / 1.000 | 1.000 / 0.833 / 1.000 | 0.625 / 0.625 / 0.875 |
| b1 | 1.000 / 1.000 / 1.000 | 0.917 / 0.750 / 1.000 | 0.625 / 0.500 / 1.000 |
| b2 | 1.000 / 0.900 / 1.000 | 0.917 / 0.750 / 1.000 | 0.750 / 0.625 / 1.000 |
| full | 1.000 / 0.900 / 1.000 | 1.000 / 0.833 / 1.000 | 0.625 / 0.375 / 1.000 |

สรุป

- EX ของ B0, B2 และ full เท่ากันที่ 0.900 (B1 0.867 / 0.900) — ที่ 30 ข้อ ต่างกัน 1 ข้อ = 3.3 pp จึงแยกกันไม่ออกด้วย EX
- ระบบ retrieval ทุกตัวได้ valid SQL 1.000 (B0 0.967) และใช้ prompt เล็กกว่า B0: B1/B2 ~2.1–2.2k vs 3.9k tokens
- full ไม่ได้ดีกว่า B2 ที่ค่า default ใหม่ (EX เท่ากัน, EX-strict ต่ำกว่า) แต่ใช้ token ~2.4 เท่าและช้ากว่า ~1.7 เท่า;
  ข้อดีของ agent เห็นได้เมื่อกฎที่ต้องใช้ไม่อยู่ใน context (ดู demo q22 ใน `eval/error_analysis.md`)
- full ได้ผลเหมือนกันทุกข้อทั้งสองรอบ; B1/B2 ต่างกันระหว่างรอบ 1–2 ข้อ (q19, q25)
- schema recall@5 (min_score 0.35, ไม่เรียก chat model, `retrieval_20261023-130126.json`): 0.969 ก่อนปักกฎ → 1.000 หลังปักกฎ
  (ถ้านับเฉพาะ chunk ตาราง ไม่นับตารางที่กฎอ้างถึง: 0.821)
- ข้อที่ full ยังผิด (q25, q26, q28) วิเคราะห์ไว้ใน `eval/error_analysis.md` หัวข้อ "หลังปักกฎ": ใช้ `name_th` กับคำถามอังกฤษ
  (q26, q28) และลำดับ AND/OR (q25)

## Measured improvement: ปักกฎหลัก 2 ข้อไว้ใน context

เปลี่ยนตัวแปรเดียวคือ `pinned_rules` (ค่าอื่นทั้งหมดคงเดิม) และตั้งเกณฑ์ตัดสินไว้ก่อนรัน after
รายงานเต็ม (สร้างด้วย `eval/before_after.py`): [`eval/results/before_after_pinned_rules.md`](eval/results/before_after_pinned_rules.md)

| | full EX (2 รอบ) | valid SQL | b2 EX (2 รอบ) | prompt tok / คำถาม (full) |
|---|---|---|---|---|
| before (`--no-pin-rules`) | 0.867 / 0.867 | 1.000 | 0.833 / 0.833 | 4713 |
| after (`--pin-rules`, default ตอนนี้) | 0.900 / 0.900 | 1.000 | 0.900 / 0.900 | 5312 (+599, +12.7%) |

- full: q12 ผิด→ถูกทั้งสองรอบ, ไม่มีข้อ ถูก→ผิด; q26 ใช้กฎทั้งสองถูกแล้ว แต่ยังผิดเพราะ `name_th`
- b2: ผิด→ถูก q12, q22, q27; ถูก→ผิดบางรอบ q19, q25 (แต่ละข้อผิด 1 ใน 2 รอบ)
- เหตุผลนอก testset: ใน few-shot 44 ข้อ มี 20 ข้อ (45%) ที่ต้องใช้กฎยอดขาย/ตัด cancelled-refunded หรือชื่อจังหวัด
- ข้อจำกัด: กฎที่ปักเลือกจาก error analysis บน testset เดียวกัน (ไม่มี dev split) และ 1 ข้อ = 3.3 pp

## ข้อจำกัด

- **จูนบน testset:** มี testset ชุดเดียว 30 ข้อ (ไม่มี dev split) ค่า k / min_score เลือกจากผลบนชุดเดียวกับที่รายงาน
  และส่วนต่างระหว่างค่าที่ดีที่สุดคือ 1–2 ข้อ ตัวเลขจึงมองโลกในแง่ดีกว่าความจริง และความแตกต่างระหว่าง config หลายคู่อยู่ในระดับ noise
- **ผลไม่ deterministic:** แม้ temperature 0 proxy ให้ผลต่างกันระหว่างรอบ (B2 ค่าเดียวกันได้ 0.867 ใน sweep, 0.800 และ 0.833 ในสองรอบจริง)
- **Agent ไม่ตรวจผลที่น่าสงสัย:** เมื่อผลเป็น 0 หรือว่าง agent มักตอบเลยแทนที่จะเรียก `search_schema` หากฎที่ขาด
  และเงื่อนไขหยุด "ไม่เรียก tool = ตอบแล้ว" + "SQL ล่าสุดที่รันสำเร็จ = คำตอบ" อาจทำให้ query สำรวจข้อมูลกลายเป็นคำตอบ
- **Rate limit ของ proxy:** 200k tokens/นาที ระบบรอแล้วลองใหม่ (เวลารอไม่นับใน latency ของ eval แต่ผู้ใช้หน้าเว็บต้องรอจริง)
- **ภาษาของคำตอบ:** model บางครั้งตอบเป็นภาษาไทยกับคำถามอังกฤษ และใช้ชื่อหมวด `name_th` (นับเป็นผิดตามกติกา)
- **ข้อมูลสังเคราะห์และ model เดียว:** ผลไม่ได้แทนข้อมูลจริงหรือ model อื่น; ตัวอย่าง few-shot 44 ข้อเขียนขึ้นเองและตรวจไม่ให้ซ้ำกับ testset
  (`eval/validate_examples.py`) แต่ยังสอน pattern เดียวกับที่ testset ใช้
- **ความปลอดภัย:** guard + connection read-only ป้องกันการเขียนและการอ่านตารางนอก 8 ตาราง แต่ไม่ได้จำกัดว่าผู้ใช้เห็นข้อมูลส่วนบุคคล
  (อีเมล / เบอร์โทร) ได้แค่ไหน และไม่มี authentication ที่ API
