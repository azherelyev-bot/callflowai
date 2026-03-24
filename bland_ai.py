"""
Bland AI Voice Integration Module
==================================

Handles outbound AI voice calls via Bland AI's API (https://api.bland.ai).

When a customer calls in, this module triggers a Bland AI callback where
"Aria" — the AI voice agent — qualifies the lead, confirms details, and
books the appointment. Once the call ends, Bland AI fires a webhook back
to our /bland-webhook endpoint with the full transcript and extracted data.

In MOCK_MODE, every function returns a realistic simulated response so you
can demo the entire call → booking flow without live API keys.
"""

import os
import uuid
from datetime import datetime

import requests


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BLAND_API_BASE = "https://api.bland.ai/v1"

# The contractor name Aria will use when greeting callers.
# In production, pull this from a tenant/company settings table.
CONTRACTOR_NAME = "Nashville Comfort HVAC"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_mock() -> bool:
    """Return True when the app is running in demo / mock mode."""
    return os.getenv("MOCK_MODE", "false").lower() == "true"


def _bland_headers() -> dict:
    """Build auth headers for the Bland AI API."""
    return {
        "Authorization": os.getenv("BLAND_AI_API_KEY", ""),
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Conversation script — the prompt Bland AI's voice agent follows
# ---------------------------------------------------------------------------

def _build_call_script(caller_name: str, service_type: str) -> str:
    """
    Generate the conversation script (system prompt) that Bland AI's
    voice agent will follow during the call.

    The script tells Aria to:
      1. Greet the caller by name and reference their service need
      2. Confirm the service address
      3. Offer available time slots
      4. Confirm the booking and wrap up politely
    """
    return f"""You are Aria, a friendly and professional AI receptionist for {CONTRACTOR_NAME}.

GREETING:
"Hi, this is Aria, your CallFlowAI assistant for {CONTRACTOR_NAME}. \
I saw you called about {service_type}. I'd love to help get you scheduled today."

STEP 1 — CONFIRM SERVICE DETAILS:
- Confirm the type of service they need: "{service_type}"
- Ask: "Can I get the address where you need the service?"
- If they give a partial address, politely ask for the full street address and ZIP code.

STEP 2 — CHECK AVAILABILITY:
- Say: "Let me check what we have available for you..."
- Offer the next available time slot.
- If they can't make it, offer up to two alternatives.
- Always confirm day, date, and time clearly.

STEP 3 — BOOK THE APPOINTMENT:
- Once they agree to a slot, say: "Perfect, I've got you booked for \
[service_type] on [date] at [time]. You'll receive a confirmation shortly."
- Ask: "Is there anything else I can help you with?"

STEP 4 — WRAP UP:
- Say: "Great, you're all set! A technician from {CONTRACTOR_NAME} will be \
there on [date]. Have a wonderful day!"

RULES:
- Be warm, concise, and conversational — never robotic.
- If the caller asks something outside scheduling, say: "That's a great question. \
Let me have one of our team members follow up with you on that."
- Never reveal that you are an AI unless directly asked.
- Keep the call under 3 minutes.
- Extract and remember: caller name, phone number, service address, \
service type, chosen appointment time.
"""


# ---------------------------------------------------------------------------
# handle_inbound_call — Trigger a Bland AI callback to the customer
# ---------------------------------------------------------------------------

def handle_inbound_call(caller_name: str, phone_number: str,
                        service_type: str, zip_code: str) -> dict:
    """
    Initiate an outbound AI voice call via Bland AI to the customer
    who just called in. Bland AI calls them back and Aria walks them
    through booking.

    Parameters
    ----------
    caller_name   : Full name of the caller
    phone_number  : Phone number to call back (E.164 or standard US format)
    service_type  : The service they're requesting (e.g. "HVAC Repair")
    zip_code      : Their ZIP code for service area validation

    Returns a dict with the Bland AI call ID and status.
    """

    # ------------------------------------------------------------------
    # MOCK MODE — return a realistic simulated Bland AI response
    # ------------------------------------------------------------------
    if _is_mock():
        mock_call_id = f"bland-{uuid.uuid4().hex[:12]}"
        return {
            "call_id": mock_call_id,
            "status": "queued",
            "phone_number": phone_number,
            "caller_name": caller_name,
            "service_type": service_type,
            "message": "Mock call queued — Aria will call the customer back.",
            "estimated_start": "~5 seconds",
        }

    # ------------------------------------------------------------------
    # LIVE MODE — POST to Bland AI's /v1/calls endpoint
    # ------------------------------------------------------------------
    payload = {
        # The phone number Bland AI will dial
        "phone_number": phone_number,

        # The system prompt / script Aria follows
        "task": _build_call_script(caller_name, service_type),

        # Voice and model settings
        "voice": "maya",                # Bland AI voice preset
        "first_sentence": (
            f"Hi, this is Aria, your CallFlowAI assistant for "
            f"{CONTRACTOR_NAME}. I saw you called about {service_type}. "
            f"I'd love to help get you scheduled today."
        ),
        "wait_for_greeting": False,     # Aria speaks first

        # Webhook URL Bland AI will POST to when the call ends.
        # In production, replace with your public URL / ngrok tunnel.
        "webhook": os.getenv("BLAND_WEBHOOK_URL",
                             "https://your-domain.com/bland-webhook"),

        # Metadata we attach so the webhook can correlate back to our records
        "metadata": {
            "caller_name": caller_name,
            "phone_number": phone_number,
            "service_type": service_type,
            "zip_code": zip_code,
        },

        # Max call duration (seconds) — keep calls tight
        "max_duration": 180,
    }

    resp = requests.post(
        f"{BLAND_API_BASE}/calls",
        headers=_bland_headers(),
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    return {
        "call_id": data.get("call_id"),
        "status": data.get("status", "queued"),
        "phone_number": phone_number,
        "caller_name": caller_name,
        "service_type": service_type,
    }


# ---------------------------------------------------------------------------
# get_call_summary — Retrieve call outcome from Bland AI
# ---------------------------------------------------------------------------

def get_call_summary(call_id: str) -> dict:
    """
    Fetch the summary of a completed Bland AI call by its call ID.

    Returns
    -------
    dict with keys:
        caller_name      : str
        service_requested: str
        appointment_time : str (ISO-8601) or None
        outcome          : "booked" | "not_interested" | "no_answer"
        transcript       : list of {speaker, text} turns
    """

    # ------------------------------------------------------------------
    # MOCK MODE — return a full simulated transcript where Aria
    # successfully books Jane Doe for HVAC Repair on March 25 at 10 AM.
    # ------------------------------------------------------------------
    if _is_mock():
        return {
            "call_id": call_id,
            "caller_name": "Jane Doe",
            "service_requested": "HVAC Repair",
            "appointment_time": "2025-03-25T10:00:00",
            "outcome": "booked",
            "call_duration_seconds": 127,
            "transcript": [
                {
                    "speaker": "aria",
                    "text": (
                        "Hi, this is Aria, your CallFlowAI assistant for "
                        "Nashville Comfort HVAC. I saw you called about "
                        "HVAC Repair. I'd love to help get you scheduled today."
                    ),
                },
                {
                    "speaker": "caller",
                    "text": (
                        "Oh great, yes. My furnace started making a loud "
                        "rattling noise this morning and the heat isn't "
                        "coming on properly."
                    ),
                },
                {
                    "speaker": "aria",
                    "text": (
                        "I'm sorry to hear that — we'll get that taken care "
                        "of for you. Can I get the address where you need "
                        "the service?"
                    ),
                },
                {
                    "speaker": "caller",
                    "text": "Sure, it's 412 Oak Street, Nashville, 37209.",
                },
                {
                    "speaker": "aria",
                    "text": (
                        "Perfect, 412 Oak Street, 37209. Let me check what "
                        "we have available for you... I have an opening "
                        "tomorrow, March 25th, at 10:00 AM. Does that work?"
                    ),
                },
                {
                    "speaker": "caller",
                    "text": "That works perfectly.",
                },
                {
                    "speaker": "aria",
                    "text": (
                        "Wonderful! I've got you booked for HVAC Repair on "
                        "March 25th at 10:00 AM. You'll receive a "
                        "confirmation shortly. Is there anything else I can "
                        "help you with?"
                    ),
                },
                {
                    "speaker": "caller",
                    "text": "No, that's it. Thank you so much!",
                },
                {
                    "speaker": "aria",
                    "text": (
                        "Great, you're all set! A technician from Nashville "
                        "Comfort HVAC will be there tomorrow at 10 AM. "
                        "Have a wonderful day!"
                    ),
                },
            ],
        }

    # ------------------------------------------------------------------
    # LIVE MODE — GET the call record from Bland AI
    # ------------------------------------------------------------------
    resp = requests.get(
        f"{BLAND_API_BASE}/calls/{call_id}",
        headers=_bland_headers(),
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    # Bland AI returns structured "concatenated_transcript" and
    # "variables" extracted by the AI during the conversation.
    variables = data.get("variables", {})
    status = data.get("status", "")

    # Determine the outcome from the call status and extracted variables
    if variables.get("appointment_time"):
        outcome = "booked"
    elif status == "no-answer":
        outcome = "no_answer"
    else:
        outcome = "not_interested"

    return {
        "call_id": call_id,
        "caller_name": variables.get("caller_name", "Unknown"),
        "service_requested": variables.get("service_type", "Unknown"),
        "appointment_time": variables.get("appointment_time"),
        "outcome": outcome,
        "call_duration_seconds": data.get("call_length"),
        "transcript": data.get("transcripts", []),
    }


# ---------------------------------------------------------------------------
# parse_webhook_payload — Extract structured data from Bland AI's webhook
# ---------------------------------------------------------------------------

def parse_webhook_payload(payload: dict) -> dict:
    """
    Parse the POST body that Bland AI sends to our /bland-webhook
    endpoint when a call completes.

    Extracts the fields we need to create a ServiceTitan booking:
        caller_name, phone_number, service_type, zip_code,
        appointment_time, address, outcome

    In mock mode, returns a complete pre-filled result.
    """

    if _is_mock():
        return {
            "caller_name": "Jane Doe",
            "phone_number": "555-123-4567",
            "service_type": "HVAC Repair",
            "zip_code": "37209",
            "address": "412 Oak Street, Nashville, TN 37209",
            "appointment_time": "2025-03-25T10:00:00",
            "outcome": "booked",
            "call_id": payload.get("call_id", "bland-mock-id"),
            "call_duration_seconds": 127,
        }

    # In live mode, Bland AI sends metadata we attached + extracted variables
    metadata = payload.get("metadata", {})
    variables = payload.get("variables", {})
    status = payload.get("status", "")

    if variables.get("appointment_time"):
        outcome = "booked"
    elif status == "no-answer":
        outcome = "no_answer"
    else:
        outcome = "not_interested"

    return {
        "caller_name": metadata.get("caller_name",
                                    variables.get("caller_name", "Unknown")),
        "phone_number": metadata.get("phone_number", ""),
        "service_type": metadata.get("service_type",
                                     variables.get("service_type", "Unknown")),
        "zip_code": metadata.get("zip_code",
                                 variables.get("zip_code", "")),
        "address": variables.get("address", ""),
        "appointment_time": variables.get("appointment_time"),
        "outcome": outcome,
        "call_id": payload.get("call_id"),
        "call_duration_seconds": payload.get("call_length"),
    }


# ---------------------------------------------------------------------------
# run_mock_demo — Simulate the complete pipeline end-to-end
# ---------------------------------------------------------------------------

def run_mock_demo() -> dict:
    """
    Simulate the entire CallFlow AI pipeline in one call, no API keys needed.

    The flow:
        1. Incoming call received from Jane Doe (HVAC Repair, ZIP 37209)
        2. Bland AI call triggered — Aria calls the customer back
        3. Aria qualifies the lead, confirms address, picks a time slot
        4. Call ends — transcript captured with full conversation
        5. Appointment booked in ServiceTitan via servicetitan_client

    Returns a single dict summarising every step, suitable for returning
    directly as a JSON response from the /demo route.

    This function imports servicetitan_client here (rather than at module
    level) to keep bland_ai.py usable as a standalone module without
    requiring the ServiceTitan client to be configured.
    """
    # Local import so bland_ai.py doesn't hard-depend on servicetitan_client
    # at module load time — keeps it testable in isolation.
    import servicetitan_client as st

    now = datetime.now().isoformat()

    # -- Step 1: Incoming call data ------------------------------------------
    # This is what the telephony system sends us when a homeowner calls.
    call_data = {
        "caller_name": "Jane Doe",
        "phone_number": "555-123-4567",
        "service_type": "HVAC Repair",
        "zip_code": "37209",
    }

    # -- Step 2: Bland AI initiates a callback to the customer ---------------
    # In mock mode this returns a fake call_id and "queued" status instantly.
    bland_result = handle_inbound_call(**call_data)

    # -- Step 3: Simulate the completed voice conversation -------------------
    # In mock mode, get_call_summary returns Aria's full 9-turn transcript
    # where she books Jane Doe for HVAC Repair on March 25th at 10 AM.
    call_summary = get_call_summary(bland_result["call_id"])

    # -- Step 4: Book the appointment in ServiceTitan ------------------------
    # create_booking pushes the job into ServiceTitan's dispatch board.
    # In mock mode it returns a realistic confirmation with a ST-XXXXXXXX id.
    booking = st.create_booking(
        caller_name=call_summary["caller_name"],
        phone_number=call_data["phone_number"],
        service_type=call_summary["service_requested"],
        zip_code=call_data["zip_code"],
        appointment_time=call_summary["appointment_time"],
    )

    # -- Step 5: Assemble the full pipeline summary --------------------------
    return {
        "demo": True,
        "pipeline_summary": (
            "Complete flow: call received → Aria called back → "
            "lead qualified → appointment booked in ServiceTitan"
        ),
        "steps": {
            "1_call_received": {
                "timestamp": now,
                "description": "Homeowner calls the contractor's business line.",
                "caller": call_data,
            },
            "2_bland_ai_triggered": {
                "description": "Bland AI initiates callback — Aria dials the customer.",
                "call_id": bland_result["call_id"],
                "status": bland_result["status"],
                "message": "Aria is calling Jane Doe back...",
            },
            "3_voice_conversation": {
                "description": (
                    "Aria greets the caller, confirms service details and "
                    "address, checks availability, and books the appointment."
                ),
                "duration_seconds": call_summary["call_duration_seconds"],
                "outcome": call_summary["outcome"],
                "transcript": call_summary["transcript"],
            },
            "4_appointment_booked": {
                "description": (
                    "Appointment created in ServiceTitan — shows on the "
                    "dispatch board and triggers technician assignment."
                ),
                "servicetitan_booking": booking,
                "confirmation_number": booking.get("confirmation_number"),
                "appointment_time": call_summary["appointment_time"],
                "technician": booking.get("technician"),
            },
        },
    }
