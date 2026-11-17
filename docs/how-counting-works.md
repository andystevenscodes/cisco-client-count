# How counting works

Everything described here is visible in the web interface: the input under
**Raw data**, the steps under **Translation**. The rule itself is about forty
lines of code in [ccc/translate.py](../ccc/translate.py).

## From stream to number

### 1. Messages

The controller sends `Telemetry` messages (Cisco kvGPB). Each has a header and a
list of rows:

```json
{
  "node_id": "wlc-01",
  "subscription_id": "2101",
  "encoding_path": "Cisco-IOS-XE-wireless-client-oper:client-oper-data/common-oper-data",
  "collection_id": 48213,
  "msg_timestamp": 1794911625343,
  "rows": [
    {
      "timestamp": 1794911625343,
      "keys": { "client-mac": "3c:22:fb:10:9a:01" },
      "content": {
        "ap-name": "AP-FORUM-2010",
        "ms-ap-slot-id": 1,
        "wlan-id": 1,
        "co-state": "client-status-run",
        "username": "..."
      }
    }
  ]
}
```

Every message is put in the raw buffer unchanged. That is all that happens to a
message whose path is not one of the two the tool counts from.

### 2. Updates

With a periodic subscription the controller sends the whole table once per
period, spread over many messages. The tool collects the messages of one stream
(controller + subscription + path) into an **update** (in the code: a cycle).
An update is complete when that stream has been quiet for `CYCLE_GAP_SECONDS`
(default 5).

The time of an update is the moment its first message arrived, on the server's
clock, rounded down to the second.

### 3. The rule

Each row of the client table is one client. For each row:

| Field | Read from | Used for |
|---|---|---|
| `client-mac` | `keys` | identity within the update |
| `ap-name` | `content` | the access point the client counts for |
| `co-state` | `content` | whether the client counts |
| `wlan-id` | `content` | shown on the Translation page |

A client is **counted** when

- `ap-name` is not empty, and
- `co-state` is one of `COUNT_STATES` (default: `client-status-run`).

`client-status-run` is the state of a client that completed association,
authentication and address learning. Clients still on their way there
(`client-status-authenticating`, `client-status-ip-learning`,
`client-status-webauth-required` and so on) are listed on the Translation page
with the reason they were left out. To count those too, add their states to
`COUNT_STATES`, comma separated.

A MAC address that appears more than once in one update counts once; the last row
wins. This also keeps the count right if two bursts were ever to run together.

The number for an access point is the number of counted clients naming it.

### 4. Zero counts

A client table only names access points that have clients. To tell "empty" from
"no data", the tool also writes a row with `0` for access points it knows about:

- with the access point list (subscription 2102): every access point on the most
  recent list, when that list is at most an hour old;
- without it: every access point that had a client in the last 7 days.

### 5. Storage

For each update, one row per access point goes into `ap_client_count`
(`ts, wlc, ap_name, clients`), the hourly table is updated, and one row
describing the update itself goes into `cycle` (messages, rows, counted,
not counted, duration).

## What is stored where

| Table | Content | Kept |
|---|---|---|
| `ap_client_count` | one row per access point per update | `RETENTION_DAYS` (400) |
| `ap_client_count_hourly` | per access point per hour: samples, sum, min, max | indefinitely |
| `ap` | access points: MAC, IP, location, site/policy/RF tag, model, first and last seen | indefinitely |
| `cycle` | one row per update, for the dashboard and for auditing | `RETENTION_DAYS` |

Reporting reads the views `powerbi_ap_counts`, `powerbi_ap_counts_hourly` and
`powerbi_access_points`; see [powerbi.md](powerbi.md).

Client MAC addresses and user names are never written to the database.

## How much is stored

Rows per day in `ap_client_count` = access points × updates per day.

| Access points | Every 5 min | Every 1 min |
|---|---|---|
| 100 | 28,800 | 144,000 |
| 1,000 | 288,000 | 1,440,000 |
| 3,000 | 864,000 | 4,320,000 |

Count on roughly 150 to 200 bytes per row including the two indexes: 1,000 access
points at 5 minutes is 15 to 20 GB per year. The hourly table is small: 8,760 rows per
access point per year. Lower `RETENTION_DAYS` if the detail is only needed for
recent weeks; the hourly history stays.

## Accuracy

- A count is a snapshot at the moment of the update. A client that connects and
  leaves between two updates is not seen.
- A device is a client, not a person. Phones, laptops and headless devices all
  count; someone with two devices counts twice. For occupancy, calibrate against
  a known headcount or compare relative levels.
- The number matches the controller's own client table at that moment for clients
  in run state. It can be lower than totals that include clients in other states.
- Roaming between two updates moves a client from one access point to the next;
  it is never counted on both in the same update.
- If the controller stops sending, no rows are written. Gaps in the data are
  gaps in the telemetry, not zeros.

## Not counted, by design

- Streams with another path: received and shown, never stored.
- Per-SSID or per-band splits. The fields (`wlan-id`, `ms-ap-slot-id`) are in the
  rows and visible on the Translation page, but the stored number is per access point.
