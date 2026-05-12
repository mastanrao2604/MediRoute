"""
Canonical dispatch event type constants.

Every significant state change emits one of these event types to ShiftTimelineEvent.
Phase 1: in-process handler calls.
Phase 3: Kafka topic messages (same constants, zero change to callers).

See §18 (Event-Oriented Architecture) in ARCHITECTURE.md.
"""

# ── Shift lifecycle ────────────────────────────────────────────────────────
SHIFT_CREATED          = "shift.created"
SHIFT_DISPATCHING      = "shift.dispatching"
SHIFT_FILLED           = "shift.filled"
SHIFT_EXPIRED          = "shift.expired"
SHIFT_CANCELLED        = "shift.cancelled"

# ── Offer lifecycle ────────────────────────────────────────────────────────
OFFER_SENT             = "offer.sent"
OFFER_ACCEPTED         = "offer.accepted"
OFFER_DECLINED         = "offer.declined"
OFFER_TIMED_OUT        = "offer.timed_out"
OFFER_CANCELLED        = "offer.cancelled"

# ── Nurse presence ─────────────────────────────────────────────────────────
NURSE_ONLINE           = "nurse.online"
NURSE_OFFLINE          = "nurse.offline"
NURSE_LOCATION_UPDATED = "nurse.location_updated"
NURSE_BUSY             = "nurse.busy"        # accepted an assignment
NURSE_AVAILABLE        = "nurse.available"   # released from assignment

# ── Assignment lifecycle ───────────────────────────────────────────────────
ASSIGNMENT_CREATED     = "assignment.created"
ASSIGNMENT_CHECKIN     = "assignment.checked_in"
ASSIGNMENT_CHECKOUT    = "assignment.checked_out"
ASSIGNMENT_COMPLETED   = "assignment.completed"
ASSIGNMENT_NO_SHOW     = "assignment.no_show"
ASSIGNMENT_CANCELLED   = "assignment.cancelled"

# ── Trust + operations ─────────────────────────────────────────────────────
RELIABILITY_UPDATED    = "reliability.score_updated"
FRAUD_FLAGGED          = "fraud.flagged"
MANUAL_OVERRIDE        = "dispatch.manual_override"
WAVE_EXHAUSTED         = "dispatch.wave_exhausted"
DISPATCH_FAILED        = "dispatch.failed"
