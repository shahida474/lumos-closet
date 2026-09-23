"""SQLite database layer for the Lumos Closet standalone app.

The whole database lives in a single file (see DB_PATH). It is created
automatically on first run -- no separate init step is required, and the
database always starts empty.
"""
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Overridable so Docker / hosts can persist the DB on a mounted volume.
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "lumos.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS products (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL,
    category            TEXT NOT NULL DEFAULT '',
    price               INTEGER NOT NULL DEFAULT 0,   -- price in whole BDT (taka)
    low_stock_threshold INTEGER NOT NULL DEFAULT 5,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS variants (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    size       TEXT NOT NULL DEFAULT '',
    color      TEXT NOT NULL DEFAULT '',
    stock      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS orders (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_no              TEXT NOT NULL UNIQUE,
    customer_name           TEXT NOT NULL,
    phone                   TEXT NOT NULL,
    address                 TEXT NOT NULL,
    status                  TEXT NOT NULL DEFAULT 'pending',  -- pending|confirmed|shipped|delivered|cancelled
    payment_method          TEXT NOT NULL DEFAULT 'cod',      -- cod|bkash|nagad|rocket|card
    payment_status          TEXT NOT NULL DEFAULT 'unpaid',   -- paid|unpaid
    total                   INTEGER NOT NULL DEFAULT 0,       -- total in whole BDT
    subtotal                INTEGER NOT NULL DEFAULT 0,       -- pre-discount total in whole BDT
    discount_type           TEXT NOT NULL DEFAULT '',         -- ''|flat|percent ('' = no discount)
    discount_value          INTEGER NOT NULL DEFAULT 0,       -- taka for flat, 0-100 for percent
    note                    TEXT NOT NULL DEFAULT '',
    steadfast_tracking_code TEXT NOT NULL DEFAULT '',
    steadfast_consignment_id INTEGER,
    created_at              TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS order_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id      INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    variant_id    INTEGER REFERENCES variants(id),  -- may be NULL if variant was deleted later
    product_name  TEXT NOT NULL,                     -- snapshot, survives product edits
    variant_label TEXT NOT NULL DEFAULT '',
    qty           INTEGER NOT NULL,
    unit_price    INTEGER NOT NULL                   -- snapshot in whole BDT
);
"""


def get_db():
    """Return a new SQLite connection with Row access and FK enforcement."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Create the database file and tables if they do not exist yet."""
    parent = os.path.dirname(DB_PATH)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = get_db()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
        _migrate(conn)
    finally:
        conn.close()


def _migrate(conn):
    """Add columns that newer versions of the app expect.

    Keeps databases created by older versions working without any
    manual step -- each missing column is added once and left alone.
    """
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(orders)")}
    for name, ddl in (
        ("subtotal", "INTEGER NOT NULL DEFAULT 0"),
        ("discount_type", "TEXT NOT NULL DEFAULT ''"),
        ("discount_value", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if name not in cols:
            conn.execute(f"ALTER TABLE orders ADD COLUMN {name} {ddl}")
    # Orders written before discounts existed: subtotal equals total.
    conn.execute(
        "UPDATE orders SET subtotal = total "
        "WHERE discount_type = '' AND subtotal = 0"
    )
    conn.commit()


def get_setting(key, default=""):
    conn = get_db()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def set_setting(key, value):
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()
