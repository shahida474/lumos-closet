# Lumos Closet — Standalone Shop Manager

A self-hostable web app for running a small clothing business (inventory,
orders, printable invoices, daily/monthly audit reports, Steadfast Courier
integration). Bilingual UI — **Bangla (default) and English** — all prices in
Bangladeshi Taka (৳).

Stack: Python 3 + Flask + SQLite (one `.db` file). No frontend build step, no
external services required.

## Run locally

```bash
cd lumos-closet-standalone
pip install -r requirements.txt
APP_PASSWORD=choose-a-strong-password python app.py
```

Open http://127.0.0.1:5000 and log in. The database (`lumos.db`) is created
automatically on first run and starts **empty** — add your real products.

Environment variables:

| Variable       | Purpose                                              | Default        |
|----------------|------------------------------------------------------|----------------|
| `APP_PASSWORD` | Login password (set this!)                           | `changeme`     |
| `SECRET_KEY`   | Flask session secret (set a long random value in prod)| random per run |
| `DB_PATH`      | Where the SQLite file lives                          | `./lumos.db`   |
| `PORT`         | Port to listen on                                    | `5000`         |

## Deploy with Docker (Render / Railway / any VPS)

The included `Dockerfile` builds a production image (gunicorn). It expects the
SQLite file at `/data/lumos.db`, so **mount a persistent volume on `/data`** —
otherwise orders and inventory vanish on every restart/redeploy.

```bash
docker build -t lumos-closet .
docker run -d --name lumos \
  -p 8000:8000 \
  -v lumos-data:/data \
  -e APP_PASSWORD=choose-a-strong-password \
  -e SECRET_KEY=$(openssl rand -hex 32) \
  lumos-closet
```

### Render (render.com)
1. Push this folder to a Git repo (GitHub/GitLab).
2. Render dashboard → **New → Web Service** → connect the repo.
3. Choose **Docker** as the runtime (it will use the `Dockerfile`).
4. Add a **Disk**: name `lumos-data`, mount path `/data` (1 GB is plenty).
5. Environment variables: `APP_PASSWORD`, `SECRET_KEY` (generate a random value).
6. Deploy. Your app is live at the `onrender.com` URL Render gives you.

### Railway (railway.app)
1. Push this folder to a Git repo.
2. Railway → **New Project → Deploy from Repo**.
3. Add a **Volume** mounted at `/data`.
4. Variables tab: set `APP_PASSWORD` and `SECRET_KEY`.
5. Railway builds the `Dockerfile` automatically. Add a public domain under
   **Settings → Networking** to get your URL.

### Generic VPS (Docker)
Same `docker run` command as above. Put it behind Caddy or nginx with HTTPS,
e.g. with Caddy:

```
your-domain.com {
    reverse_proxy 127.0.0.1:8000
}
```

Backups: the whole business is one file — copy `/data/lumos.db`
(`docker cp lumos:/data/lumos.db ./backup.db`) on a schedule.

## Steadfast Courier setup

1. Log in to your Steadfast merchant panel (portal.packzy.com) → API settings,
   copy your **API Key** and **Secret Key**.
2. In the app: **Settings → Steadfast** → paste both keys → Save.
   (Keys are stored server-side in the database only; they never reach the browser.)
3. On any order page: **📦 Send to Steadfast** posts the consignment
   (invoice no., customer name/phone/address, COD amount). The tracking code is
   saved on the order, and **Check delivery status** polls Steadfast for it.
   - Phone numbers must be 11 digits — the order form enforces this.
   - COD amount sent is the order total for Cash-on-Delivery orders, `0` for
     prepaid (bKash/Nagad/Rocket/Card) orders.

## Features

- Dashboard: today's sales, pending orders, low-stock alerts
- Products with size/color variants, per-variant stock, low-stock thresholds
- Orders: customer info, items, status workflow
  (Pending → Confirmed → Shipped → Delivered, plus Cancelled),
  payment method (COD / bKash / Nagad / Rocket / Card), paid/unpaid
- Stock auto-decrements on order creation, restored on cancellation
- Printable invoice per order (print-friendly page)
- Daily & monthly audit reports: total sales, order count, sales by payment
  method, current inventory value
- Bilingual UI toggle (Bangla / English), persisted in the session
- Single-password login gate for the public internet
