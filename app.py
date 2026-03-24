"""
CallFlow AI — Flask backend for the AI receptionist service.

Run with:
    flask run            (development)
    flask run --debug    (auto-reload on file changes)

Endpoints:
    POST /incoming-call    — Webhook: receives call data, triggers Bland AI
    POST /bland-webhook    — Webhook: Bland AI posts here when a call ends
    GET  /available-slots  — Returns the next 3 open appointment windows
    GET  /dashboard        — JSON summary of today's call activity
    GET  /demo             — Runs the full mock flow end-to-end
    GET  /health           — Status check
"""

import os
from datetime import datetime

from dotenv import load_dotenv
from flask import Flask, jsonify, request

import bland_ai
import servicetitan_client as st

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
load_dotenv()  # read .env into os.environ

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-fallback-key")

# ---------------------------------------------------------------------------
# In-memory call log (replaced by a database in production)
# ---------------------------------------------------------------------------
# Each entry: {caller_name, phone_number, service_type, zip_code,
#              timestamp, status ("booked" | "missed"), booking, bland_call_id}
call_log: list[dict] = []


# ---------------------------------------------------------------------------
# POST /incoming-call — Webhook for incoming calls
# ---------------------------------------------------------------------------
@app.route("/incoming-call", methods=["POST"])
def incoming_call():
    """
    Receives a POST with incoming call data, then:
      1. Logs the call
      2. Triggers a Bland AI callback so Aria can qualify the lead
      3. Returns the Bland AI call ID for tracking

    Expected JSON body:
        {
            "caller_name":   "Jane Doe",
            "phone_number":  "555-123-4567",
            "service_type":  "HVAC Repair",
            "zip_code":      "37209"
        }
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    # --- Validate required fields -------------------------------------------
    required = ["caller_name", "phone_number", "service_type", "zip_code"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    caller_name = data["caller_name"]
    phone_number = data["phone_number"]
    service_type = data["service_type"]
    zip_code = data["zip_code"]

    # --- Trigger Bland AI to call the customer back -------------------------
    bland_result = bland_ai.handle_inbound_call(
        caller_name=caller_name,
        phone_number=phone_number,
        service_type=service_type,
        zip_code=zip_code,
    )

    # --- Log the call as "in_progress" — it will be updated when Bland AI
    #     posts back to /bland-webhook with the outcome ---------------------
    call_log.append({
        "caller_name": caller_name,
        "phone_number": phone_number,
        "service_type": service_type,
        "zip_code": zip_code,
        "timestamp": datetime.now().isoformat(),
        "status": "in_progress",
        "booking": None,
        "bland_call_id": bland_result.get("call_id"),
    })

    return jsonify({
        "status": "call_initiated",
        "message": "Aria is calling the customer back now.",
        "bland_ai": bland_result,
    }), 201


# ---------------------------------------------------------------------------
# POST /bland-webhook — Bland AI calls this when a voice call ends
# ---------------------------------------------------------------------------
@app.route("/bland-webhook", methods=["POST"])
def bland_webhook():
    """
    Webhook endpoint that Bland AI POSTs to when a call completes.

    Bland AI sends the full transcript, extracted variables (name, address,
    appointment time), and call metadata. We parse that data and — if the
    caller booked — create the appointment in ServiceTitan automatically.

    This is the bridge between the voice conversation and the booking system.
    """
    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"error": "Request body must be JSON"}), 400

    # --- Parse the Bland AI webhook payload ---------------------------------
    call_data = bland_ai.parse_webhook_payload(payload)

    # --- Find the matching call_log entry so we can update it ---------------
    call_id = call_data.get("call_id")
    log_entry = next(
        (c for c in call_log if c.get("bland_call_id") == call_id),
        None,
    )

    # --- If the caller booked, create the job in ServiceTitan ---------------
    if call_data["outcome"] == "booked":
        booking = st.create_booking(
            caller_name=call_data["caller_name"],
            phone_number=call_data["phone_number"],
            service_type=call_data["service_type"],
            zip_code=call_data["zip_code"],
            appointment_time=call_data.get("appointment_time"),
        )

        # Update the in-memory log entry
        if log_entry:
            log_entry["status"] = "booked"
            log_entry["booking"] = booking
        else:
            # Webhook arrived before /incoming-call (race), or standalone test
            call_log.append({
                "caller_name": call_data["caller_name"],
                "phone_number": call_data["phone_number"],
                "service_type": call_data["service_type"],
                "zip_code": call_data["zip_code"],
                "timestamp": datetime.now().isoformat(),
                "status": "booked",
                "booking": booking,
                "bland_call_id": call_id,
            })

        return jsonify({
            "status": "booked",
            "booking": booking,
            "call_summary": call_data,
        }), 200

    # --- Not booked (caller declined or no answer) --------------------------
    status = "missed" if call_data["outcome"] == "no_answer" else "not_interested"
    if log_entry:
        log_entry["status"] = status

    return jsonify({
        "status": status,
        "call_summary": call_data,
    }), 200


# ---------------------------------------------------------------------------
# GET /demo — Run the full mock flow end-to-end in one request
# ---------------------------------------------------------------------------
@app.route("/demo", methods=["GET"])
def demo():
    """
    Simulates the complete CallFlow AI pipeline in a single request:

        1. Incoming call received from Jane Doe (HVAC Repair, ZIP 37209)
        2. Bland AI call triggered — Aria calls the customer back
        3. Aria qualifies the lead, confirms the address, picks a slot
        4. Call ends — webhook fires with transcript + extracted data
        5. Appointment booked in ServiceTitan
        6. Returns a clean JSON summary of every step

    All orchestration lives in bland_ai.run_mock_demo() so the same
    flow can be triggered from tests or CLI scripts without Flask.
    """
    # run_mock_demo() handles the full pipeline: incoming call → Bland AI
    # callback → voice conversation → ServiceTitan booking — and returns
    # a single dict summarising every step.
    result = bland_ai.run_mock_demo()

    # Log the demo call so it shows up on the /dashboard endpoint
    step4 = result["steps"]["4_appointment_booked"]
    step1 = result["steps"]["1_call_received"]
    call_log.append({
        "caller_name": step1["caller"]["caller_name"],
        "phone_number": step1["caller"]["phone_number"],
        "service_type": step1["caller"]["service_type"],
        "zip_code": step1["caller"]["zip_code"],
        "timestamp": step1["timestamp"],
        "status": "booked",
        "booking": step4["servicetitan_booking"],
        "bland_call_id": result["steps"]["2_bland_ai_triggered"]["call_id"],
    })

    return jsonify(result), 200


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
# Health check
# ---------------------------------------------------------------------------
@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "mock_mode": os.getenv("MOCK_MODE", "false").lower() == "true",
        "bland_ai_configured": bool(os.getenv("BLAND_AI_API_KEY")),
    }), 200


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(debug=True, host="0.0.0.0", port=port)
