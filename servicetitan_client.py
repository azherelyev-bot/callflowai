"""
ServiceTitan API client — handles OAuth 2.0 and all API calls.

When MOCK_MODE is enabled, every method returns realistic fake data
so you can demo the full flow without live ServiceTitan credentials.
"""

import os
import time
import uuid
from datetime import datetime, timedelta

import requests


# ---------------------------------------------------------------------------
# OAuth token cache (module-level so it persists across requests)
# ---------------------------------------------------------------------------
_token_cache = {
    "access_token": None,
    "expires_at": 0,          # Unix timestamp when the token expires
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_mock() -> bool:
    """Return True when the app is running in demo / mock mode."""
    return os.getenv("MOCK_MODE", "false").lower() == "true"


def _base_url() -> str:
    return "https://api.servicetitan.io"


def _auth_url() -> str:
    return "https://auth.servicetitan.io/connect/token"


def _headers() -> dict:
    """Build request headers with a valid Bearer token."""
    token = get_access_token()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "ST-App-Key": os.getenv("SERVICETITAN_CLIENT_ID", ""),
    }


# ---------------------------------------------------------------------------
# OAuth 2.0 — client-credentials flow
# ---------------------------------------------------------------------------

def get_access_token() -> str:
    """
    Return a valid access token, refreshing it if expired.

    ServiceTitan uses a standard OAuth 2.0 client-credentials grant:
      POST https://auth.servicetitan.io/connect/token
      grant_type=client_credentials
      client_id=...
      client_secret=...

    The token is cached in memory and reused until it expires.
    """

    if _is_mock():
        return "mock-access-token"

    # Re-use the cached token if it hasn't expired yet (with 60 s buffer)
    if _token_cache["access_token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["access_token"]

    # Request a fresh token
    resp = requests.post(
        _auth_url(),
        data={
            "grant_type": "client_credentials",
            "client_id": os.getenv("SERVICETITAN_CLIENT_ID"),
            "client_secret": os.getenv("SERVICETITAN_CLIENT_SECRET"),
        },
        timeout=15,
    )
    resp.raise_for_status()
    body = resp.json()

    _token_cache["access_token"] = body["access_token"]
    _token_cache["expires_at"] = time.time() + body.get("expires_in", 3600)

    return _token_cache["access_token"]


# ---------------------------------------------------------------------------
# Booking API — create a new job in ServiceTitan
# ---------------------------------------------------------------------------

def create_booking(caller_name: str, phone_number: str,
                   service_type: str, zip_code: str,
                   appointment_time: str | None = None) -> dict:
    """
    Create a new job booking via ServiceTitan's Booking API.

    Parameters
    ----------
    caller_name     : Full name of the homeowner
    phone_number    : Their phone number
    service_type    : E.g. "HVAC Repair", "Plumbing", "Electrical"
    zip_code        : Service location ZIP
    appointment_time: ISO-8601 datetime string for the slot (optional)

    Returns a dict with the booking confirmation from ServiceTitan.
    """

    if _is_mock():
        booking_id = str(uuid.uuid4())[:8].upper()
        slot = appointment_time or (datetime.now() + timedelta(hours=4)).isoformat()
        return {
            "id": booking_id,
            "status": "Scheduled",
            "customer": caller_name,
            "phone": phone_number,
            "service_type": service_type,
            "zip_code": zip_code,
            "appointment_time": slot,
            "technician": "Mike R.",
            "confirmation_number": f"ST-{booking_id}",
        }

    tenant = os.getenv("SERVICETITAN_TENANT_ID")
    payload = {
        "name": caller_name,
        "contacts": [{"type": "Phone", "value": phone_number}],
        "address": {"zip": zip_code},
        "jobType": service_type,
        "start": appointment_time,
        "summary": f"AI-booked: {service_type} for {caller_name}",
    }

    resp = requests.post(
        f"{_base_url()}/booking/v2/tenant/{tenant}/bookings",
        headers=_headers(),
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Scheduling API — fetch available appointment slots
# ---------------------------------------------------------------------------

def get_available_slots(zip_code: str, service_type: str,
                        limit: int = 3) -> list[dict]:
    """
    Query ServiceTitan's scheduling/availability API and return
    the next *limit* open appointment windows.

    Each slot dict contains: start, end, technician_name.
    """

    if _is_mock():
        # Generate realistic fake slots starting from the next whole hour
        now = datetime.now().replace(minute=0, second=0, microsecond=0)
        slots = []
        offset_hours = 2  # first slot is ~2 hours from now
        for i in range(limit):
            start = now + timedelta(hours=offset_hours + i * 3)
            end = start + timedelta(hours=2)
            slots.append({
                "start": start.isoformat(),
                "end": end.isoformat(),
                "technician": ["Mike R.", "Sarah L.", "James T."][i % 3],
            })
        return slots

    tenant = os.getenv("SERVICETITAN_TENANT_ID")
    params = {
        "zip": zip_code,
        "jobType": service_type,
        "startsOnOrAfter": datetime.utcnow().isoformat() + "Z",
        "pageSize": limit,
    }

    resp = requests.get(
        f"{_base_url()}/dispatch/v2/tenant/{tenant}/capacity/availability",
        headers=_headers(),
        params=params,
        timeout=15,
    )
    resp.raise_for_status()

    # Normalize the response into our simplified slot format
    data = resp.json().get("data", [])
    return [
        {
            "start": slot["start"],
            "end": slot["end"],
            "technician": slot.get("technicianName", "Unassigned"),
        }
        for slot in data[:limit]
    ]
