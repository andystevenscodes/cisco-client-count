# cisco-client-count

Counts the Wi-Fi clients connected to each access point of a Cisco Catalyst 9800
wireless controller, from the telemetry the controller streams itself.

The controller pushes its client table to this tool (model-driven telemetry,
gRPC dial-out). The tool turns every update into one number per access point,
stores it in PostgreSQL and makes it available to Power BI. It runs as two Docker
containers on an Ubuntu server and has a web interface that shows

- the **count** per access point, live and over time,
- the **raw data** exactly as the controller sent it,
- the **translation** from raw rows to a number, step by step, so every count can be checked by hand,
- the **configuration to paste on the controller**, generated for your addresses.

Typical use: occupancy of buildings, floors and rooms, based on the number of
connected devices per access point.

```
 Catalyst 9800                      this server (Docker)                     reporting
┌──────────────┐  gRPC dial-out   ┌───────────────────────────────┐
│ telemetry    │ ───────────────► │ app   receiver → count → web  │ ──► web interface :8080
│ subscription │   TCP 57500      │ db    PostgreSQL              │ ──► Power BI (CSV/JSON or SQL)
└──────────────┘                  └───────────────────────────────┘
```

## Contents

- [Requirements](#requirements)
- [Install on Ubuntu](#install-on-ubuntu)
- [Configure the controller](#configure-the-controller)
- [The web interface](#the-web-interface)
- [How a count is made](#how-a-count-is-made)
- [Filter and groups](#filter-and-groups)
- [Power BI](#power-bi)
- [Settings](#settings)
- [Operating it](#operating-it)
- [Try it without a controller](#try-it-without-a-controller)
- [Security and privacy](#security-and-privacy)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [License](#license)

More detail lives in [docs/](docs/):
[install](docs/install-ubuntu.md) ·
[controller setup](docs/wlc-setup.md) ·
[how counting works](docs/how-counting-works.md) ·
[Power BI](docs/powerbi.md)

## Requirements

| | |
|---|---|
| Controller | Cisco Catalyst 9800 (IOS XE 17.x), NETCONF/YANG enabled |
| Server | Ubuntu Server 22.04 or 24.04, 2 vCPU, 4 GB RAM, disk according to [retention](docs/how-counting-works.md#how-much-is-stored) |
| Software | Docker Engine with the Compose plugin, git |
| Network | controller → server on TCP 57500; your browser and Power BI → server on TCP 8080 |

## Install on Ubuntu

The short version. [docs/install-ubuntu.md](docs/install-ubuntu.md) has every step
including Docker itself, the firewall and https.

```bash
sudo git clone https://github.com/andystevenscodes/cisco-client-count.git /opt/cisco-client-count
```

```bash
cd /opt/cisco-client-count
```

Create `.env` with generated passwords. The script prints the web password once:

```bash
sudo ./scripts/init-env.sh
```

Build and start:

```bash
sudo docker compose up -d --build
```

Check that both containers are healthy:

```bash
sudo docker compose ps
```

Open `http://<server-ip>:8080` and sign in with the password from the previous step
(it is also in `.env` as `ADMIN_PASSWORD`).

The containers restart on their own after a reboot.

## Configure the controller

Open **WLC setup** in the web interface, fill in the address of this server and
copy the generated configuration. It looks like this:

```
configure terminal
telemetry receiver protocol CLIENT_COUNT
 host ip-address 192.0.2.10 57500
 protocol grpc-tcp
!
telemetry ietf subscription 2101
 encoding encode-kvgpb
 filter xpath /wireless-client-oper:client-oper-data/common-oper-data
 source-address 192.0.2.1
 receiver-type protocol
 stream yang-push
 update-policy periodic 30000
 receiver name CLIENT_COUNT
!
telemetry ietf subscription 2102
 encoding encode-kvgpb
 filter xpath /wireless-access-point-oper:access-point-oper-data/capwap-data
 source-address 192.0.2.1
 receiver-type protocol
 stream yang-push
 update-policy periodic 90000
 receiver name CLIENT_COUNT
!
end
```

`192.0.2.10` is this server, `192.0.2.1` the address the controller sends from.
`periodic` is in hundredths of a second: `30000` is every 5 minutes.

Check on the controller:

```
show telemetry ietf subscription 2101 detail
show telemetry ietf subscription 2101 receiver
```

The subscription must be `Valid` and the receiver `Connected`. The first update
then appears under **Raw data** within one period.

Details, the syntax for older releases and removal: [docs/wlc-setup.md](docs/wlc-setup.md).

## The web interface

| Page | What it shows |
|---|---|
| **Dashboard** | Connected clients now, the trend, the count per access point (click one for its history), and the state of every telemetry stream. A filter on access point name, location, site tag or controller narrows all of it, and can be saved as a group. |
| **Raw data** | Every message as received, newest first: decoded to JSON or as protobuf text. Live, with pause. |
| **Translation** | One update, followed from the messages that arrived, through the rule, to the number per access point and the rows written to the database. |
| **WLC setup** | The configuration to paste on the controller, plus the commands to verify and remove it. |
| **Power BI** | The export URLs and the database connection for reporting. |

## How a count is made

1. The controller sends its whole client table once per period, as a burst of
   messages. The burst is one *update*; it is complete when the stream has been
   quiet for 5 seconds.
2. Every row is one client. Four fields are read: `client-mac`, `ap-name`,
   `co-state` and `wlan-id`.
3. A client counts for the access point in `ap-name` when its `co-state` is
   `client-status-run`, the state of a fully connected client. A MAC address that
   appears twice in one update counts once.
4. Access points from the access point list that have no clients are stored with 0,
   so "empty" and "no data" are not the same thing in a report.
5. One row per access point is written with the time of the update.

The **Translation** page shows these steps with the real data of any recent update.
[docs/how-counting-works.md](docs/how-counting-works.md) describes the edge cases.

## Filter and groups

Type `AP-FORUM-2*` in the filter on the dashboard and the total, the chart and
the table show only the access points whose name starts with that. With names
that carry building and floor, that is the number of clients on a floor. Save
the filter as a group ("Forum floor 2") to see all floors side by side and to
get them in Power BI. `*` is any text, `?` one character; text without a
wildcard may appear anywhere in the name, location, site tag or controller.
More in [docs/how-counting-works.md](docs/how-counting-works.md#filters-and-groups).

## Power BI

Two ways in, both read the same datasets:

| Dataset | One row is | Use it for |
|---|---|---|
| `ap-counts` | one access point at one count | detail of recent days |
| `ap-counts-hourly` | one access point in one hour (average, min, max) | occupancy over weeks and months |
| `access-points` | one access point (location, tags, model) | dimension table |
| `group-counts` | one group (for example a floor) at one count | clients per floor over time |
| `ap-groups` | one access point in one group | slicing the other tables by group |

**Web export** (no driver needed): *Get data → Web*, URL
`http://<server>:8080/export/ap-counts-hourly.csv?days=365`, authentication
*Basic* with user `powerbi` and `EXPORT_TOKEN` as password.

**PostgreSQL**: *Get data → PostgreSQL database*, server `<server>:5432`, database
`ccc`, user `powerbi` (read-only), views `powerbi_ap_counts`,
`powerbi_ap_counts_hourly`, `powerbi_access_points`, `powerbi_group_counts`,
`powerbi_ap_groups`.

Step by step, with parameters and an example model: [docs/powerbi.md](docs/powerbi.md).

## Settings

All settings are in `.env`; [.env.example](.env.example) documents each one.
After a change: `sudo docker compose up -d`.

| Setting | Default | Meaning |
|---|---|---|
| `ADMIN_PASSWORD` | required | Password of the web interface |
| `POSTGRES_PASSWORD` | required | Password of the database owner |
| `SECRET_KEY` | required | Signs the login cookie |
| `EXPORT_TOKEN` | empty | Password for `/export/...`; empty disables unattended export |
| `POWERBI_DB_PASSWORD` | empty | Creates the read-only database user `powerbi` |
| `DB_BIND` | `127.0.0.1` | `0.0.0.0` publishes PostgreSQL for Power BI |
| `TELEMETRY_PORT` | `57500` | Port the controller sends to |
| `WEB_PORT` | `8080` | Port of the web interface |
| `ALLOWED_WLC_SOURCES` | empty | Addresses or networks that may send telemetry |
| `DOCKER_SUBNET` | `192.168.237.0/24` | Address range of the project's internal Docker network |
| `TZ` | `Europe/Amsterdam` | Time zone of the `*_local` columns and the logs |
| `COUNT_STATES` | `client-status-run` | Client states that count as connected |
| `CYCLE_GAP_SECONDS` | `5` | Quiet time that ends an update |
| `ANONYMIZE_CLIENTS` | `false` | Replace client MAC addresses and hide user names before a message is kept |
| `RETENTION_DAYS` | `400` | Days the per-count rows are kept (hourly rows are kept indefinitely) |
| `RAW_BUFFER_MESSAGES` | `500` | Messages kept in memory for the Raw data page |

## Operating it

Logs:

```bash
sudo docker compose logs -f app
```

Update to the newest version:

```bash
cd /opt/cisco-client-count && sudo git pull && sudo docker compose up -d --build
```

Backup of the database:

```bash
sudo docker compose exec -T db pg_dump -U ccc ccc | gzip > ccc-$(date +%F).sql.gz
```

Stop, and start again:

```bash
sudo docker compose down
```

```bash
sudo docker compose up -d
```

The data is in `./data/postgres` and survives both.

## Try it without a controller

A simulator is included. It connects the same way a controller does and sends
made-up clients and access points:

```bash
sudo docker compose exec app python tools/simulate_wlc.py --aps 40 --clients 600 --period 30
```

Stop it with Ctrl-C. Its data is stored under the controller name `sim-wlc-01`;
remove it afterwards with:

```bash
sudo docker compose exec db psql -U ccc ccc -c "DELETE FROM ap_client_count WHERE wlc='sim-wlc-01'; DELETE FROM ap_client_count_hourly WHERE wlc='sim-wlc-01'; DELETE FROM ap WHERE wlc='sim-wlc-01'; DELETE FROM cycle WHERE wlc='sim-wlc-01';"
```

## Security and privacy

- Telemetry arrives over plain gRPC (`grpc-tcp`), as is usual on a management
  network. Limit who can reach port 57500 with the firewall and with
  `ALLOWED_WLC_SOURCES`.
- The web interface speaks http. Put a reverse proxy with a certificate in front
  of it when it is reached from outside a trusted network
  ([example](docs/install-ubuntu.md#https)), and set `SESSION_COOKIE_SECURE=true`.
- The client table contains MAC addresses and user names. Only the **count** is
  stored in the database. The raw messages, including those fields, are kept in
  memory for the Raw data and Translation pages and are gone after a restart.
  With `ANONYMIZE_CLIENTS=true` the MAC addresses are replaced by a pseudonym and
  user names are hidden before a message is kept at all; the count is unaffected.
- The `powerbi` database user can read the reporting views and nothing else.

## Troubleshooting

| Symptom | Look at |
|---|---|
| Nothing under Raw data | On the controller: `show telemetry ietf subscription 2101 receiver`. `Connecting` means the server is not reachable on TCP 57500 (firewall, routing, wrong source address). |
| Receiver stays `Connecting` although the port is open | `tcpdump -ni any tcp port 57500` on the server shows the controller's SYN packets without an answer: a Docker network on the server overlaps with the controller's address. Check with `ip route get <controller-ip>`; if it says `dev br-...` or `dev docker0`, see [the note on address ranges](docs/install-ubuntu.md#docker-address-range). |
| Subscription `Invalid` | The YANG interface is not running (`show netconf-yang status`), or the xpath has a typo. |
| Messages arrive, no count | Dashboard → Telemetry streams: the path must be `…client-oper-data/common-oper-data`. Other paths are received but not counted. |
| Counts lower than the controller shows | Translation → step 3 lists the clients per state. Only `client-status-run` counts by default. |
| An access point is missing | Without the access point list (subscription 2102) an access point only appears once it has had a client. |
| "connection refused: source not in ALLOWED_WLC_SOURCES" under Raw data | Add the controller's source address to `ALLOWED_WLC_SOURCES`. |
| Port 57500 already in use | Another collector runs on this host. Set `TELEMETRY_PORT` to a free port and use that port on the controller. |

## Development

```
ccc/receiver.py    gRPC dial-out server
ccc/decode.py      kvGPB telemetry to Python
ccc/pipeline.py    messages → updates → translation → store; raw buffer
ccc/translate.py   the counting rule (pure functions)
ccc/patterns.py    the filter syntax of the dashboard, groups and export
ccc/db.py          PostgreSQL schema, writes, reporting views
ccc/web.py         web interface, API, export
proto/             Cisco telemetry .proto files (Apache-2.0, see proto/LICENSE)
tools/             controller simulator
tests/             unit tests of the counting path
```

Run the tests in the image:

```bash
docker run --rm -u root -v "$PWD/tests":/app/tests cisco-client-count:local sh -c "pip install -q pytest && python -m pytest -q tests"
```

## License

[MIT](LICENSE). The two files in `proto/ccc/pb/` are Cisco's telemetry
definitions and stay under their own Apache-2.0 license ([proto/LICENSE](proto/LICENSE)).
