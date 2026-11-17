# Controller setup

The tool does not log in to the controller. The controller is told, once, to push
two tables to the tool. This is standard model-driven telemetry (gRPC dial-out,
kvGPB encoding), the same mechanism other collectors use, and it can run next to
existing subscriptions.

The **WLC setup** page of the web interface generates everything below for your
addresses, ready to copy.

## Prerequisites

- Catalyst 9800 on IOS XE 17.x.
- The YANG interface is running. Check:

  ```
  show netconf-yang status
  ```

  If it says `netconf-yang: disabled`, enable it first (this also opens NETCONF
  on TCP 830, so apply your usual access policy):

  ```
  configure terminal
  netconf-yang
  end
  ```

- The controller can reach the server on TCP 57500 (or the port in `TELEMETRY_PORT`).
- Two free subscription ids. See what is in use:

  ```
  show telemetry ietf subscription all
  ```

## Configuration

`192.0.2.10` is the server running this tool. `192.0.2.1` is the address the
controller sends from, usually the wireless management interface.

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

Save with `write memory` once it works.

### The two subscriptions

| Id | Table | One row is | Period | Used for |
|---|---|---|---|---|
| 2101 | `client-oper-data/common-oper-data` | one client | 5 minutes | the count |
| 2102 | `access-point-oper-data/capwap-data` | one joined access point | 15 minutes | names, location, tags, and zero counts |

Subscription 2102 is optional but recommended: without it an access point only
shows up once it has had a client, and it carries no location or tags.

### The period

`update-policy periodic` is in hundredths of a second.

| Every | Value | Rows stored per access point per day |
|---|---|---|
| 1 minute | `6000` | 1440 |
| 5 minutes | `30000` | 288 |
| 15 minutes | `90000` | 96 |

Five minutes is a good default for occupancy. A shorter period gives finer detail
and proportionally more rows; see
[how much is stored](how-counting-works.md#how-much-is-stored). Keep the period
at 30 seconds or more: an update must be finished well before the next one starts.

### Older releases: inline receiver

If the controller does not accept `telemetry receiver protocol`, put the receiver
inside each subscription instead:

```
telemetry ietf subscription 2101
 encoding encode-kvgpb
 filter xpath /wireless-client-oper:client-oper-data/common-oper-data
 source-address 192.0.2.1
 stream yang-push
 update-policy periodic 30000
 receiver ip address 192.0.2.10 57500 protocol grpc-tcp
```

The setup page has a switch for this form.

## Verify

```
show telemetry ietf subscription 2101 detail
show telemetry ietf subscription 2101 receiver
show telemetry connection all
```

Expected: subscription state `Valid`, receiver state `Connected`.

| State | Meaning |
|---|---|
| `Connecting` | The server does not answer on that address and port: firewall, routing, wrong port, or the wrong source address. |
| `Invalid` | The YANG interface is not running, or the xpath is misspelled. |
| `Connected`, but nothing under Raw data | The traffic reaches another host, or `ALLOWED_WLC_SOURCES` refuses this source (shown under Raw data → Receiver errors). |

In the tool, the first update appears under **Raw data** within one period, and
**Dashboard → Telemetry streams** lists both subscriptions with their measured period.

## High availability and several controllers

- An HA pair sends under one host name; nothing changes after a switchover.
- Any number of controllers can send to the same server. Each is stored under its
  own host name (`wlc` column). Use the same subscription ids on all of them or
  different ones, either works.

## Remove

```
configure terminal
no telemetry ietf subscription 2101
no telemetry ietf subscription 2102
no telemetry receiver protocol CLIENT_COUNT
end
```

## Other subscriptions

Anything else sent to the same port is received and visible under Raw data, which
is handy for looking at a YANG table before building on it. Only the two paths
above are translated and stored.
