"""The translation: telemetry rows -> a number per access point.

Pure functions, no I/O. What the web interface shows under "Translation" is
exactly what these functions return, so the page cannot drift from the count.
"""

from collections import Counter

CLIENT_PATH = "client-oper-data/common-oper-data"
AP_PATH = "access-point-oper-data/capwap-data"

KIND_CLIENTS = "clients"
KIND_INVENTORY = "inventory"


def kind_for(encoding_path):
    """Which translator handles this stream, or None for raw-only."""
    path = (encoding_path or "").rstrip("/")
    if path.endswith(CLIENT_PATH):
        return KIND_CLIENTS
    if path.endswith(AP_PATH):
        return KIND_INVENTORY
    return None


def _get(data, *path):
    for part in path:
        if not isinstance(data, dict):
            return None
        data = data.get(part)
    return data


def _text(value):
    return "" if value is None else str(value).strip()


def translate_clients(rows, count_states=("client-status-run",)):
    """One row is one client. A client counts for its AP when its state is in
    `count_states` and the row names an AP. A MAC that appears twice in one
    cycle counts once; the last row wins.
    """
    count_states = set(count_states)
    by_client = {}
    duplicates = 0

    for index, row in enumerate(rows):
        keys, content = row.get("keys") or {}, row.get("content") or {}
        mac = _text(keys.get("client-mac") or content.get("client-mac")).lower()
        ap_name = _text(content.get("ap-name"))
        state = _text(content.get("co-state"))

        if not ap_name:
            counted, reason = False, "no ap-name in the row"
        elif state not in count_states:
            counted, reason = False, f"co-state is {state or '(empty)'}"
        else:
            counted, reason = True, f"co-state is {state}"

        decision = {
            "client_mac": mac,
            "ap_name": ap_name,
            "co_state": state,
            "wlan_id": content.get("wlan-id"),
            "slot": content.get("ms-ap-slot-id"),
            "radio_type": _text(content.get("ms-radio-type")),
            "counted": counted,
            "reason": reason,
        }
        key = mac or f"row-{index}"
        if key in by_client:
            duplicates += 1
        by_client[key] = decision

    decisions = list(by_client.values())
    counts = Counter(d["ap_name"] for d in decisions if d["counted"])
    skipped = Counter(d["reason"] for d in decisions if not d["counted"])
    return {
        "rows": len(rows),
        "duplicates": duplicates,
        "clients": len(decisions),
        "counted": sum(counts.values()),
        "not_counted": sum(skipped.values()),
        "skipped": dict(skipped),
        "counts": dict(counts),
        "decisions": decisions,
    }


def translate_inventory(rows):
    """One row is one joined access point."""
    aps = {}
    for row in rows:
        keys, content = row.get("keys") or {}, row.get("content") or {}
        name = _text(content.get("name"))
        if not name:
            continue
        aps[name] = {
            "ap_name": name,
            "wtp_mac": _text(keys.get("wtp-mac") or content.get("wtp-mac")).lower(),
            "ip": _text(content.get("wtp-ip") or content.get("ip-addr")),
            "location": _text(_get(content, "ap-location", "location")),
            "site_tag": _text(_get(content, "tag-info", "site-tag", "site-tag-name")),
            "policy_tag": _text(_get(content, "tag-info", "policy-tag-info", "policy-tag-name")),
            "rf_tag": _text(_get(content, "tag-info", "rf-tag", "rf-tag-name")),
            "model": _text(_get(content, "device-detail", "static-info", "ap-models", "model")),
        }
    return {"rows": len(rows), "aps": aps}
