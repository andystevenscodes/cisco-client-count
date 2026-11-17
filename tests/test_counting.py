"""The path from telemetry bytes to a count, without a database or a network."""

from ccc import decode, patterns, translate
from ccc.config import Config
from ccc.pb import telemetry_pb2
from ccc.pipeline import Pipeline

CLIENT_PATH = "Cisco-IOS-XE-wireless-client-oper:client-oper-data/common-oper-data"
AP_PATH = "Cisco-IOS-XE-wireless-access-point-oper:access-point-oper-data/capwap-data"


def _field(parent, name, value=None):
    f = parent.fields.add()
    f.name = name
    if isinstance(value, int):
        f.uint32_value = value
    elif isinstance(value, str):
        f.string_value = value
    return f


def client_message(clients, wlc="wlc-1", sub="2101", collection_id=1):
    """clients: (mac, ap, state)"""
    msg = telemetry_pb2.Telemetry(
        node_id_str=wlc, subscription_id_str=sub, encoding_path=CLIENT_PATH,
        collection_id=collection_id, msg_timestamp=1_700_000_000_000,
    )
    for mac, ap, state in clients:
        row = msg.data_gpbkv.add()
        _field(_field(row, "keys"), "client-mac", mac)
        content = _field(row, "content")
        _field(content, "ap-name", ap)
        _field(content, "co-state", state)
        _field(content, "wlan-id", 1)
        _field(content, "username", "alice")
        policy = _field(content, "wlan-policy")
        _field(policy, "current-switching-mode", "local")
    return msg.SerializeToString()


def ap_message(aps, wlc="wlc-1", sub="2102"):
    msg = telemetry_pb2.Telemetry(node_id_str=wlc, subscription_id_str=sub, encoding_path=AP_PATH)
    for index, (name, location) in enumerate(aps):
        row = msg.data_gpbkv.add()
        _field(_field(row, "keys"), "wtp-mac", f"00:00:00:00:00:{index:02x}")
        content = _field(row, "content")
        _field(content, "name", name)
        _field(_field(content, "ap-location"), "location", location)
        tags = _field(content, "tag-info")
        _field(_field(tags, "site-tag"), "site-tag-name", "ST-A")
    return msg.SerializeToString()


class Clock:
    def __init__(self):
        self.now = 1_700_000_000.0

    def __call__(self):
        return self.now


class FakeStore:
    def __init__(self):
        self.client_cycles, self.inventories = [], []

    def save_client_cycle(self, cycle):
        self.client_cycles.append(cycle)

    def save_inventory(self, cycle):
        self.inventories.append(cycle)


def make_pipeline(**env):
    clock, store = Clock(), FakeStore()
    return Pipeline(Config(env), store, clock), clock, store


RUN = "client-status-run"


def test_decode_keeps_yang_names_and_nesting():
    msg = decode.parse(client_message([("aa:aa:aa:aa:aa:01", "AP-1", RUN)]))
    rows = decode.rows(msg)
    assert rows[0]["keys"] == {"client-mac": "aa:aa:aa:aa:aa:01"}
    assert rows[0]["content"]["ap-name"] == "AP-1"
    assert rows[0]["content"]["wlan-policy"] == {"current-switching-mode": "local"}
    assert decode.header(msg)["encoding_path"] == CLIENT_PATH


def test_only_run_state_counts():
    rows = decode.rows(decode.parse(client_message([
        ("aa:aa:aa:aa:aa:01", "AP-1", RUN),
        ("aa:aa:aa:aa:aa:02", "AP-1", RUN),
        ("aa:aa:aa:aa:aa:03", "AP-1", "client-status-authenticating"),
        ("aa:aa:aa:aa:aa:04", "AP-2", RUN),
        ("aa:aa:aa:aa:aa:05", "", RUN),
    ])))
    result = translate.translate_clients(rows)
    assert result["counts"] == {"AP-1": 2, "AP-2": 1}
    assert result["counted"] == 3
    assert result["not_counted"] == 2
    assert result["skipped"] == {
        "co-state is client-status-authenticating": 1,
        "no ap-name in the row": 1,
    }


def test_a_repeated_mac_counts_once_and_the_last_row_wins():
    rows = decode.rows(decode.parse(client_message([
        ("aa:aa:aa:aa:aa:01", "AP-1", RUN),
        ("AA:AA:AA:AA:AA:01", "AP-2", RUN),
    ])))
    result = translate.translate_clients(rows)
    assert result["counts"] == {"AP-2": 1}
    assert result["duplicates"] == 1
    assert result["rows"] == 2 and result["clients"] == 1


def test_which_streams_are_translated():
    assert translate.kind_for(CLIENT_PATH) == "clients"
    assert translate.kind_for(AP_PATH) == "inventory"
    assert translate.kind_for("Cisco-IOS-XE-process-cpu-oper:cpu-usage/cpu-utilization") is None


def test_a_cycle_closes_when_the_stream_goes_quiet():
    pipeline, clock, store = make_pipeline()
    pipeline.ingest("10.0.0.1", client_message([("aa:aa:aa:aa:aa:01", "AP-1", RUN)]))
    clock.now += 1
    pipeline.ingest("10.0.0.1", client_message([("aa:aa:aa:aa:aa:02", "AP-1", RUN)], collection_id=2))
    clock.now += 2
    assert pipeline.sweep() == []  # still within the gap: the burst may continue
    clock.now += 5
    closed = pipeline.sweep()
    assert len(closed) == 1
    assert closed[0]["counts"] == {"AP-1": 2}
    assert closed[0]["messages"] == 2
    assert closed[0]["stored"] is True
    assert store.client_cycles[0]["counts"] == {"AP-1": 2}


def test_two_updates_are_two_counts():
    pipeline, clock, store = make_pipeline()
    pipeline.ingest("10.0.0.1", client_message([("aa:aa:aa:aa:aa:01", "AP-1", RUN)]))
    clock.now += 10
    pipeline.sweep()
    clock.now += 50
    pipeline.ingest("10.0.0.1", client_message([("aa:aa:aa:aa:aa:01", "AP-2", RUN)]))
    clock.now += 10
    pipeline.sweep()
    assert [c["counts"] for c in store.client_cycles] == [{"AP-1": 1}, {"AP-2": 1, "AP-1": 0}]
    # AP-1 had a client a minute ago, so it is written as 0 rather than left out.
    assert store.client_cycles[1]["zero_filled"] == ["AP-1"]
    assert pipeline.status()["streams"][0]["period"] == 60.0


def test_access_point_list_gives_zero_counts():
    pipeline, clock, store = make_pipeline()
    pipeline.ingest("10.0.0.1", ap_message([("AP-1", "Hall"), ("AP-2", "Hall"), ("AP-3", "Lab")]))
    pipeline.ingest("10.0.0.1", client_message([("aa:aa:aa:aa:aa:01", "AP-1", RUN)]))
    clock.now += 10
    pipeline.sweep()
    cycle = store.client_cycles[0]
    assert cycle["counts"] == {"AP-1": 1, "AP-2": 0, "AP-3": 0}
    assert cycle["zero_fill_source"] == "inventory"
    assert store.inventories[0]["aps"]["AP-3"]["location"] == "Lab"
    assert store.inventories[0]["aps"]["AP-1"]["site_tag"] == "ST-A"


def test_controllers_are_counted_apart():
    pipeline, clock, store = make_pipeline()
    pipeline.ingest("10.0.0.1", client_message([("aa:aa:aa:aa:aa:01", "AP-1", RUN)], wlc="wlc-1"))
    pipeline.ingest("10.0.0.2", client_message([("aa:aa:aa:aa:aa:02", "AP-1", RUN)], wlc="wlc-2"))
    clock.now += 10
    pipeline.sweep()
    assert sorted(c["wlc"] for c in store.client_cycles) == ["wlc-1", "wlc-2"]
    assert all(c["counts"] == {"AP-1": 1} for c in store.client_cycles)


def test_raw_buffer_keeps_the_message_as_sent():
    pipeline, _, _ = make_pipeline()
    data = client_message([("aa:aa:aa:aa:aa:01", "AP-1", RUN)])
    pipeline.ingest("10.0.0.1", data)
    listed = pipeline.raw_list()
    assert listed[0]["rows"] == 1 and listed[0]["bytes"] == len(data)
    detail = pipeline.raw_get(listed[0]["seq"])
    assert detail["decoded"]["rows"][0]["keys"]["client-mac"] == "aa:aa:aa:aa:aa:01"
    assert "client-mac" in detail["protobuf_text"]


def test_anonymize_hides_identity_but_not_the_count():
    pipeline, clock, store = make_pipeline(ANONYMIZE_CLIENTS="true", ANONYMIZE_SALT="s")
    pipeline.ingest("10.0.0.1", client_message([
        ("aa:aa:aa:aa:aa:01", "AP-1", RUN), ("aa:aa:aa:aa:aa:02", "AP-1", RUN),
    ]))
    clock.now += 10
    pipeline.sweep()
    detail = pipeline.raw_get(1)
    row = detail["decoded"]["rows"][0]
    assert row["keys"]["client-mac"].startswith("anon-")
    assert row["content"]["username"] == "(hidden)"
    assert "aa:aa:aa:aa:aa:01" not in detail["protobuf_text"]
    assert store.client_cycles[0]["counts"] == {"AP-1": 2}


def test_a_message_that_is_not_telemetry_is_reported_not_fatal():
    pipeline, _, _ = make_pipeline()
    pipeline.ingest("10.0.0.1", b"\xff\xff\xff\xff not protobuf")
    assert pipeline.status()["messages"] == 0
    assert pipeline.status()["errors"]


def test_source_filter():
    config = Config({"ALLOWED_WLC_SOURCES": "10.0.0.0/24, 192.0.2.7"})
    assert config.source_allowed("10.0.0.9")
    assert config.source_allowed("192.0.2.7")
    assert not config.source_allowed("192.0.2.8")
    assert Config({}).source_allowed("203.0.113.1")


def test_filter_syntax():
    like = patterns.like_patterns
    assert like("AP-FORUM-2*") == ["AP-FORUM-2%"]          # starts with
    assert like("*-2??") == ["%-2__"]                      # wildcards anchor the match
    assert like("forum floor 2") == ["%forum floor 2%"]    # no wildcard: anywhere
    assert like(" AP-A-1* ,AP-B-1*,, ") == ["AP-A-1%", "AP-B-1%"]
    assert like("") == [] and like("  ,  ") == []
    # characters that mean something to LIKE are taken literally
    assert like("AP_50%*") == ["AP\\_50\\%%"]
    assert patterns.normalize("  AP-A-1*,AP-B-1* ") == "AP-A-1*, AP-B-1*"


def test_filter_looks_at_name_location_tag_and_controller():
    sql = patterns.match_sql("a", "%s")
    for column in ("a.ap_name", "a.location", "a.site_tag", "a.wlc"):
        assert f"{column} ILIKE ANY (%s)" in sql
    assert patterns.match_sql("", "%s", ["ap_name", "wlc"]) == "(ap_name ILIKE ANY (%s) OR wlc ILIKE ANY (%s))"


def test_a_source_that_only_sends_empty_tables_is_ignored():
    pipeline, clock, store = make_pipeline()
    pipeline.ingest("10.0.0.9", client_message([], wlc="switch-1"))
    pipeline.ingest("10.0.0.9", ap_message([], wlc="switch-1"))
    pipeline.ingest("10.0.0.1", client_message([("aa:aa:aa:aa:aa:01", "AP-1", RUN)], wlc="wlc-1"))
    clock.now += 10
    closed = pipeline.sweep()
    assert len(closed) == 3
    # received and visible as raw data and as a stream ...
    assert pipeline.status()["messages"] == 3
    streams = {(s["wlc"], s["kind"]): s for s in pipeline.status()["streams"]}
    assert streams[("switch-1", "clients")]["empty"] and streams[("switch-1", "inventory")]["empty"]
    assert not streams[("wlc-1", "clients")]["empty"]
    # ... but not stored and not offered on the Translation page
    assert [c["wlc"] for c in store.client_cycles] == ["wlc-1"]
    assert store.inventories == []
    assert [c["wlc"] for c in pipeline.cycles()] == ["wlc-1"]


def test_a_controller_without_clients_still_stores_zeros():
    pipeline, clock, store = make_pipeline()
    pipeline.ingest("10.0.0.1", ap_message([("AP-1", "Hall"), ("AP-2", "Hall")]))
    pipeline.ingest("10.0.0.1", client_message([]))
    clock.now += 10
    pipeline.sweep()
    assert store.client_cycles[0]["counts"] == {"AP-1": 0, "AP-2": 0}
    assert not pipeline.status()["streams"][0]["empty"]
