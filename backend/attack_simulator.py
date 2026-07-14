"""
attack_simulator.py
--------------------
Crafts the malicious traffic used by the dashboard's "Attack Console".
Each function returns a `security.Command` (or signals a firmware-level
bypass) so the rest of the pipeline treats simulated attacks exactly like
real packets would be treated by the authenticator / validator / twin.

Attack types modelled (all real, documented ICS attack patterns):

  unsigned        - attacker without credentials injects a command with no
                     / a garbage signature. Caught by CommandAuthenticator.

  replayed        - attacker captured a legitimate signed command earlier
                     (e.g. via network tap) and resends it later, or resends
                     it twice. Caught by the nonce/timestamp replay check.

  stolen_key_jump - attacker has compromised the HMI or stolen its signing
                     key (fully "valid" credentials) and sends a physically
                     unsafe instantaneous setpoint jump. This is the
                     interesting case: a signature-only check would let it
                     through. Caught by CommandValidator's ramp-rate check.

  firmware_bypass - attacker has implanted malware directly on the VFD/PLC
                     (Stuxnet-style) and writes the output frequency
                     register directly, never touching the authenticated
                     command channel at all. No perimeter control can catch
                     this by definition -- only the digital twin's physics
                     residual can, because the motor's *behaviour* stops
                     matching what a legitimately-commanded motor would do.
"""

import time
import uuid

from security import Command, sign_command


def _nonce() -> str:
    return uuid.uuid4().hex[:12]


def make_legit_command(frequency_hz: float, label: str = "operator setpoint") -> Command:
    ts = time.time()
    nonce = _nonce()
    sig = sign_command(frequency_hz, ts, nonce)
    return Command(
        frequency_hz=frequency_hz, timestamp=ts, nonce=nonce,
        signature=sig, source="operator", label=label,
    )


def make_unsigned_injection(frequency_hz: float) -> Command:
    ts = time.time()
    return Command(
        frequency_hz=frequency_hz, timestamp=ts, nonce=_nonce(),
        signature="deadbeef" * 8, source="attacker",
        label=f"unsigned command -> {frequency_hz:.0f}Hz",
    )


def make_replay_attack(captured: Command) -> Command:
    """Resend a previously captured, validly-signed command unmodified."""
    replay = Command(
        frequency_hz=captured.frequency_hz,
        timestamp=captured.timestamp,   # stale on purpose
        nonce=captured.nonce,           # reused on purpose
        signature=captured.signature,
        source="attacker",
        label=f"replayed capture ({captured.frequency_hz:.0f}Hz @ t={captured.timestamp:.1f})",
    )
    return replay


def make_stolen_key_jump(frequency_hz: float = 60.0) -> Command:
    """Attacker has the real signing key but no regard for the ramp policy --
    a signed, fully 'authentic' command demanding an instant full-speed jump."""
    ts = time.time()
    nonce = _nonce()
    sig = sign_command(frequency_hz, ts, nonce)  # attacker CAN compute a valid sig
    return Command(
        frequency_hz=frequency_hz, timestamp=ts, nonce=nonce,
        signature=sig, source="attacker",
        label=f"signed instant jump -> {frequency_hz:.0f}Hz (stolen credentials)",
    )
