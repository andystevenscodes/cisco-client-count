# Power BI

The counts can be read in two ways. Both give the same three datasets.

| | Web export | PostgreSQL |
|---|---|---|
| Driver on the Power BI machine | none | PostgreSQL connector (included in recent Power BI Desktop) |
| Port | 8080 (web) | 5432 |
| Credentials | export token | database user `powerbi` |
| Good for | scheduled imports, Excel, scripts | large histories, DirectQuery, custom SQL |

## The datasets

### ap-counts (`powerbi_ap_counts`)

One row per access point per update. Kept for `RETENTION_DAYS`.

| Column | Type | |
|---|---|---|
| `ts_utc` | timestamp | time of the update, UTC |
| `ts_local` | timestamp | the same moment in the server's time zone (`TZ`) |
| `wlc` | text | host name of the controller |
| `ap_name` | text | access point |
| `location` | text | location string configured on the access point |
| `site_tag` | text | site tag |
| `policy_tag` | text | policy tag |
| `clients` | integer | connected clients at that moment |

### ap-counts-hourly (`powerbi_ap_counts_hourly`)

One row per access point per hour. Kept indefinitely.

| Column | Type | |
|---|---|---|
| `hour_utc`, `hour_local` | timestamp | start of the hour |
| `wlc`, `ap_name`, `location`, `site_tag`, `policy_tag` | text | as above |
| `clients_avg` | decimal | average of the counts in that hour |
| `clients_min`, `clients_max` | integer | lowest and highest count in that hour |
| `samples` | integer | number of updates in that hour |

### access-points (`powerbi_access_points`)

One row per access point: `wlc`, `ap_name`, `wtp_mac`, `ip`, `location`,
`site_tag`, `policy_tag`, `rf_tag`, `model`, `first_seen_utc`, `last_seen_utc`.

Location and tags come from the access point list (subscription 2102). They are
empty when that subscription is not configured.

## Option A: web export

Set `EXPORT_TOKEN` in `.env` (the install script generates one) and restart.

In Power BI Desktop:

1. **Get data → Web**.
2. URL, for example:

   ```
   http://<server>:8080/export/ap-counts-hourly.csv?days=365
   ```

3. When asked how to sign in, choose **Basic**. User name `powerbi` (any name
   works), password: the export token. Apply it to the full URL of the server.
4. Power BI detects the columns; check that the time columns are typed as
   date/time and load.

Repeat for `access-points.csv`, and for `ap-counts.csv` if you want the detail.

### URLs

```
/export/ap-counts.csv
/export/ap-counts-hourly.csv
/export/access-points.csv
```

Replace `.csv` by `.json` for JSON.

### Parameters

| Parameter | Example | Meaning |
|---|---|---|
| `days` | `days=30` | the last N days. Default 30 for `ap-counts`, 365 for `ap-counts-hourly` |
| `from` | `from=2026-01-01` | start, in local time; `2026-01-01T08:00` also works |
| `to` | `to=2026-02-01` | end, exclusive |
| `wlc` | `wlc=wlc-01` | one controller |

`from` wins over `days`.

### Test it

```bash
curl -u powerbi:YOUR_EXPORT_TOKEN "http://<server>:8080/export/ap-counts.csv?days=1" | head
```

A token can also be sent as `Authorization: Bearer <token>`.

### Scheduled refresh in the Power BI service

The service cannot reach a server on a private network by itself. Install the
on-premises data gateway on a machine that can reach the server, and map the web
data source to it with the same Basic credentials.

## Option B: PostgreSQL

In `.env`:

```
POWERBI_DB_PASSWORD=<a password>
DB_BIND=0.0.0.0
```

```bash
sudo docker compose up -d
```

At start the tool creates the database user `powerbi`. It can read the three
`powerbi_*` views and nothing else. Open port 5432 only to the machines that
need it.

In Power BI Desktop:

1. **Get data → PostgreSQL database**.
2. Server `<server>:5432`, database `ccc`.
3. Import or DirectQuery. For long histories, DirectQuery or an import of
   `powerbi_ap_counts_hourly` keeps the model small.
4. Sign in with **Database** credentials: user `powerbi`, the password from `.env`.
5. Select the three `powerbi_*` views.

The connection is not encrypted by default. If Power BI refuses, untick
"Encrypt connection" in the data source settings.

## A simple model

- `powerbi_access_points` as the dimension table.
- Relate it to the counts on `wlc` + `ap_name`. Power BI relates on one column,
  so add a key column to both tables in Power Query:

  ```
  = [wlc] & "|" & [ap_name]
  ```

- Or skip the dimension table: `location`, `site_tag` and `policy_tag` are already
  on every count row.

Measures that work on the hourly table:

```
Clients (avg) = SUM ( 'ap-counts-hourly'[clients_avg] )
Clients (peak) = SUM ( 'ap-counts-hourly'[clients_max] )
```

Put `hour_local` on the axis and `location` or `site_tag` in the legend to get
occupancy per building or floor. Summing `clients_avg` over access points gives
the average number of clients in a location during that hour; summing
`clients_max` gives an upper bound for the peak, since the access points need not
peak at the same moment.

## Example queries

Occupancy per location per hour, last week:

```sql
SELECT hour_local, location, sum(clients_avg) AS clients
  FROM powerbi_ap_counts_hourly
 WHERE hour_local >= now() - interval '7 days'
 GROUP BY hour_local, location
 ORDER BY hour_local, location;
```

Busiest hour per access point this month:

```sql
SELECT DISTINCT ON (wlc, ap_name) wlc, ap_name, hour_local, clients_max
  FROM powerbi_ap_counts_hourly
 WHERE hour_local >= date_trunc('month', now())
 ORDER BY wlc, ap_name, clients_max DESC;
```
