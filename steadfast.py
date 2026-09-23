"""Thin client for the Steadfast Courier merchant API.

Verified against the official Steadfast API docs (V1) and the
steadfast-courier SDK:
  - Base URL : https://portal.packzy.com/api/v1
  - Auth     : "Api-Key" and "Secret-Key" request headers
  - Create   : POST /create_order  (application/json)
  - Status   : GET  /status_by_trackingcode/{tracking_code}

The API key / secret are passed in by the caller (they live in our
settings table, server-side only) and are never exposed to the browser.
"""
import re

import requests

BASE_URL = "https://portal.packzy.com/api/v1"
_TIMEOUT = 20  # seconds


class SteadfastError(Exception):
    """Raised for any Steadfast API or validation problem, with a human message."""


def _headers(api_key, secret_key):
    return {
        "Api-Key": api_key,
        "Secret-Key": secret_key,
        "Content-Type": "application/json",
    }


def validate_payload(invoice, recipient_name, recipient_phone, recipient_address,
                     cod_amount):
    """Mirror Steadfast's own validation rules so we fail fast with clear messages."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", invoice or ""):
        raise SteadfastError("Invoice must be unique and use only letters, numbers, hyphens or underscores.")
    if not recipient_name or len(recipient_name) > 100:
        raise SteadfastError("Recipient name is required (max 100 characters).")
    if not re.fullmatch(r"\d{11}", recipient_phone or ""):
        raise SteadfastError("Recipient phone must be exactly 11 digits.")
    if not recipient_address or len(recipient_address) > 250:
        raise SteadfastError("Recipient address is required (max 250 characters).")
    try:
        amount = float(cod_amount)
    except (TypeError, ValueError):
        raise SteadfastError("COD amount must be a number.")
    if amount < 0:
        raise SteadfastError("COD amount cannot be negative.")
    return int(amount)


def create_order(api_key, secret_key, *, invoice, recipient_name,
                 recipient_phone, recipient_address, cod_amount, note="",
                 item_description="", total_lot=1, delivery_type=0):
    """Post one consignment to Steadfast.

    Returns the consignment dict (consignment_id, tracking_code, status...).
    Raises SteadfastError with a readable message on any failure.
    """
    if not api_key or not secret_key:
        raise SteadfastError("Steadfast API key / secret are not configured. Add them in Settings.")
    cod_amount = validate_payload(invoice, recipient_name, recipient_phone,
                                  recipient_address, cod_amount)

    payload = {
        "invoice": invoice,
        "recipient_name": recipient_name,
        "recipient_phone": recipient_phone,
        "recipient_address": recipient_address,
        "cod_amount": cod_amount,
        "note": note or "",
        "item_description": item_description or "",
        "total_lot": total_lot,
        "delivery_type": delivery_type,  # 0 = home delivery, 1 = point delivery
    }
    try:
        resp = requests.post(BASE_URL + "/create_order",
                             headers=_headers(api_key, secret_key),
                             json=payload, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise SteadfastError(f"Could not reach Steadfast: {exc}")

    try:
        data = resp.json()
    except ValueError:
        raise SteadfastError(f"Steadfast returned an unexpected response (HTTP {resp.status_code}).")

    if resp.status_code != 200 or data.get("status") != 200:
        # Steadfast puts the reason in "message".
        raise SteadfastError(str(data.get("message") or f"Steadfast error (HTTP {resp.status_code})."))

    consignment = data.get("consignment") or {}
    if not consignment.get("tracking_code"):
        raise SteadfastError("Steadfast did not return a tracking code.")
    return consignment


def get_status_by_tracking_code(api_key, secret_key, tracking_code):
    """Return the delivery_status string for a tracking code (e.g. 'in_review',
    'pending', 'delivered', 'cancelled', 'hold')."""
    if not api_key or not secret_key:
        raise SteadfastError("Steadfast API key / secret are not configured. Add them in Settings.")
    try:
        resp = requests.get(BASE_URL + f"/status_by_trackingcode/{tracking_code}",
                            headers=_headers(api_key, secret_key), timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise SteadfastError(f"Could not reach Steadfast: {exc}")
    try:
        data = resp.json()
    except ValueError:
        raise SteadfastError(f"Steadfast returned an unexpected response (HTTP {resp.status_code}).")
    if resp.status_code != 200 or data.get("status") != 200:
        raise SteadfastError(str(data.get("message") or f"Steadfast error (HTTP {resp.status_code})."))
    return str(data.get("delivery_status") or "unknown")
