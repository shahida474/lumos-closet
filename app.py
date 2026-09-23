"""Lumos Closet -- standalone shop manager for a small clothing business.

Run locally:
    pip install -r requirements.txt
    APP_PASSWORD=secret python app.py

Then open http://127.0.0.1:5000 and log in.

Environment variables:
    APP_PASSWORD  password for the login gate (default: "changeme" -- set this!)
    SECRET_KEY    Flask session secret (default: random each restart -- set for production)
    DB_PATH       where the SQLite file lives (default: ./lumos.db)
    PORT          port to listen on (default: 5000)
"""
import os
import re
import secrets
from datetime import date, datetime
from functools import wraps

from flask import (Flask, flash, g, redirect, render_template, request,
                   session, url_for)

import db
import steadfast
from i18n import DEFAULT, SUPPORTED, get_strings

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
APP_PASSWORD = os.environ.get("APP_PASSWORD", "changeme")

if os.environ.get("APP_PASSWORD") is None:
    print("WARNING: APP_PASSWORD is not set -- using default password 'changeme'.")
    print("         Set APP_PASSWORD before exposing this app to the internet!")

db.init_db()

# ---------------------------------------------------------------- helpers ---

STATUSES = ["pending", "confirmed", "shipped", "delivered", "cancelled"]
PAY_METHODS = ["cod", "bkash", "nagad", "rocket", "card"]
PAY_STATUSES = ["paid", "unpaid"]


def lang():
    return session.get("lang", DEFAULT)


def t(key):
    return get_strings(lang()).get(key, key)


def format_taka(value):
    """Format an integer taka amount, e.g. 1250 -> ৳1,250."""
    try:
        return f"৳{int(value):,}"
    except (TypeError, ValueError):
        return "৳0"


@app.context_processor
def inject_common():
    # Available in every template: t(), lang, taka filter values, choice lists.
    return {
        "t": t,
        "lang": lang(),
        "taka": format_taka,
        "statuses": STATUSES,
        "pay_methods": PAY_METHODS,
        "pay_statuses": PAY_STATUSES,
        "today": date.today().isoformat(),
        "this_month": date.today().strftime("%Y-%m"),
    }


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def next_invoice_no():
    """LC-YYYYMMDD-NNNN, unique per day. Matches Steadfast's invoice rules."""
    day = date.today().strftime("%Y%m%d")
    conn = db.get_db()
    try:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM orders WHERE invoice_no LIKE ?",
            (f"LC-{day}-%",),
        ).fetchone()["c"]
    finally:
        conn.close()
    return f"LC-{day}-{n + 1:04d}"


def variant_label(variant):
    parts = [p for p in (variant["size"], variant["color"]) if p]
    return " / ".join(parts) if parts else "—"


def restore_stock(order_id):
    """Add back the stock reserved by an order (used on cancellation)."""
    conn = db.get_db()
    try:
        items = conn.execute(
            "SELECT variant_id, qty FROM order_items WHERE order_id = ?", (order_id,)
        ).fetchall()
        for it in items:
            if it["variant_id"]:
                conn.execute("UPDATE variants SET stock = stock + ? WHERE id = ?",
                             (it["qty"], it["variant_id"]))
        conn.commit()
    finally:
        conn.close()


def reserve_stock(items):
    """Decrement stock for [(variant_id, qty)]. Raises ValueError if short."""
    conn = db.get_db()
    try:
        for variant_id, qty in items:
            row = conn.execute("SELECT stock FROM variants WHERE id = ?",
                               (variant_id,)).fetchone()
            if row is None:
                raise ValueError("Variant not found.")
            if row["stock"] < qty:
                raise ValueError("short")
        for variant_id, qty in items:
            conn.execute("UPDATE variants SET stock = stock - ? WHERE id = ?",
                         (qty, variant_id))
        conn.commit()
    finally:
        conn.close()


# ------------------------------------------------------------------ auth ---

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("password", "") == APP_PASSWORD:
            session["logged_in"] = True
            return redirect(request.args.get("next") or url_for("dashboard"))
        flash(t("wrong_password"), "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/lang/<code>")
def set_lang(code):
    if code in SUPPORTED:
        session["lang"] = code
    return redirect(request.args.get("next") or url_for("dashboard"))


# ------------------------------------------------------------- dashboard ---

@app.route("/")
@login_required
def dashboard():
    conn = db.get_db()
    try:
        today = date.today().isoformat()
        sales = conn.execute(
            "SELECT COALESCE(SUM(total),0) AS s FROM orders "
            "WHERE date(created_at) = ? AND status != 'cancelled'", (today,)
        ).fetchone()["s"]
        pending = conn.execute(
            "SELECT COUNT(*) AS c FROM orders WHERE status = 'pending'"
        ).fetchone()["c"]
        low = conn.execute(
            """SELECT v.id, v.size, v.color, v.stock, p.name, p.low_stock_threshold
               FROM variants v JOIN products p ON p.id = v.product_id
               WHERE v.stock <= p.low_stock_threshold
               ORDER BY v.stock ASC"""
        ).fetchall()
        recent = conn.execute(
            "SELECT * FROM orders ORDER BY id DESC LIMIT 8"
        ).fetchall()
    finally:
        conn.close()
    return render_template("dashboard.html", sales=sales, pending=pending,
                           low=low, recent=recent, vlabel=variant_label)


# -------------------------------------------------------------- products ---

@app.route("/products")
@login_required
def products():
    conn = db.get_db()
    try:
        prods = conn.execute("SELECT * FROM products ORDER BY name").fetchall()
        stock = {}
        for p in prods:
            row = conn.execute(
                "SELECT COALESCE(SUM(stock),0) AS s FROM variants WHERE product_id = ?",
                (p["id"],)).fetchone()
            stock[p["id"]] = row["s"]
    finally:
        conn.close()
    return render_template("products.html", products=prods, stock=stock)


@app.route("/products/new", methods=["GET", "POST"])
@login_required
def product_new():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        category = request.form.get("category", "").strip()
        try:
            price = int(request.form.get("price", "0") or 0)
            threshold = int(request.form.get("threshold", "5") or 5)
        except ValueError:
            price, threshold = 0, 5
        if not name:
            flash(t("product_name") + "?", "error")
        else:
            conn = db.get_db()
            try:
                cur = conn.execute(
                    "INSERT INTO products (name, category, price, low_stock_threshold)"
                    " VALUES (?,?,?,?)", (name, category, max(price, 0), max(threshold, 0)))
                conn.commit()
                pid = cur.lastrowid
            finally:
                conn.close()
            flash(t("product_added"), "ok")
            return redirect(url_for("product_detail", product_id=pid))
    return render_template("product_form.html")


@app.route("/products/<int:product_id>")
@login_required
def product_detail(product_id):
    conn = db.get_db()
    try:
        product = conn.execute("SELECT * FROM products WHERE id = ?",
                               (product_id,)).fetchone()
        if product is None:
            return redirect(url_for("products"))
        variants = conn.execute("SELECT * FROM variants WHERE product_id = ? ORDER BY id",
                                (product_id,)).fetchall()
    finally:
        conn.close()
    return render_template("product_detail.html", product=product,
                           variants=variants, vlabel=variant_label)


@app.route("/products/<int:product_id>/variants/add", methods=["POST"])
@login_required
def variant_add(product_id):
    size = request.form.get("size", "").strip()
    color = request.form.get("color", "").strip()
    try:
        stock = int(request.form.get("stock", "0") or 0)
    except ValueError:
        stock = 0
    conn = db.get_db()
    try:
        conn.execute("INSERT INTO variants (product_id, size, color, stock)"
                     " VALUES (?,?,?,?)", (product_id, size, color, max(stock, 0)))
        conn.commit()
    finally:
        conn.close()
    flash(t("variant_added"), "ok")
    return redirect(url_for("product_detail", product_id=product_id))


@app.route("/variants/<int:variant_id>/stock", methods=["POST"])
@login_required
def variant_stock(variant_id):
    try:
        stock = int(request.form.get("stock", "0") or 0)
    except ValueError:
        stock = 0
    conn = db.get_db()
    try:
        row = conn.execute("SELECT product_id FROM variants WHERE id = ?",
                           (variant_id,)).fetchone()
        if row:
            conn.execute("UPDATE variants SET stock = ? WHERE id = ?",
                         (max(stock, 0), variant_id))
            conn.commit()
            flash(t("stock_updated"), "ok")
            pid = row["product_id"]
        else:
            pid = None
    finally:
        conn.close()
    return redirect(url_for("product_detail", product_id=pid) if pid else url_for("products"))


@app.route("/products/<int:product_id>/delete", methods=["POST"])
@login_required
def product_delete(product_id):
    conn = db.get_db()
    try:
        conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("products"))


# ---------------------------------------------------------------- orders ---

@app.route("/orders")
@login_required
def orders():
    conn = db.get_db()
    try:
        orders = conn.execute("SELECT * FROM orders ORDER BY id DESC").fetchall()
    finally:
        conn.close()
    return render_template("orders.html", orders=orders)


@app.route("/orders/new", methods=["GET", "POST"])
@login_required
def order_new():
    conn = db.get_db()
    try:
        variants = conn.execute(
            """SELECT v.id, v.size, v.color, v.stock, p.name, p.price
               FROM variants v JOIN products p ON p.id = v.product_id
               ORDER BY p.name, v.id"""
        ).fetchall()
    finally:
        conn.close()

    if request.method == "POST":
        name = request.form.get("customer_name", "").strip()
        phone = re.sub(r"\D", "", request.form.get("phone", ""))
        address = request.form.get("address", "").strip()
        note = request.form.get("note", "").strip()
        pay_method = request.form.get("payment_method", "cod")
        pay_status = request.form.get("payment_status", "unpaid")

        if not name or not address or not phone:
            flash(t("name_address_required"), "error")
            return render_template("order_form.html", variants=variants, vlabel=variant_label)
        if not re.fullmatch(r"\d{11}", phone):
            flash(t("invalid_phone") + " " + t("phone_hint"), "error")
            return render_template("order_form.html", variants=variants, vlabel=variant_label)
        if pay_method not in PAY_METHODS:
            pay_method = "cod"
        if pay_status not in PAY_STATUSES:
            pay_status = "unpaid"

        # Discount: none | flat taka amount | percent (0-100).
        disc_type = request.form.get("discount_type", "none")
        if disc_type not in ("flat", "percent"):
            disc_type = ""
        try:
            disc_value = max(0, int(request.form.get("discount_value", "0") or 0))
        except ValueError:
            disc_value = 0
        if disc_type == "percent":
            disc_value = min(disc_value, 100)
        if disc_type == "" or disc_value == 0:
            disc_type, disc_value = "", 0

        # Delivery fee (e.g. Steadfast's location-based charge), added on top.
        try:
            delivery_fee = max(0, int(request.form.get("delivery_fee", "0") or 0))
        except ValueError:
            delivery_fee = 0

        # Collect chosen items: form fields look like qty_<variant_id>.
        chosen = []
        for v in variants:
            try:
                qty = int(request.form.get(f"qty_{v['id']}", "0") or 0)
            except ValueError:
                qty = 0
            if qty > 0:
                chosen.append((v, qty))
        if not chosen:
            flash(t("choose_at_least_one_item"), "error")
            return render_template("order_form.html", variants=variants, vlabel=variant_label)

        # Stock check first, so we never create a half-reserved order.
        for v, qty in chosen:
            if v["stock"] < qty:
                flash(f"{t('not_enough_stock')}: {v['name']} ({variant_label(v)})", "error")
                return render_template("order_form.html", variants=variants, vlabel=variant_label)

        subtotal = sum(v["price"] * qty for v, qty in chosen)
        if disc_type == "flat":
            disc_amount = min(disc_value, subtotal)
        elif disc_type == "percent":
            disc_amount = subtotal * disc_value // 100
        else:
            disc_amount = 0
        total = subtotal - disc_amount + delivery_fee
        invoice_no = next_invoice_no()

        conn = db.get_db()
        try:
            cur = conn.execute(
                """INSERT INTO orders (invoice_no, customer_name, phone, address,
                                      payment_method, payment_status, total,
                                      subtotal, discount_type, discount_value,
                                      delivery_fee, note)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (invoice_no, name, phone, address, pay_method, pay_status, total,
                 subtotal, disc_type, disc_value, delivery_fee, note))
            order_id = cur.lastrowid
            for v, qty in chosen:
                conn.execute(
                    """INSERT INTO order_items
                       (order_id, variant_id, product_name, variant_label, qty, unit_price)
                       VALUES (?,?,?,?,?,?)""",
                    (order_id, v["id"], v["name"], variant_label(v), qty, v["price"]))
                conn.execute("UPDATE variants SET stock = stock - ? WHERE id = ?",
                             (qty, v["id"]))
            conn.commit()
        finally:
            conn.close()

        flash(f"{t('order_created')} {invoice_no}", "ok")
        return redirect(url_for("order_detail", order_id=order_id))

    return render_template("order_form.html", variants=variants, vlabel=variant_label)


@app.route("/orders/<int:order_id>")
@login_required
def order_detail(order_id):
    conn = db.get_db()
    try:
        order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if order is None:
            return redirect(url_for("orders"))
        items = conn.execute("SELECT * FROM order_items WHERE order_id = ?",
                             (order_id,)).fetchall()
    finally:
        conn.close()
    return render_template("order_detail.html", order=order, items=items)


@app.route("/orders/<int:order_id>/status", methods=["POST"])
@login_required
def order_status(order_id):
    new_status = request.form.get("status", "")
    if new_status not in STATUSES:
        return redirect(url_for("order_detail", order_id=order_id))
    conn = db.get_db()
    try:
        order = conn.execute("SELECT status FROM orders WHERE id = ?",
                             (order_id,)).fetchone()
        if order is None:
            return redirect(url_for("orders"))
        old = order["status"]
        if old != new_status:
            if new_status == "cancelled":
                # Cancel: give the reserved stock back.
                items = conn.execute(
                    "SELECT variant_id, qty FROM order_items WHERE order_id = ?",
                    (order_id,)).fetchall()
                for it in items:
                    if it["variant_id"]:
                        conn.execute("UPDATE variants SET stock = stock + ? WHERE id = ?",
                                     (it["qty"], it["variant_id"]))
                flash(t("order_cancelled_stock_restored"), "ok")
            elif old == "cancelled":
                # Re-opening a cancelled order: reserve stock again if available.
                items = conn.execute(
                    "SELECT variant_id, qty FROM order_items WHERE order_id = ?",
                    (order_id,)).fetchall()
                for it in items:
                    if it["variant_id"]:
                        row = conn.execute("SELECT stock FROM variants WHERE id = ?",
                                           (it["variant_id"],)).fetchone()
                        if row is None or row["stock"] < it["qty"]:
                            conn.rollback()
                            flash(t("not_enough_stock"), "error")
                            return redirect(url_for("order_detail", order_id=order_id))
                for it in items:
                    if it["variant_id"]:
                        conn.execute("UPDATE variants SET stock = stock - ? WHERE id = ?",
                                     (it["qty"], it["variant_id"]))
                flash(t("status_updated"), "ok")
            else:
                flash(t("status_updated"), "ok")
            conn.execute("UPDATE orders SET status = ? WHERE id = ?",
                         (new_status, order_id))
            conn.commit()
    finally:
        conn.close()
    return redirect(url_for("order_detail", order_id=order_id))


@app.route("/orders/<int:order_id>/paystatus", methods=["POST"])
@login_required
def order_paystatus(order_id):
    new = request.form.get("payment_status", "")
    if new in PAY_STATUSES:
        conn = db.get_db()
        try:
            conn.execute("UPDATE orders SET payment_status = ? WHERE id = ?",
                         (new, order_id))
            conn.commit()
        finally:
            conn.close()
        flash(t("status_updated"), "ok")
    return redirect(url_for("order_detail", order_id=order_id))


# --------------------------------------------------------------- invoice ---

@app.route("/orders/<int:order_id>/invoice")
@login_required
def invoice(order_id):
    conn = db.get_db()
    try:
        order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if order is None:
            return redirect(url_for("orders"))
        items = conn.execute("SELECT * FROM order_items WHERE order_id = ?",
                             (order_id,)).fetchall()
    finally:
        conn.close()
    return render_template("invoice.html", order=order, items=items)


# ------------------------------------------------------------- steadfast ---

@app.route("/orders/<int:order_id>/steadfast", methods=["POST"])
@login_required
def order_steadfast(order_id):
    conn = db.get_db()
    try:
        order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        items = conn.execute("SELECT * FROM order_items WHERE order_id = ?",
                             (order_id,)).fetchall() if order else []
    finally:
        conn.close()
    if order is None:
        return redirect(url_for("orders"))

    api_key = db.get_setting("steadfast_api_key")
    secret = db.get_setting("steadfast_secret")

    invoice = re.sub(r"[^A-Za-z0-9_-]", "-", order["invoice_no"])
    # COD amount: only collect cash on delivery; prepaid orders go as 0.
    cod_amount = order["total"] if order["payment_method"] == "cod" else 0
    item_desc = ", ".join(f"{it['product_name']} x{it['qty']}" for it in items)[:200]
    total_lot = sum(it["qty"] for it in items)

    try:
        consignment = steadfast.create_order(
            api_key, secret,
            invoice=invoice,
            recipient_name=order["customer_name"],
            recipient_phone=order["phone"],
            recipient_address=order["address"],
            cod_amount=cod_amount,
            note=f"Lumos Closet {order['invoice_no']}",
            item_description=item_desc,
            total_lot=total_lot,
        )
    except steadfast.SteadfastError as exc:
        flash(f"{t('steadfast_error')}: {exc}", "error")
        return redirect(url_for("order_detail", order_id=order_id))

    conn = db.get_db()
    try:
        conn.execute(
            "UPDATE orders SET steadfast_tracking_code = ?, steadfast_consignment_id = ?"
            " WHERE id = ?",
            (consignment["tracking_code"], consignment.get("consignment_id"), order_id))
        conn.commit()
    finally:
        conn.close()
    flash(f"{t('steadfast_sent')} {t('tracking_code')}: {consignment['tracking_code']}", "ok")
    return redirect(url_for("order_detail", order_id=order_id))


@app.route("/orders/<int:order_id>/steadfast-status", methods=["POST"])
@login_required
def order_steadfast_status(order_id):
    conn = db.get_db()
    try:
        order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    finally:
        conn.close()
    if order is None or not order["steadfast_tracking_code"]:
        return redirect(url_for("orders"))
    try:
        status = steadfast.get_status_by_tracking_code(
            db.get_setting("steadfast_api_key"),
            db.get_setting("steadfast_secret"),
            order["steadfast_tracking_code"])
        flash(f"{t('delivery_status')}: {status}", "ok")
    except steadfast.SteadfastError as exc:
        flash(f"{t('steadfast_error')}: {exc}", "error")
    return redirect(url_for("order_detail", order_id=order_id))


# --------------------------------------------------------------- reports ---

@app.route("/reports")
@login_required
def reports():
    view = request.args.get("view", "daily")
    if view not in ("daily", "monthly"):
        view = "daily"
    if view == "daily":
        day = request.args.get("date") or date.today().isoformat()
        try:
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            day = date.today().isoformat()
        where, params, label = "date(created_at) = ?", (day,), day
    else:
        month = request.args.get("month") or date.today().strftime("%Y-%m")
        try:
            datetime.strptime(month, "%Y-%m")
        except ValueError:
            month = date.today().strftime("%Y-%m")
        where, params, label = "strftime('%Y-%m', created_at) = ?", (month,), month

    conn = db.get_db()
    try:
        # Sales count only live (non-cancelled) orders.
        sales = conn.execute(
            f"SELECT COALESCE(SUM(total),0) AS s, COUNT(*) AS c FROM orders"
            f" WHERE {where} AND status != 'cancelled'", params).fetchone()
        by_pay = conn.execute(
            f"SELECT payment_method, COALESCE(SUM(total),0) AS s, COUNT(*) AS c"
            f" FROM orders WHERE {where} AND status != 'cancelled'"
            f" GROUP BY payment_method ORDER BY s DESC", params).fetchall()
        orders = conn.execute(
            f"SELECT * FROM orders WHERE {where} ORDER BY id DESC", params).fetchall()
        inv_value = conn.execute(
            """SELECT COALESCE(SUM(p.price * v.stock), 0) AS v
               FROM variants v JOIN products p ON p.id = v.product_id"""
        ).fetchone()["v"]
    finally:
        conn.close()
    return render_template("reports.html", view=view, label=label,
                           sales=sales, by_pay=by_pay, orders=orders,
                           inv_value=inv_value)


# -------------------------------------------------------------- settings ---

@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        db.set_setting("steadfast_api_key", request.form.get("api_key", "").strip())
        db.set_setting("steadfast_secret", request.form.get("secret", "").strip())
        flash(t("keys_saved"), "ok")
        return redirect(url_for("settings"))
    return render_template(
        "settings.html",
        api_key_set=bool(db.get_setting("steadfast_api_key")),
        secret_set=bool(db.get_setting("steadfast_secret")),
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
