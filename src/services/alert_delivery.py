"""Parse upstream /api/ai/query machine.alertDelivery (WhatsApp + email)."""

from __future__ import annotations

from typing import Any, Dict, Optional


def alert_delivery_from_machine(machine: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    raw = (machine or {}).get("alertDelivery")
    if not isinstance(raw, dict):
        return {"whatsapp": _channel(None), "email": _channel(None, email=True)}
    return {
        "whatsapp": _channel(raw.get("whatsapp")),
        "email": _channel(raw.get("email"), email=True),
    }


def _channel(block: Any, *, email: bool = False) -> Dict[str, Any]:
    if not isinstance(block, dict):
        return {"enabled": False, "phone": None, "email": None, "name": None}
    enabled = bool(block.get("enabled"))
    name = block.get("name")
    name = str(name).strip() if name else None
    phone = block.get("phone")
    phone = str(phone).strip() if phone else None
    mail = block.get("email")
    mail = str(mail).strip() if mail else None
    out = {"enabled": enabled, "name": name if enabled else None, "phone": None, "email": None}
    if email:
        out["email"] = mail if enabled else None
    else:
        out["phone"] = phone if enabled else None
        out["email"] = mail if enabled else None
    return out


def dispatch_status(channel: Dict[str, Any], *, kind: str) -> Dict[str, Any]:
    """Map a parsed channel to the alerts API dispatch object. Does not send a message."""
    enabled = bool(channel.get("enabled"))
    recipient = channel.get("phone") if kind == "whatsapp" else channel.get("email")
    if enabled and recipient:
        return {"status": "sent", "enabled": True, "recipient": recipient, "name": channel.get("name")}
    if enabled and not recipient:
        return {"status": "skipped", "enabled": True, "recipient": None, "name": channel.get("name"), "reason": "no_recipient"}
    return {"status": "skipped", "enabled": False, "recipient": None, "name": None}
