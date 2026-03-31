"""
CallFlow AI — Flask backend for the AI receptionist service.

Run with:
    flask run            (development)
    flask run --debug    (auto-reload on file changes)

Endpoints:
    POST /vapi/webhook     — Webhook: Vapi posts here for call events
    GET  /available-slots  — Returns the next 3 open appointment windows
    GET  /dashboard        — JSON summary of today's call activity
    GET  /demo             — Runs the full mock flow end-to-end
    GET  /health           — Status check
    GET  /admin/login      — Admin login page
    POST /admin/login      — Authenticate admin
    GET  /admin/dashboard  — Admin dashboard with live stats
    GET  /admin/calls/<id> — Call detail view with transcript
    GET  /admin/logout     — End admin session
"""

import functools
import logging
import os
import uuid
from datetime import datetime, timedelta

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, session, url_for

import servicetitan_client as st

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
load_dotenv()  # read .env into os.environ

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-fallback-key")

# Session cookie lasts 24 hours
app.permanent_session_lifetime = timedelta(hours=24)

# ---------------------------------------------------------------------------
# Hardcoded admin credentials (swap for database lookup in production)
# ---------------------------------------------------------------------------
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "callflowai123"


# ---------------------------------------------------------------------------
# Auth helper — decorator that protects /admin/* routes
# ---------------------------------------------------------------------------
def login_required(view_func):
    """Redirect to /admin/login if the user is not authenticated."""
    @functools.wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("admin_login"))
        return view_func(*args, **kwargs)
    return wrapped

# ---------------------------------------------------------------------------
# In-memory call log (replaced by a database in production)
# ---------------------------------------------------------------------------
# Each entry: {caller_name, phone_number, service_type, zip_code,
#              timestamp, status ("booked" | "completed" | "missed"), booking, ...}
call_log: list[dict] = []


# ---------------------------------------------------------------------------
# POST /vapi/webhook — Vapi sends call events here
# ---------------------------------------------------------------------------
@app.route("/vapi/webhook", methods=["POST"])
def vapi_webhook():
    """
    Webhook endpoint that Vapi POSTs to for call lifecycle events.

    We handle "end-of-call-report" to extract caller info, transcript, and
    summary and save as a call log entry. All other event types are
    acknowledged with {"status": "ok"}.

    Vapi event types include:
        - assistant-request, function-call, status-update,
          end-of-call-report, hang, speech-update, transcript, etc.
    """
    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"status": "ok"}), 200

    message = payload.get("message", {})
    event_type = message.get("type", "")

    logger.info("Vapi webhook event: %s", event_type)

    if event_type == "end-of-call-report":
        _handle_end_of_call_report(message)

    return jsonify({"status": "ok"}), 200


def _handle_end_of_call_report(message: dict):
    """Parse an end-of-call-report event and save it to the call log."""
    call_obj = message.get("call", {})
    customer = call_obj.get("customer", {})

    # Caller phone number — Vapi stores it on the customer object
    phone_number = customer.get("number", "Unknown")

    # Transcript: Vapi sends a list of {role, message} turns
    raw_transcript = message.get("transcript", "")
    transcript = []
    if isinstance(raw_transcript, list):
        for turn in raw_transcript:
            transcript.append({
                "speaker": "aria" if turn.get("role") == "assistant" else "caller",
                "text": turn.get("message", turn.get("content", "")),
            })
    elif isinstance(raw_transcript, str) and raw_transcript:
        transcript.append({"speaker": "system", "text": raw_transcript})

    summary = message.get("summary", "")
    ended_reason = message.get("endedReason", "")
    recording_url = message.get("recordingUrl", "")
    duration = message.get("durationSeconds", 0)

    # Try to extract a caller name from the summary or transcript
    caller_name = customer.get("name", "Unknown Caller")

    # Determine status — if the summary mentions booking/appointment, mark booked
    status = "completed"
    if any(kw in summary.lower() for kw in ["booked", "appointment", "scheduled"]):
        status = "booked"
    elif ended_reason in ("customer-ended-call", "assistant-ended-call"):
        status = "completed"
    elif ended_reason in ("no-answer", "busy", "voicemail"):
        status = "missed"

    call_log.append({
        "call_id": uuid.uuid4().hex[:10],
        "vapi_call_id": call_obj.get("id", ""),
        "caller_name": caller_name,
        "phone_number": phone_number,
        "service_type": "",
        "zip_code": "",
        "timestamp": call_obj.get("startedAt", datetime.now().isoformat()),
        "status": status,
        "booking": None,
        "summary": summary,
        "transcript": transcript,
        "recording_url": recording_url,
        "duration_seconds": duration,
        "ended_reason": ended_reason,
    })


# ---------------------------------------------------------------------------
# GET /demo — Run the full mock flow end-to-end in one request
# ---------------------------------------------------------------------------
@app.route("/demo", methods=["GET"])
def demo():
    """
    Simulates the complete CallFlow AI pipeline in a single request.

    Creates a mock call log entry with a realistic Aria transcript so the
    admin dashboard has data to display without requiring a live Vapi call.
    """
    now = datetime.now().isoformat()

    mock_transcript = [
        {"speaker": "aria", "text": (
            "Hi, this is Aria, your CallFlowAI assistant for Nashville Comfort "
            "HVAC. I saw you called about HVAC Repair. I'd love to help get "
            "you scheduled today."
        )},
        {"speaker": "caller", "text": (
            "Oh great, yes. My furnace started making a loud rattling noise "
            "this morning and the heat isn't coming on properly."
        )},
        {"speaker": "aria", "text": (
            "I'm sorry to hear that — we'll get that taken care of for you. "
            "Can I get the address where you need the service?"
        )},
        {"speaker": "caller", "text": "Sure, it's 412 Oak Street, Nashville, 37209."},
        {"speaker": "aria", "text": (
            "Perfect, 412 Oak Street, 37209. Let me check what we have "
            "available for you... I have an opening tomorrow at 10:00 AM. "
            "Does that work?"
        )},
        {"speaker": "caller", "text": "That works perfectly."},
        {"speaker": "aria", "text": (
            "Wonderful! I've got you booked for HVAC Repair tomorrow at "
            "10:00 AM. You'll receive a confirmation shortly. Is there "
            "anything else I can help you with?"
        )},
        {"speaker": "caller", "text": "No, that's it. Thank you so much!"},
        {"speaker": "aria", "text": (
            "Great, you're all set! A technician from Nashville Comfort HVAC "
            "will be there tomorrow at 10 AM. Have a wonderful day!"
        )},
    ]

    # Force mock mode for ServiceTitan booking
    original_mock = os.environ.get("MOCK_MODE")
    os.environ["MOCK_MODE"] = "true"
    try:
        booking = st.create_booking(
            caller_name="Jane Doe",
            phone_number="555-123-4567",
            service_type="HVAC Repair",
            zip_code="37209",
            appointment_time="2025-03-25T10:00:00",
            job_description=(
                "Furnace making loud rattling noise, heat not working properly. "
                "Customer reports issue started this morning."
            ),
        )
    finally:
        if original_mock is None:
            os.environ.pop("MOCK_MODE", None)
        else:
            os.environ["MOCK_MODE"] = original_mock

    call_entry = {
        "call_id": uuid.uuid4().hex[:10],
        "caller_name": "Jane Doe",
        "phone_number": "555-123-4567",
        "service_type": "HVAC Repair",
        "zip_code": "37209",
        "timestamp": now,
        "status": "booked",
        "booking": booking,
        "summary": (
            "Jane Doe called about HVAC Repair. Furnace making loud rattling "
            "noise. Appointment booked for tomorrow at 10:00 AM."
        ),
        "transcript": mock_transcript,
        "duration_seconds": 127,
    }
    call_log.append(call_entry)

    return jsonify({
        "demo": True,
        "pipeline_summary": (
            "Complete flow: call received → Aria qualified lead → "
            "appointment booked in ServiceTitan"
        ),
        "call": call_entry,
        "booking": booking,
    }), 200


# ---------------------------------------------------------------------------
# GET /available-slots — Query open appointment windows
# ---------------------------------------------------------------------------
@app.route("/available-slots", methods=["GET"])
def available_slots():
    """
    Returns the next 3 available appointment slots for a given
    service type and ZIP code.

    Query params:
        ?zip_code=37209&service_type=HVAC+Repair
    """
    zip_code = request.args.get("zip_code", "")
    service_type = request.args.get("service_type", "")

    if not zip_code or not service_type:
        return jsonify({"error": "zip_code and service_type are required"}), 400

    slots = st.get_available_slots(zip_code, service_type, limit=3)
    return jsonify({"slots": slots}), 200


# ---------------------------------------------------------------------------
# GET /dashboard — Today's call activity summary
# ---------------------------------------------------------------------------
@app.route("/dashboard", methods=["GET"])
def dashboard():
    """
    Returns a JSON summary of today's activity:
        - total calls received
        - appointments booked
        - calls missed (no availability / not booked)
        - list of today's call records
    """
    today = datetime.now().date().isoformat()

    todays_calls = [
        c for c in call_log
        if c["timestamp"].startswith(today)
    ]
    booked = [c for c in todays_calls if c["status"] == "booked"]
    missed = [c for c in todays_calls if c["status"] in ("missed", "not_interested")]
    in_progress = [c for c in todays_calls if c["status"] == "in_progress"]

    return jsonify({
        "date": today,
        "total_calls": len(todays_calls),
        "appointments_booked": len(booked),
        "calls_missed": len(missed),
        "calls_in_progress": len(in_progress),
        "calls": todays_calls,
    }), 200


# ---------------------------------------------------------------------------
# GET/POST /admin/login — Admin authentication
# ---------------------------------------------------------------------------
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    """
    GET  — Render the login form.
    POST — Validate credentials and create a session.

    On successful login, sets session["logged_in"] = True and redirects
    to /admin/dashboard. Sessions are marked permanent so the cookie
    lasts 24 hours (configured via app.permanent_session_lifetime).
    """
    if request.method == "GET":
        # Already logged in — skip the form
        if session.get("logged_in"):
            return redirect(url_for("admin_dashboard"))
        return render_template("login.html", error=None)

    # POST — check credentials
    username = request.form.get("username", "")
    password = request.form.get("password", "")

    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        session.permanent = True  # use the 24-hour lifetime
        session["logged_in"] = True
        session["login_time"] = datetime.now().isoformat()
        return redirect(url_for("admin_dashboard"))

    return render_template("login.html", error="Invalid username or password."), 401


# ---------------------------------------------------------------------------
# GET /admin/dashboard — Protected admin dashboard
# ---------------------------------------------------------------------------
@app.route("/admin/dashboard", methods=["GET"])
@login_required
def admin_dashboard():
    """
    Renders the admin dashboard with live stats pulled from the
    in-memory call_log: total calls, appointments booked, conversion
    rate, and the full call list for today.
    """
    today = datetime.now().date().isoformat()

    todays_calls = [
        c for c in call_log if c["timestamp"].startswith(today)
    ]
    booked = [c for c in todays_calls if c["status"] == "booked"]
    missed = [c for c in todays_calls if c["status"] in ("missed", "not_interested")]
    total = len(todays_calls)
    conversion = round((len(booked) / total) * 100) if total > 0 else 0

    return render_template(
        "admin_dashboard.html",
        date=today,
        total_calls=total,
        appointments_booked=len(booked),
        calls_missed=len(missed),
        conversion_rate=conversion,
        calls=todays_calls,
    )


# ---------------------------------------------------------------------------
# GET /admin/calls/<call_id> — Call detail view
# ---------------------------------------------------------------------------
@app.route("/admin/calls/<call_id>", methods=["GET"])
@login_required
def call_detail(call_id):
    """
    Renders a detailed view for a single call, looked up by its call_id.
    Shows full customer info, booking details, job description, and the
    complete Aria conversation transcript in chat bubble format.
    """
    call = next((c for c in call_log if c.get("call_id") == call_id), None)
    if not call:
        return redirect(url_for("admin_dashboard"))
    return render_template("call_detail.html", call=call)


# ---------------------------------------------------------------------------
# GET /admin/logout — End the admin session
# ---------------------------------------------------------------------------
@app.route("/admin/logout", methods=["GET"])
def admin_logout():
    """Clear the session and redirect back to the login page."""
    session.clear()
    return redirect(url_for("admin_login"))


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "mock_mode": os.getenv("MOCK_MODE", "false").lower() == "true",
        "vapi_configured": bool(os.getenv("VAPI_API_KEY")),
    }), 200


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(debug=True, host="0.0.0.0", port=port)
