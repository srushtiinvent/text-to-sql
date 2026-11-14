"""Create the demo `shop` database (SQLite + a Postgres SQL dump) with deterministic data.

    python scripts/make_demo_db.py

Writes:
  data/demo/databases/shop/shop.sqlite
  data/demo/databases/shop/database_description/*.csv   (BIRD-style column docs)
  data/demo/shop_postgres.sql                           (for docker compose)

The data contains deliberate traps (cancelled and refunded orders, customers with
no orders, multi-item orders that fan out joins) so that wrong-but-plausible SQL
gives a different answer from correct SQL.
"""
from __future__ import annotations

import csv
import datetime as dt
import random
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "demo" / "databases" / "shop"

CITIES = ["Pune", "Mumbai", "Delhi", "Bengaluru", "Jaipur", "Hyderabad", "Chennai", "Kolkata"]
SEGMENTS = ["Consumer", "Corporate", "Small Business"]
FIRST = ["Aarav", "Diya", "Ishaan", "Ananya", "Kabir", "Meera", "Rohan", "Saanvi", "Vihaan",
         "Aditi", "Arjun", "Kavya", "Neel", "Priya", "Reyansh", "Tara", "Yash", "Zoya"]
LAST = ["Sharma", "Patel", "Iyer", "Reddy", "Nair", "Gupta", "Mehta", "Rao", "Singh", "Das"]
CATEGORIES = {
    "Electronics": ["Wireless Earbuds", "Smartwatch", "Bluetooth Speaker", "Power Bank",
                    "USB-C Hub", "Mechanical Keyboard", "Webcam", "Portable SSD"],
    "Books": ["Designing Data-Intensive Applications", "Clean Code", "The Pragmatic Programmer",
              "Deep Work", "Atomic Habits", "Sapiens", "Zero to One", "The Lean Startup"],
    "Home": ["Desk Lamp", "Office Chair", "Water Bottle", "Coffee Mug", "Bedsheet Set",
             "Wall Clock", "Plant Pot", "Storage Box"],
    "Clothing": ["Hoodie", "Running Shoes", "Denim Jacket", "Cotton T-Shirt", "Track Pants",
                 "Rain Jacket", "Formal Shirt", "Sneakers"],
    "Grocery": ["Green Tea", "Basmati Rice", "Olive Oil", "Dark Chocolate", "Almonds",
                "Instant Coffee", "Honey", "Oats"],
}
PRICE_RANGE = {"Electronics": (899, 7999), "Books": (299, 999), "Home": (199, 4999),
               "Clothing": (399, 3999), "Grocery": (99, 899)}
METHODS = ["card", "upi", "netbanking", "cod"]

DDL_SQLITE = """
CREATE TABLE customers (
  customer_id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  city TEXT NOT NULL,
  segment TEXT NOT NULL,
  signup_date TEXT NOT NULL
);
CREATE TABLE products (
  product_id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  category TEXT NOT NULL,
  price REAL NOT NULL
);
CREATE TABLE orders (
  order_id INTEGER PRIMARY KEY,
  customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
  order_date TEXT NOT NULL,
  status TEXT NOT NULL,
  total_amount REAL NOT NULL
);
CREATE TABLE order_items (
  order_id INTEGER NOT NULL REFERENCES orders(order_id),
  product_id INTEGER NOT NULL REFERENCES products(product_id),
  quantity INTEGER NOT NULL,
  unit_price REAL NOT NULL,
  PRIMARY KEY (order_id, product_id)
);
CREATE TABLE payments (
  payment_id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES orders(order_id),
  paid_at TEXT NOT NULL,
  amount REAL NOT NULL,
  method TEXT NOT NULL
);
"""

DDL_POSTGRES = DDL_SQLITE.replace("REAL", "NUMERIC(12,2)").replace(
    "signup_date TEXT", "signup_date DATE").replace("order_date TEXT", "order_date DATE").replace(
    "paid_at TEXT", "paid_at DATE")

DESCRIPTIONS = {
    "orders": [
        ("order_id", "order id", "unique id of the order", ""),
        ("customer_id", "customer id", "the customer who placed the order", ""),
        ("order_date", "order date", "date the order was placed", "YYYY-MM-DD"),
        ("status", "order status", "current status of the order",
         "completed = delivered and paid; cancelled = never paid; refunded = paid then money returned. "
         "Spending/revenue should only count completed orders."),
        ("total_amount", "total amount", "order total in INR including all items", ""),
    ],
    "customers": [
        ("customer_id", "customer id", "unique id of the customer", ""),
        ("name", "customer name", "full name of the customer", ""),
        ("city", "city", "city where the customer lives", ""),
        ("segment", "segment", "customer type", "Consumer, Corporate or Small Business"),
        ("signup_date", "signup date", "date the customer created an account", "YYYY-MM-DD"),
    ],
    "payments": [
        ("payment_id", "payment id", "unique id of the payment", ""),
        ("order_id", "order id", "order this payment belongs to", ""),
        ("paid_at", "paid at", "date the payment was made", "YYYY-MM-DD"),
        ("amount", "amount", "amount paid in INR", ""),
        ("method", "payment method", "how the customer paid", "card, upi, netbanking or cod (cash on delivery)"),
    ],
}


def generate(seed: int = 42):
    rng = random.Random(seed)
    customers = []
    for cid in range(1, 201):
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        signup = dt.date(2025, 1, 1) + dt.timedelta(days=rng.randrange(0, 600))
        customers.append((cid, name, rng.choice(CITIES), rng.choices(SEGMENTS, [6, 2, 2])[0],
                          signup.isoformat()))
    products = []
    pid = 1
    for cat, names in CATEGORIES.items():
        lo, hi = PRICE_RANGE[cat]
        for n in names:
            products.append((pid, n, cat, float(rng.randrange(lo, hi, 10))))
            pid += 1

    # ~15% of customers never order (a NOT EXISTS / LEFT JOIN trap)
    active = [c for c in customers if rng.random() > 0.15]
    orders, items, payments = [], [], []
    payment_id = 1
    start = dt.date(2026, 1, 1)
    for oid in range(1, 1501):
        cust = rng.choice(active)
        day = start + dt.timedelta(days=rng.randrange(0, 317))  # up to 2026-11-13
        status = rng.choices(["completed", "cancelled", "refunded"], [80, 12, 8])[0]
        chosen = rng.sample(products, rng.choice([1, 1, 2, 2, 3, 4]))
        total = 0.0
        for p in chosen:
            qty = rng.choice([1, 1, 1, 2, 3])
            items.append((oid, p[0], qty, p[3]))
            total += qty * p[3]
        orders.append((oid, cust[0], day.isoformat(), status, round(total, 2)))
        if status in ("completed", "refunded"):
            # some orders are paid in two instalments (a join fan-out trap)
            parts = [total] if rng.random() > 0.1 else [round(total * 0.5, 2), round(total - round(total * 0.5, 2), 2)]
            for amt in parts:
                paid = day + dt.timedelta(days=rng.randrange(0, 3))
                payments.append((payment_id, oid, paid.isoformat(), round(amt, 2), rng.choice(METHODS)))
                payment_id += 1
    return {"customers": customers, "products": products, "orders": orders,
            "order_items": items, "payments": payments}


def write_sqlite(data, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.executescript(DDL_SQLITE)
    for table, rows in data.items():
        ph = ",".join("?" * len(rows[0]))
        conn.executemany(f"INSERT INTO {table} VALUES ({ph})", rows)
    conn.commit()
    conn.close()


def write_postgres_dump(data, path: Path):
    def lit(v):
        if v is None:
            return "NULL"
        if isinstance(v, (int, float)):
            return repr(v)
        return "'" + str(v).replace("'", "''") + "'"

    lines = ["-- generated by scripts/make_demo_db.py", DDL_POSTGRES.strip(), ""]
    for table, rows in data.items():
        for i in range(0, len(rows), 500):
            vals = ",\n".join("(" + ",".join(lit(v) for v in r) + ")" for r in rows[i:i + 500])
            lines.append(f"INSERT INTO {table} VALUES\n{vals};")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_descriptions(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    for table, cols in DESCRIPTIONS.items():
        with open(directory / f"{table}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["original_column_name", "column_name", "column_description",
                        "data_format", "value_description"])
            for orig, name, desc, vdesc in cols:
                w.writerow([orig, name, desc, "", vdesc])


def main():
    data = generate()
    write_sqlite(data, OUT_DIR / "shop.sqlite")
    write_descriptions(OUT_DIR / "database_description")
    write_postgres_dump(data, ROOT / "data" / "demo" / "shop_postgres.sql")
    print({t: len(r) for t, r in data.items()})


if __name__ == "__main__":
    main()
