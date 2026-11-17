"""A stand-in for a Catalyst 9800, to try the tool without a controller.

It dials out over gRPC exactly like the controller does and sends the two
subscriptions the tool counts from, in the same kvGPB shape as IOS XE:

    docker compose exec app python tools/simulate_wlc.py --aps 40 --clients 600 --period 30

The data is made up: clients wander between access points, a few are still
authenticating (and so are not counted), and some access points stay empty.
"""

import argparse
import random
import sys
import time
from pathlib import Path

import grpc

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ccc.pb import mdt_dialout_pb2, telemetry_pb2

CLIENT_PATH = "Cisco-IOS-XE-wireless-client-oper:client-oper-data/common-oper-data"
AP_PATH = "Cisco-IOS-XE-wireless-access-point-oper:access-point-oper-data/capwap-data"
OTHER_STATES = [
    "client-status-authenticating",
    "client-status-ip-learning",
    "client-status-webauth-required",
    "client-status-associated",
]


def field(parent, name, value=None):
    f = parent.fields.add()
    f.name = name
    if isinstance(value, bool):
        f.bool_value = value
    elif isinstance(value, int):
        f.uint32_value = value
    elif isinstance(value, str):
        f.string_value = value
    return f


def mac(rng):
    return ":".join(f"{rng.randrange(256):02x}" for _ in range(6))


class Network:
    def __init__(self, args):
        self.rng = random.Random(args.seed)
        self.name = args.name
        buildings = ["Forum", "Atlas", "Orion", "Helix", "Radix"]
        self.aps = []
        for i in range(args.aps):
            building = buildings[i % len(buildings)]
            floor = (i // len(buildings)) % 4
            self.aps.append(
                {
                    "name": f"AP-{building.upper()}-{floor}{i:03d}",
                    "wtp_mac": mac(self.rng),
                    "ip": f"10.20.{i // 250}.{i % 250 + 1}",
                    "location": f"{building} floor {floor}",
                    "site_tag": f"ST-{building.upper()}",
                    "policy_tag": "PT-CAMPUS",
                }
            )
        # A fifth of the access points never gets clients.
        self.busy = self.aps[: max(1, int(len(self.aps) * 0.8))]
        self.clients = [
            {"mac": mac(self.rng), "ap": self.rng.choice(self.busy), "user": f"user{i:04d}"}
            for i in range(args.clients)
        ]

    def step(self):
        for client in self.clients:
            if self.rng.random() < 0.08:
                client["ap"] = self.rng.choice(self.busy)

    def client_rows(self, now_ms):
        for client in self.clients:
            row = telemetry_pb2.TelemetryField(timestamp=now_ms)
            keys = field(row, "keys")
            field(keys, "client-mac", client["mac"])
            content = field(row, "content")
            field(content, "client-mac", client["mac"])
            field(content, "ap-name", client["ap"]["name"])
            field(content, "ms-ap-slot-id", self.rng.choice([0, 1, 1, 1, 2]))
            field(content, "ms-radio-type", "client-dot11ax-5ghz-prot")
            field(content, "wlan-id", self.rng.choice([1, 1, 1, 2, 17]))
            field(content, "client-type", "dot11-client-normal")
            state = self.rng.choice(OTHER_STATES) if self.rng.random() < 0.04 else "client-status-run"
            field(content, "co-state", state)
            field(content, "aaa-override-passphrase", False)
            field(content, "username", client["user"])
            policy = field(content, "wlan-policy")
            field(policy, "current-switching-mode", "local")
            field(policy, "wlan-switching-mode", "local")
            yield row

    def ap_rows(self, now_ms):
        for ap in self.aps:
            row = telemetry_pb2.TelemetryField(timestamp=now_ms)
            keys = field(row, "keys")
            field(keys, "wtp-mac", ap["wtp_mac"])
            content = field(row, "content")
            field(content, "wtp-ip", ap["ip"])
            field(content, "name", ap["name"])
            detail = field(content, "device-detail")
            static = field(detail, "static-info")
            models = field(static, "ap-models")
            field(models, "model", "C9130AXI-E")
            location = field(content, "ap-location")
            field(location, "floor", 0)
            field(location, "location", ap["location"])
            tags = field(content, "tag-info")
            field(field(tags, "policy-tag-info"), "policy-tag-name", ap["policy_tag"])
            field(field(tags, "site-tag"), "site-tag-name", ap["site_tag"])
            field(field(tags, "rf-tag"), "rf-tag-name", "default-rf-tag")
            state = field(content, "ap-state")
            field(state, "ap-admin-state", "adminstate-enabled")
            field(state, "ap-operation-state", "ap-up")
            yield row


def messages(name, subscription_id, path, rows, per_message, collection_id):
    batch, sent = [], 0
    for row in rows:
        batch.append(row)
        if len(batch) == per_message:
            yield build(name, subscription_id, path, batch, collection_id)
            batch, sent = [], sent + 1
    if batch or not sent:
        # An empty table still produces a message, as on a real device.
        yield build(name, subscription_id, path, batch, collection_id)


def build(name, subscription_id, path, rows, collection_id):
    msg = telemetry_pb2.Telemetry(
        node_id_str=name,
        subscription_id_str=str(subscription_id),
        encoding_path=path,
        collection_id=collection_id,
        msg_timestamp=int(time.time() * 1000),
    )
    msg.data_gpbkv.extend(rows)
    return mdt_dialout_pb2.MdtDialoutArgs(ReqId=collection_id, data=msg.SerializeToString())


def stream(args):
    network = Network(args)
    collection_id = 1000
    cycle = 0
    while True:
        started = time.time()
        now_ms = int(started * 1000)
        if cycle % args.ap_every == 0:
            collection_id += 1
            yield from messages(args.name, args.ap_sub, AP_PATH, network.ap_rows(now_ms), args.rows_per_message, collection_id)
        collection_id += 1
        sent = 0
        for message in messages(args.name, args.client_sub, CLIENT_PATH, network.client_rows(now_ms), args.rows_per_message, collection_id):
            sent += 1
            yield message
        print(f"cycle {cycle + 1}: {len(network.clients)} clients, {len(network.aps)} access points, {sent} client messages", flush=True)
        cycle += 1
        if args.cycles and cycle >= args.cycles:
            time.sleep(1)
            return
        network.step()
        time.sleep(max(0.0, args.period - (time.time() - started)))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", default="127.0.0.1:57500", help="receiver host:port")
    parser.add_argument("--name", default="sim-wlc-01", help="controller host name")
    parser.add_argument("--aps", type=int, default=40)
    parser.add_argument("--clients", type=int, default=600)
    parser.add_argument("--period", type=float, default=30, help="seconds between client updates")
    parser.add_argument("--ap-every", type=int, default=5, help="send the access point list every N client updates")
    parser.add_argument("--cycles", type=int, default=0, help="stop after N client updates (0 = run until stopped)")
    parser.add_argument("--rows-per-message", type=int, default=50)
    parser.add_argument("--client-sub", type=int, default=2101)
    parser.add_argument("--ap-sub", type=int, default=2102)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()

    channel = grpc.insecure_channel(args.target, options=[("grpc.max_send_message_length", -1)])
    call = channel.stream_stream(
        "/mdt_dialout.gRPCMdtDialout/MdtDialout",
        request_serializer=mdt_dialout_pb2.MdtDialoutArgs.SerializeToString,
        response_deserializer=mdt_dialout_pb2.MdtDialoutArgs.FromString,
    )
    print(f"sending telemetry as {args.name} to {args.target}", flush=True)
    try:
        for _ in call(stream(args)):
            pass
    except KeyboardInterrupt:
        pass
    except grpc.RpcError as exc:
        raise SystemExit(f"receiver closed the stream: {exc.code().name} {exc.details()}")


if __name__ == "__main__":
    main()
