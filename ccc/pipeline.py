"""From telemetry messages to stored counts.

    message  ->  raw buffer (always, for the Raw page)
             ->  cycle (the burst of messages that makes up one periodic update)
             ->  translation (translate.py)  ->  store

Everything the web interface shows about the live stream is read from here.
"""

import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone

from ccc import decode, translate

log = logging.getLogger("ccc.pipeline")

MAX_CYCLE_ROWS = 2_000_000


class _Cycle:
    def __init__(self, cycle_id, key, kind, peer, now, seq):
        self.id = cycle_id
        self.key = key
        self.kind = kind
        self.peer = peer
        self.started_at = now
        self.last_at = now
        self.first_seq = seq
        self.last_seq = seq
        self.messages = 0
        self.bytes = 0
        self.rows = []
        self.collection_ids = set()
        self.wlc_msg_timestamp = 0

    def add(self, now, seq, size, hdr, rows):
        self.last_at = now
        self.last_seq = seq
        self.messages += 1
        self.bytes += size
        self.rows.extend(rows)
        if len(self.collection_ids) < 10_000:
            self.collection_ids.add(hdr["collection_id"])
        if not self.wlc_msg_timestamp:
            self.wlc_msg_timestamp = hdr["msg_timestamp"]


def _iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


class Pipeline:
    def __init__(self, config, store=None, clock=time.time):
        self.config = config
        self.store = store
        self.clock = clock
        self.lock = threading.Lock()
        self.started_at = clock()

        self.seq = 0
        self.raw = deque(maxlen=config.raw_buffer_messages)
        self.streams = {}  # (wlc, subscription, path) -> stats
        self.connections = {}  # peer -> since
        self.errors = deque(maxlen=50)

        self.cycle_seq = 0
        self.open = {}  # stream key -> _Cycle
        self.done = deque(maxlen=max(config.keep_cycles, 1) * 8)

        self.inventory = {}  # wlc -> {"at": epoch, "aps": {name: info}}
        self.ap_last_seen = {}  # wlc -> {name: epoch}

    # ── state restored from the database on start ────────────────────────────
    def load_known_aps(self, rows):
        for wlc, ap_name, last_seen in rows:
            self.ap_last_seen.setdefault(wlc, {})[ap_name] = last_seen

    # ── receiver side ────────────────────────────────────────────────────────
    def connection_opened(self, peer):
        with self.lock:
            self.connections[peer] = self.clock()

    def connection_closed(self, peer):
        with self.lock:
            self.connections.pop(peer, None)

    def note_error(self, peer, text):
        log.warning("%s: %s", peer, text)
        with self.lock:
            self.errors.appendleft({"at": _iso(self.clock()), "peer": peer, "error": text})

    def ingest(self, peer, data):
        now = self.clock()
        try:
            msg = decode.parse(data)
            if self.config.anonymize_clients and decode.anonymize(msg, self.config.anonymize_salt):
                data = msg.SerializeToString()
            hdr = decode.header(msg)
            rows = decode.rows(msg)
        except Exception as exc:  # not a Telemetry message
            self.note_error(peer, f"could not decode a {len(data)} byte message: {exc}")
            return

        kind = translate.kind_for(hdr["encoding_path"])
        key = (hdr["node_id"], hdr["subscription_id"], hdr["encoding_path"])
        with self.lock:
            self.seq += 1
            seq = self.seq
            self.raw.append(
                {
                    "seq": seq,
                    "received_at": now,
                    "peer": peer,
                    "rows": len(rows),
                    "bytes": len(data),
                    "data": data,
                    **hdr,
                }
            )
            stream = self.streams.get(key)
            if stream is None:
                stream = self.streams[key] = {
                    "wlc": key[0],
                    "subscription_id": key[1],
                    "encoding_path": key[2],
                    "kind": kind,
                    "first_seen": now,
                    "messages": 0,
                    "rows": 0,
                    "bytes": 0,
                    "cycles": 0,
                    "period": None,
                    "last_cycle_started": None,
                    "last_cycle_rows": None,
                    "empty": False,
                }
            stream["peer"] = peer
            stream["last_seen"] = now
            stream["messages"] += 1
            stream["rows"] += len(rows)
            stream["bytes"] += len(data)

            if kind:
                cycle = self.open.get(key)
                if cycle is None:
                    self.cycle_seq += 1
                    cycle = self.open[key] = _Cycle(self.cycle_seq, key, kind, peer, now, seq)
                cycle.add(now, seq, len(data), hdr, rows)

    # ── cycles ───────────────────────────────────────────────────────────────
    def sweep(self, now=None):
        """Close every cycle whose stream has gone quiet. Returns what closed."""
        now = self.clock() if now is None else now
        ready = []
        with self.lock:
            for key, cycle in list(self.open.items()):
                quiet = now - cycle.last_at >= self.config.cycle_gap_seconds
                if quiet or len(cycle.rows) >= MAX_CYCLE_ROWS:
                    ready.append(self.open.pop(key))
        closed = []
        for cycle in sorted(ready, key=lambda c: c.kind != translate.KIND_INVENTORY):
            try:
                closed.append(self._close(cycle))
            except Exception:
                log.exception("cycle %s of %s failed", cycle.id, cycle.key)
        return closed

    def run_sweeper(self, stop):
        while not stop.wait(1.0):
            self.sweep()

    def _base(self, cycle):
        wlc, subscription_id, path = cycle.key
        ids = sorted(cycle.collection_ids)
        return {
            "id": cycle.id,
            "kind": cycle.kind,
            "wlc": wlc,
            "subscription_id": subscription_id,
            "encoding_path": path,
            "peer": cycle.peer,
            "ts": _iso(int(cycle.started_at)),
            "ts_epoch": int(cycle.started_at),
            "duration_ms": int((cycle.last_at - cycle.started_at) * 1000),
            "messages": cycle.messages,
            "bytes": cycle.bytes,
            "first_seq": cycle.first_seq,
            "last_seq": cycle.last_seq,
            "collection_ids": {
                "first": ids[0] if ids else None,
                "last": ids[-1] if ids else None,
                "distinct": len(ids),
            },
            "wlc_msg_timestamp": cycle.wlc_msg_timestamp,
            "sample_row": cycle.rows[0] if cycle.rows else None,
            "stored": False,
            "store_error": None,
        }

    def _known_aps(self, wlc, now):
        inventory = self.inventory.get(wlc)
        if inventory and now - inventory["at"] <= self.config.inventory_max_age:
            return set(inventory["aps"]), "inventory"
        horizon = now - self.config.zero_fill_days * 86400
        recent = {ap for ap, seen in self.ap_last_seen.get(wlc, {}).items() if seen >= horizon}
        return recent, "recent"

    def _close(self, cycle):
        result = self._base(cycle)
        wlc = result["wlc"]
        now = cycle.started_at

        if cycle.kind == translate.KIND_CLIENTS:
            translated = translate.translate_clients(cycle.rows, self.config.count_states)
            counts = dict(translated.pop("counts"))
            with self.lock:
                known, source = self._known_aps(wlc, now)
                zero_filled = sorted(known - set(counts))
                seen = self.ap_last_seen.setdefault(wlc, {})
                for ap in counts:
                    seen[ap] = now
            for ap in zero_filled:
                counts[ap] = 0
            result.update(translated)
            result.update(
                counts=counts,
                aps_with_clients=len(counts) - len(zero_filled),
                zero_filled=zero_filled,
                zero_fill_source=source if zero_filled else None,
            )
        else:
            translated = translate.translate_inventory(cycle.rows)
            result.update(translated)
            with self.lock:
                self.inventory[wlc] = {"at": now, "aps": translated["aps"]}
                seen = self.ap_last_seen.setdefault(wlc, {})
                for ap in translated["aps"]:
                    seen[ap] = now

        # A source that only sends empty tables (a device that has the
        # subscription but no access points or clients) is received and stays
        # visible under Raw data, but is neither stored nor shown as a controller.
        if cycle.kind == translate.KIND_CLIENTS:
            empty = not result["counts"]
        else:
            empty = not result["aps"]
        result["empty"] = empty

        if self.store is not None and not empty:
            try:
                if cycle.kind == translate.KIND_CLIENTS:
                    self.store.save_client_cycle(result)
                else:
                    self.store.save_inventory(result)
                result["stored"] = True
            except Exception as exc:
                log.exception("could not store cycle %s", cycle.id)
                result["store_error"] = str(exc)

        with self.lock:
            stream = self.streams.get(cycle.key)
            if stream is not None:
                if stream["last_cycle_started"] is not None:
                    stream["period"] = round(cycle.started_at - stream["last_cycle_started"], 1)
                stream["last_cycle_started"] = cycle.started_at
                stream["last_cycle_rows"] = len(cycle.rows)
                stream["cycles"] += 1
                newly_empty = empty and not stream["empty"]
                stream["empty"] = empty
            else:
                newly_empty = False
            if empty:
                if newly_empty:
                    log.info(
                        "%s subscription %s (%s) sends an empty table; ignored until it has data",
                        wlc, result["subscription_id"], cycle.kind,
                    )
                return result
            self.done.appendleft(result)
            # Keep only the newest few per WLC and kind: the decisions are large.
            kept = {}
            for item in list(self.done):
                bucket = (item["wlc"], item["kind"])
                kept[bucket] = kept.get(bucket, 0) + 1
                if kept[bucket] > self.config.keep_cycles:
                    self.done.remove(item)
        log.info(
            "cycle %s %s %s: %s rows in %s messages%s",
            result["id"],
            wlc,
            cycle.kind,
            len(cycle.rows),
            cycle.messages,
            f", {result['counted']} clients on {result['aps_with_clients']} APs"
            if cycle.kind == translate.KIND_CLIENTS
            else f", {len(result['aps'])} APs",
        )
        return result

    # ── read side (web) ──────────────────────────────────────────────────────
    def status(self):
        now = self.clock()
        with self.lock:
            streams = []
            for stream in self.streams.values():
                item = dict(stream)
                for field in ("first_seen", "last_seen", "last_cycle_started"):
                    item[field + "_age"] = None if item[field] is None else round(now - item[field], 1)
                    item[field] = None if item[field] is None else _iso(item[field])
                streams.append(item)
            return {
                "now": _iso(now),
                "uptime": round(now - self.started_at),
                "messages": self.seq,
                "raw_buffered": len(self.raw),
                "raw_capacity": self.raw.maxlen,
                "connections": [
                    {"peer": peer, "since": _iso(since)} for peer, since in self.connections.items()
                ],
                "streams": sorted(streams, key=lambda s: (s["wlc"], s["subscription_id"])),
                "errors": list(self.errors),
                "anonymize_clients": self.config.anonymize_clients,
                "count_states": sorted(self.config.count_states),
                "cycle_gap_seconds": self.config.cycle_gap_seconds,
            }

    @staticmethod
    def _raw_summary(entry):
        item = {k: v for k, v in entry.items() if k != "data"}
        item["received_at"] = datetime.fromtimestamp(entry["received_at"], timezone.utc).isoformat(
            timespec="milliseconds"
        )
        return item

    def raw_list(self, limit=100, wlc=None, path=None, first_seq=None, last_seq=None):
        with self.lock:
            entries = list(self.raw)
        out = []
        for entry in reversed(entries):
            if wlc and entry["node_id"] != wlc:
                continue
            if path and entry["encoding_path"] != path:
                continue
            if first_seq is not None and entry["seq"] < first_seq:
                continue
            if last_seq is not None and entry["seq"] > last_seq:
                continue
            out.append(self._raw_summary(entry))
            if len(out) >= limit:
                break
        return out

    def raw_get(self, seq):
        with self.lock:
            entry = next((e for e in self.raw if e["seq"] == seq), None)
        if entry is None:
            return None
        msg = decode.parse(entry["data"])
        item = self._raw_summary(entry)
        item["decoded"] = decode.to_dict(msg)
        item["protobuf_text"] = str(msg)
        return item

    def cycles(self, kind=None, wlc=None):
        """Summaries, newest first, without the per-client decisions."""
        with self.lock:
            items = list(self.done)
        out = []
        for item in items:
            if kind and item["kind"] != kind:
                continue
            if wlc and item["wlc"] != wlc:
                continue
            out.append({k: v for k, v in item.items() if k not in ("decisions", "aps", "sample_row")})
        return out

    def cycle(self, cycle_id):
        with self.lock:
            return next((item for item in self.done if item["id"] == cycle_id), None)
