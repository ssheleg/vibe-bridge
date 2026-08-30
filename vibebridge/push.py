"""Web Push — the consent request that reaches the owner's pocket (ADR-0004).

Outbound-only: the bridge POSTs to the push service named in each
subscription (APNs/FCM infrastructure delivers); no inbound reachability is
needed, which is what lets a tailnet-private origin push at all
(research-notes §C). VAPID keys are generated once and live in the state
file (0600). A dead subscription (404/410 from the push service) is pruned,
not retried forever. Losing a push changes nothing: the 60-second
timeout-refusal default stands (SCN-004).
"""
from __future__ import annotations

import json
import logging

from .state import BridgeState

log = logging.getLogger("vibe-bridge.push")


def ensure_vapid_keys(state: BridgeState) -> None:
    """Generate the VAPID pair on first need; idempotent."""
    if state.vapid_private and state.vapid_public:
        return
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid02, b64urlencode

    v = Vapid02()
    v.generate_keys()
    state.vapid_private = v.private_pem().decode()
    raw = v.public_key.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint)
    state.vapid_public = b64urlencode(raw)
    state.save()


class PushSender:
    """Thin, injectable shell over pywebpush. `webpush` is swappable so
    tests never talk to a real push service."""

    def __init__(self, state: BridgeState, *, webpush=None) -> None:
        self.state = state
        if webpush is None:  # pragma: no cover - import indirection
            from pywebpush import webpush as _webpush
            webpush = _webpush
        self._webpush = webpush

    def send_to_all(self, payload: dict) -> int:
        """Send payload to every subscription; prune the dead; return the
        number delivered. Never raises — a failed push must not touch the
        consent path."""
        if not self.state.push_subscriptions:
            return 0
        ensure_vapid_keys(self.state)
        from pywebpush import WebPushException

        delivered, alive = 0, []
        for sub in self.state.push_subscriptions:
            try:
                self._webpush(
                    subscription_info=sub,
                    data=json.dumps(payload, ensure_ascii=False),
                    vapid_private_key=self.state.vapid_private,
                    vapid_claims={"sub": "mailto:owner@vibe-bridge.local"},
                    ttl=90,
                )
                delivered += 1
                alive.append(sub)
            except WebPushException as exc:
                code = getattr(getattr(exc, "response", None), "status_code", 0)
                if code in (401, 403, 404, 410):
                    log.info("pruning dead push subscription (%s)", code)
                    continue                    # dropped — endpoint is gone
                alive.append(sub)               # transient: keep, don't retry
            except Exception:
                log.exception("push send failed")
                alive.append(sub)
        if len(alive) != len(self.state.push_subscriptions):
            self.state.push_subscriptions = alive
            self.state.save()
        return delivered

    def add_subscription(self, sub: dict) -> None:
        """Store a browser PushSubscription (endpoint is the identity)."""
        endpoint = str(sub.get("endpoint", ""))
        if not endpoint:
            raise ValueError("subscription without endpoint")
        subs = [s for s in self.state.push_subscriptions
                if s.get("endpoint") != endpoint]
        subs.append(sub)
        self.state.push_subscriptions = subs
        self.state.save()

    def remove_subscription(self, endpoint: str) -> bool:
        before = len(self.state.push_subscriptions)
        self.state.push_subscriptions = [
            s for s in self.state.push_subscriptions
            if s.get("endpoint") != endpoint]
        if len(self.state.push_subscriptions) != before:
            self.state.save()
            return True
        return False
