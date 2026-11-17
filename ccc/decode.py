"""Cisco kvGPB telemetry -> plain Python.

A Telemetry message carries rows in `data_gpbkv`. On IOS-XE every row has two
children: `keys` (the YANG list keys) and `content` (the leaves). Leaves keep
their YANG names, hyphens included, and containers nest.
"""

import hashlib

from ccc.pb import telemetry_pb2

# Fields that identify a person or a device. With ANONYMIZE_CLIENTS they are
# replaced before anything is kept, so the raw view never holds the real value.
MAC_FIELDS = {"client-mac", "ms-mac-address", "ms-mac-addr", "sta-mac"}
HIDDEN_FIELDS = {"username", "client-duid", "user-name"}
CLIENT_PATH_MARKER = "client-oper-data"


def parse(data):
    msg = telemetry_pb2.Telemetry()
    msg.ParseFromString(data)
    return msg


def _value(field):
    which = field.WhichOneof("value_by_type")
    if which is None:
        return None
    value = getattr(field, which)
    if which == "bytes_value":
        return value.hex()
    return value


def _children(fields):
    out = {}
    lists = set()
    for child in fields:
        value = _children(child.fields) if child.fields else _value(child)
        name = child.name
        if name in out:
            # A leaf-list or a nested list: the same name repeats.
            if name not in lists:
                out[name] = [out[name]]
                lists.add(name)
            out[name].append(value)
        else:
            out[name] = value
    return out


def rows(msg):
    """Every row as {"timestamp", "keys", "content"}."""
    result = []
    for row in msg.data_gpbkv:
        keys, content = {}, {}
        loose = []
        for child in row.fields:
            if child.name == "keys":
                keys = _children(child.fields)
            elif child.name == "content":
                content = _children(child.fields)
            else:
                loose.append(child)
        if loose:
            content = {**_children(loose), **content}
        result.append({"timestamp": row.timestamp, "keys": keys, "content": content})
    return result


def header(msg):
    return {
        "node_id": msg.node_id_str,
        "subscription_id": msg.subscription_id_str,
        "encoding_path": msg.encoding_path,
        "collection_id": msg.collection_id,
        "collection_start_time": msg.collection_start_time,
        "msg_timestamp": msg.msg_timestamp,
        "collection_end_time": msg.collection_end_time,
    }


def to_dict(msg):
    out = header(msg)
    out["rows"] = rows(msg)
    return out


def pseudonym(value, salt):
    digest = hashlib.sha256((salt + "|" + value.lower()).encode()).hexdigest()
    return "anon-" + digest[:12]


def anonymize(msg, salt):
    """Replace client identifiers in place. Returns True when anything changed."""
    if CLIENT_PATH_MARKER not in msg.encoding_path:
        return False
    changed = False

    def walk(fields):
        nonlocal changed
        for field in fields:
            if field.fields:
                walk(field.fields)
            elif field.name in MAC_FIELDS and field.WhichOneof("value_by_type") == "string_value":
                field.string_value = pseudonym(field.string_value, salt)
                changed = True
            elif field.name in HIDDEN_FIELDS and field.WhichOneof("value_by_type") == "string_value":
                if field.string_value:
                    field.string_value = "(hidden)"
                    changed = True

    walk(msg.data_gpbkv)
    return changed
