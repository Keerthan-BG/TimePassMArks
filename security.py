"""
security.py
------------
Two independent defensive layers for the motor's control channel, modelled
on real ICS/SCADA hardening practice:

1. CommandAuthenticator  - cryptographic integrity/authenticity (HMAC) +
                            anti-replay (nonce + timestamp window).
                            Stops an attacker who is NOT on the trusted HMI.

2. CommandValidator       - a "process-aware" filter that rejects commands
                            that are cryptographically valid but physically
                            unreasonable (e.g. an instant 0->60Hz jump that
                            violates the drive's rated acceleration ramp).
                            Stops a compromised-but-authenticated HMI, or an
                            attacker who has stolen valid credentials.

Both layers can be individually disabled from the dashboard so the same
attack can be replayed with defenses on vs off -- that's the point of the
demo: showing *why* each layer matters, not just that "security is on".
"""

import hashlib
import hmac
import time
from collections import deque
from dataclasses import dataclass, field


SHARED_SECRET = b"replace-this-with-a-real-hsm-backed-key"  # demo only!
REPLAY_WINDOW_S = 5.0
MAX_RAMP_HZ_PER_S = 5.0     # a typical VFD acceleration-ramp setting
MAX_FREQ_HZ = 60.0
MIN_FREQ_HZ = 0.0


def sign_command(frequency_hz: float, timestamp: float, nonce: str) -> str:
    msg = f"{frequency_hz:.4f}|{timestamp:.3f}|{nonce}".encode()
    return hmac.new(SHARED_SECRET, msg, hashlib.sha256).hexdigest()


@dataclass
class Command:
    frequency_hz: float
    timestamp: float
    nonce: str
    signature: str = ""
    source: str = "operator"   # "operator" | "attacker"
    label: str = ""             # human-readable description for the log


class CommandAuthenticator:
    """HMAC signature + anti-replay check."""

    def __init__(self):
        self._seen_nonces = deque(maxlen=500)
        self._seen_set = set()

    def verify(self, cmd: Command, now: float) -> tuple[bool, str]:
        expected_sig = sign_command(cmd.frequency_hz, cmd.timestamp, cmd.nonce)
        if not hmac.compare_digest(expected_sig, cmd.signature or ""):
            return False, "invalid signature"

        if abs(now - cmd.timestamp) > REPLAY_WINDOW_S:
            return False, "stale timestamp (possible replay)"

        if cmd.nonce in self._seen_set:
            return False, "nonce reused (replay attack)"

        self._seen_set.add(cmd.nonce)
        self._seen_nonces.append(cmd.nonce)
        if len(self._seen_nonces) == self._seen_nonces.maxlen:
            # evict oldest as deque rolls over
            self._seen_set = set(self._seen_nonces)

        return True, "ok"


class CommandValidator:
    """Physics-based plausibility filter -- independent of crypto identity."""

    def __init__(self, max_ramp_hz_s: float = MAX_RAMP_HZ_PER_S):
        self.max_ramp_hz_s = max_ramp_hz_s
        self._last_freq = 0.0
        self._last_time = None

    def check(self, cmd: Command, now: float) -> tuple[bool, str]:
        if not (MIN_FREQ_HZ <= cmd.frequency_hz <= MAX_FREQ_HZ):
            return False, f"frequency {cmd.frequency_hz:.1f}Hz outside safe envelope"

        if self._last_time is not None:
            dt = max(1e-3, now - self._last_time)
            max_delta = self.max_ramp_hz_s * dt + 0.5  # small tolerance
            delta = abs(cmd.frequency_hz - self._last_freq)
            if delta > max_delta:
                return False, (
                    f"requested step of {delta:.1f}Hz exceeds rated ramp "
                    f"({self.max_ramp_hz_s} Hz/s) -- physically implausible for a legitimate command"
                )

        return True, "ok"

    def accept(self, cmd: Command, now: float):
        """Call only after a command has been accepted, to advance the baseline."""
        self._last_freq = cmd.frequency_hz
        self._last_time = now


@dataclass
class SecurityEvent:
    time_s: float
    source: str
    label: str
    verdict: str        # "ACCEPTED" | "REJECTED_AUTH" | "REJECTED_PHYSICS" | "BYPASSED"
    detail: str


class SecurityMonitor:
    """Orchestrates authentication + physics validation, and can be toggled
    off (per layer) to demonstrate what an unprotected system looks like."""

    def __init__(self):
        self.authenticator = CommandAuthenticator()
        self.validator = CommandValidator()
        self.auth_enabled = True
        self.physics_filter_enabled = True
        self.events: deque[SecurityEvent] = deque(maxlen=200)
        self.rejected_count = 0
        self.accepted_count = 0

    def evaluate(self, cmd: Command, sim_time_s: float, now: float = None):
        now = now if now is not None else time.time()

        if self.auth_enabled:
            ok, reason = self.authenticator.verify(cmd, now)
            if not ok:
                self.rejected_count += 1
                evt = SecurityEvent(sim_time_s, cmd.source, cmd.label, "REJECTED_AUTH", reason)
                self.events.append(evt)
                return False, evt

        if self.physics_filter_enabled:
            ok, reason = self.validator.check(cmd, now)
            if not ok:
                self.rejected_count += 1
                evt = SecurityEvent(sim_time_s, cmd.source, cmd.label, "REJECTED_PHYSICS", reason)
                self.events.append(evt)
                return False, evt

        self.validator.accept(cmd, now)
        self.accepted_count += 1
        verdict = "ACCEPTED" if cmd.source == "operator" else "BYPASSED"
        evt = SecurityEvent(sim_time_s, cmd.source, cmd.label, verdict, "passed all enabled checks")
        self.events.append(evt)
        return True, evt

    def snapshot(self):
        return {
            "auth_enabled": self.auth_enabled,
            "physics_filter_enabled": self.physics_filter_enabled,
            "accepted_count": self.accepted_count,
            "rejected_count": self.rejected_count,
            "events": [
                {
                    "time_s": e.time_s,
                    "source": e.source,
                    "label": e.label,
                    "verdict": e.verdict,
                    "detail": e.detail,
                }
                for e in list(self.events)[-25:][::-1]
            ],
        }
