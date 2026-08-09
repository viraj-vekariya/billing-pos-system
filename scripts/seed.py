"""Wipes and recreates the database with a small starter product catalogue."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import init_db, get_db

PRODUCTS = [
    ("White Bread 400g", "SKU-BRD-001", 45.00, 5.0, 40),
    ("Milk 1L", "SKU-MLK-001", 60.00, 5.0, 30),
    ("Basmati Rice 1kg", "SKU-RCE-001", 120.00, 5.0, 25),
    ("Notebook A4 200pg", "SKU-STA-001", 55.00, 12.0, 50),
    ("Ballpoint Pen (Box of 10)", "SKU-STA-002", 80.00, 12.0, 20),
    ("Bluetooth Earphones", "SKU-ELE-001", 899.00, 18.0, 10),
    ("USB Cable 1m", "SKU-ELE-002", 149.00, 18.0, 35),
    ("Dish Soap 500ml", "SKU-HHD-001", 95.00, 18.0, 15),
]


def main():
    init_db()
    conn = get_db()
    conn.executemany(
        "INSERT INTO products (name, sku, unit_price, tax_rate_percent, stock_quantity) "
        "VALUES (?, ?, ?, ?, ?)",
        PRODUCTS,
    )
    conn.commit()
    conn.close()
    print(f"Seeded {len(PRODUCTS)} products.")


if __name__ == "__main__":
    main()
