"""
CallFlow AI — SQLAlchemy database models.

Tables:
    Call        — One row per inbound/outbound call
    Booking     — Appointment details linked to a call (optional 1:1)
"""

import uuid
from datetime import datetime

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def _hex_id():
    return uuid.uuid4().hex[:10]


class Call(db.Model):
    __tablename__ = "calls"

    id = db.Column(db.String(10), primary_key=True, default=_hex_id)
    vapi_call_id = db.Column(db.String(128), nullable=True, index=True)
    caller_name = db.Column(db.String(256), nullable=False, default="Unknown Caller")
    phone_number = db.Column(db.String(32), nullable=False, default="Unknown")
    service_type = db.Column(db.String(128), nullable=False, default="")
    zip_code = db.Column(db.String(16), nullable=False, default="")
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    status = db.Column(db.String(32), nullable=False, default="completed")
    summary = db.Column(db.Text, nullable=False, default="")
    transcript = db.Column(db.JSON, nullable=False, default=list)
    recording_url = db.Column(db.Text, nullable=True)
    duration_seconds = db.Column(db.Integer, nullable=False, default=0)
    ended_reason = db.Column(db.String(64), nullable=True)

    # One-to-one relationship with booking
    booking = db.relationship("Booking", backref="call", uselist=False, cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "call_id": self.id,
            "vapi_call_id": self.vapi_call_id,
            "caller_name": self.caller_name,
            "phone_number": self.phone_number,
            "service_type": self.service_type,
            "zip_code": self.zip_code,
            "timestamp": self.timestamp.isoformat() if self.timestamp else "",
            "status": self.status,
            "summary": self.summary,
            "transcript": self.transcript or [],
            "recording_url": self.recording_url,
            "duration_seconds": self.duration_seconds,
            "ended_reason": self.ended_reason,
            "booking": self.booking.to_dict() if self.booking else None,
        }


class Booking(db.Model):
    __tablename__ = "bookings"

    id = db.Column(db.String(64), primary_key=True, default=lambda: f"BK-{uuid.uuid4().hex[:8].upper()}")
    call_id = db.Column(db.String(10), db.ForeignKey("calls.id"), nullable=False, unique=True)
    status = db.Column(db.String(32), nullable=False, default="Scheduled")
    customer = db.Column(db.String(256), nullable=False, default="")
    phone = db.Column(db.String(32), nullable=False, default="")
    service_type = db.Column(db.String(128), nullable=False, default="")
    zip_code = db.Column(db.String(16), nullable=False, default="")
    appointment_time = db.Column(db.DateTime, nullable=True)
    technician = db.Column(db.String(256), nullable=True)
    confirmation_number = db.Column(db.String(64), nullable=True)
    job_description = db.Column(db.Text, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "status": self.status,
            "customer": self.customer,
            "phone": self.phone,
            "service_type": self.service_type,
            "zip_code": self.zip_code,
            "appointment_time": self.appointment_time.isoformat() if self.appointment_time else "",
            "technician": self.technician,
            "confirmation_number": self.confirmation_number,
            "job_description": self.job_description,
        }
