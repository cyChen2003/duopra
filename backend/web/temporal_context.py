"""Host calendar facts shared by voice sessions and the background assistant."""
from datetime import datetime, timedelta, timezone
import json
from zoneinfo import ZoneInfo


SYSTEM_PREFIX = "Streaming Omni Conversation."
SCHEMA = "venus-host-temporal-context-v1"


def clock_payload(now=None, timezone_name="Asia/Shanghai"):
    """Build bounded, typed facts; ``now`` is injectable for deterministic tests."""
    if type(timezone_name) is not str or len(timezone_name) > 80:
        raise ValueError("Expected a short IANA timezone")
    zone = ZoneInfo(timezone_name)
    current = now if now is not None else datetime.now(timezone.utc)
    if not isinstance(current, datetime) or current.tzinfo is None:
        raise ValueError("Clock must be an aware datetime")
    local = current.astimezone(zone)
    return {
        "session_context": {
            "schema": SCHEMA,
            "source": "host_clock",
            "current_datetime": local.isoformat(timespec="seconds"),
            "current_date": local.date().isoformat(),
            "current_year": local.year,
            "timezone": timezone_name,
            "relative_dates": {
                "today": local.date().isoformat(),
                "tomorrow": (local.date() + timedelta(days=1)).isoformat(),
                "day_after_tomorrow": (local.date() + timedelta(days=2)).isoformat(),
            },
            "date_policy": (
                "Use relative_dates for 今天/today, 明天/tomorrow, 后天/day after tomorrow; "
                "these are already resolved calendar dates, do not add another day. "
                "For a month and day without a year, use current_year unless "
                "the conversation specifies otherwise. Keep explicit user dates "
                "unchanged. Ask when the intended date or location remains "
                "ambiguous. This context is private metadata, not a tool result "
                "or text to read aloud."
            ),
        }
    }


def serialize(payload):
    """Use the deployed protocol wrapper, escaping any delimiter characters."""
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False)
    return "<backend>" + raw.replace("<", "\\u003c").replace(">", "\\u003e") + "</backend>"


class SessionClock:
    """Deliver once before unit0 and refresh only at a local calendar rollover.

    The caller combines this *separate* observation with tools/results without
    changing their Schemas or completion order. Timestamp drift within a day
    does not repeatedly add context tokens to the native KV cache.
    """
    def __init__(self, timezone_name="Asia/Shanghai", enabled=False):
        if type(enabled) is not bool:
            raise ValueError("Clock enable flag must be a boolean")
        # Validate deployment configuration before a session acquires the KV
        # owner, including when the feature is disabled.
        clock_payload(datetime(2000, 1, 1, tzinfo=timezone.utc), timezone_name)
        self.timezone_name = timezone_name
        self.enabled = enabled
        self.last_date = None
        self.last_unit = -1
        self.last_context = None

    def before_input_unit(self, unit_index, now=None):
        if type(unit_index) is not int or unit_index < 0 or unit_index <= self.last_unit:
            raise ValueError("Clock unit must increase monotonically")
        self.last_unit = unit_index
        if not self.enabled:
            return []
        payload = clock_payload(now, self.timezone_name)
        current_date = payload["session_context"]["current_date"]
        if current_date == self.last_date:
            return []
        self.last_date = current_date
        self.last_context = payload["session_context"].copy()
        return [serialize(payload)]

    def proof(self):
        return {"schema": SCHEMA, "enabled": self.enabled,
                "timezone": self.timezone_name, "source": "host_clock",
                "native_system_prefix_unchanged": True,
                "tool_schema_unchanged": True,
                "generated_action_dates_rewritten": False,
                "last_delivered_context": self.last_context.copy() if self.last_context else None,
                "model_date_accuracy_or_speech_acceptance_claimed": False}
