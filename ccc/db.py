"""PostgreSQL storage.

Tables hold what the tool writes; the `powerbi_*` views are the stable surface
for reporting. Power BI reads the views, either straight from the database
(role `powerbi`, read-only) or through the CSV/JSON export of the web interface.
"""

import logging
import time
from datetime import datetime, timedelta, timezone

from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ccc.patterns import MATCH_COLUMNS, match_sql

log = logging.getLogger("ccc.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS ap (
    wlc            text NOT NULL,
    ap_name        text NOT NULL,
    wtp_mac        text NOT NULL DEFAULT '',
    ip             text NOT NULL DEFAULT '',
    location       text NOT NULL DEFAULT '',
    site_tag       text NOT NULL DEFAULT '',
    policy_tag     text NOT NULL DEFAULT '',
    rf_tag         text NOT NULL DEFAULT '',
    model          text NOT NULL DEFAULT '',
    first_seen     timestamptz NOT NULL,
    last_seen      timestamptz NOT NULL,
    last_inventory timestamptz,
    PRIMARY KEY (wlc, ap_name)
);

CREATE TABLE IF NOT EXISTS ap_client_count (
    ts      timestamptz NOT NULL,
    wlc     text NOT NULL,
    ap_name text NOT NULL,
    clients integer NOT NULL,
    PRIMARY KEY (ts, wlc, ap_name)
);
CREATE INDEX IF NOT EXISTS ap_client_count_ap_idx ON ap_client_count (wlc, ap_name, ts DESC);

CREATE TABLE IF NOT EXISTS ap_client_count_hourly (
    hour        timestamptz NOT NULL,
    wlc         text NOT NULL,
    ap_name     text NOT NULL,
    samples     integer NOT NULL,
    clients_sum bigint NOT NULL,
    clients_min integer NOT NULL,
    clients_max integer NOT NULL,
    PRIMARY KEY (hour, wlc, ap_name)
);

CREATE TABLE IF NOT EXISTS cycle (
    id                bigserial PRIMARY KEY,
    ts                timestamptz NOT NULL,
    wlc               text NOT NULL,
    kind              text NOT NULL,
    subscription_id   text NOT NULL,
    encoding_path     text NOT NULL,
    messages          integer NOT NULL,
    rows_received     integer NOT NULL,
    bytes_received    bigint NOT NULL,
    duration_ms       integer NOT NULL,
    duplicates        integer NOT NULL DEFAULT 0,
    clients_counted   integer NOT NULL DEFAULT 0,
    clients_skipped   integer NOT NULL DEFAULT 0,
    aps_with_clients  integer NOT NULL DEFAULT 0,
    aps_zero_filled   integer NOT NULL DEFAULT 0,
    aps_in_inventory  integer NOT NULL DEFAULT 0,
    wlc_msg_timestamp bigint NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS cycle_lookup_idx ON cycle (kind, wlc, ts DESC);

-- A group is a name for the access points that fit a filter, for example one
-- floor of a building. like_patterns is the filter in the form the database
-- matches with (see patterns.py).
CREATE TABLE IF NOT EXISTS ap_group (
    name          text PRIMARY KEY,
    pattern       text NOT NULL,
    like_patterns text[] NOT NULL,
    created       timestamptz NOT NULL DEFAULT now()
);
"""

VIEWS = {
    "powerbi_ap_counts": """
        SELECT c.ts                        AS ts_utc,
               (c.ts AT TIME ZONE {tz})    AS ts_local,
               c.wlc, c.ap_name,
               COALESCE(a.location, '')    AS location,
               COALESCE(a.site_tag, '')    AS site_tag,
               COALESCE(a.policy_tag, '')  AS policy_tag,
               c.clients
          FROM ap_client_count c
          LEFT JOIN ap a USING (wlc, ap_name)
    """,
    "powerbi_ap_counts_hourly": """
        SELECT h.hour                      AS hour_utc,
               (h.hour AT TIME ZONE {tz})  AS hour_local,
               h.wlc, h.ap_name,
               COALESCE(a.location, '')    AS location,
               COALESCE(a.site_tag, '')    AS site_tag,
               COALESCE(a.policy_tag, '')  AS policy_tag,
               round(h.clients_sum::numeric / h.samples, 2) AS clients_avg,
               h.clients_min, h.clients_max, h.samples
          FROM ap_client_count_hourly h
          LEFT JOIN ap a USING (wlc, ap_name)
    """,
    "powerbi_access_points": """
        SELECT wlc, ap_name, wtp_mac, ip, location, site_tag, policy_tag, rf_tag, model,
               first_seen AS first_seen_utc, last_seen AS last_seen_utc
          FROM ap
    """,
    "powerbi_ap_groups": """
        SELECT g.name AS group_name, g.pattern, a.wlc, a.ap_name
          FROM ap_group g
          JOIN ap a ON {match}
    """,
    "powerbi_group_counts": """
        SELECT c.ts                        AS ts_utc,
               (c.ts AT TIME ZONE {tz})    AS ts_local,
               g.name                      AS group_name,
               c.wlc,
               count(*)::integer           AS access_points,
               sum(c.clients)::integer     AS clients
          FROM ap_group g
          JOIN ap a ON {match}
          JOIN ap_client_count c ON c.wlc = a.wlc AND c.ap_name = a.ap_name
         GROUP BY c.ts, g.name, c.wlc
    """,
}

# Columns each export hands out, in order. `time` is the column a from/to
# filter applies to.
EXPORTS = {
    "ap-counts": {
        "view": "powerbi_ap_counts",
        "time": "ts_utc",
        "columns": ["ts_utc", "ts_local", "wlc", "ap_name", "location", "site_tag", "policy_tag", "clients"],
    },
    "ap-counts-hourly": {
        "view": "powerbi_ap_counts_hourly",
        "time": "hour_utc",
        "columns": [
            "hour_utc", "hour_local", "wlc", "ap_name", "location", "site_tag", "policy_tag",
            "clients_avg", "clients_min", "clients_max", "samples",
        ],
    },
    "access-points": {
        "view": "powerbi_access_points",
        "time": None,
        "columns": [
            "wlc", "ap_name", "wtp_mac", "ip", "location", "site_tag", "policy_tag", "rf_tag", "model",
            "first_seen_utc", "last_seen_utc",
        ],
    },
    "ap-groups": {
        "view": "powerbi_ap_groups",
        "time": None,
        "columns": ["group_name", "pattern", "wlc", "ap_name"],
    },
    "group-counts": {
        "view": "powerbi_group_counts",
        "time": "ts_utc",
        "columns": ["ts_utc", "ts_local", "group_name", "wlc", "access_points", "clients"],
    },
}


def _ts(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc)


class Store:
    def __init__(self, config):
        self.config = config
        self.pool = ConnectionPool(
            config.database_url, min_size=1, max_size=8, open=False, kwargs={"autocommit": False}
        )

    def open(self, attempts=60):
        """Wait for the database (it may still be starting) and prepare it."""
        for attempt in range(1, attempts + 1):
            try:
                self.pool.open(wait=True, timeout=5)
                self.init_schema()
                return
            except Exception as exc:
                log.warning("database not ready (%s/%s): %s", attempt, attempts, exc)
                try:
                    self.pool.close()
                except Exception:
                    pass
                self.pool = ConnectionPool(
                    self.config.database_url, min_size=1, max_size=8, open=False,
                    kwargs={"autocommit": False},
                )
                time.sleep(2)
        raise RuntimeError("database did not become available")

    def init_schema(self):
        tz = sql.Literal(self.config.tz)
        with self.pool.connection() as conn:
            # One instance at a time: two starting together would race on the views.
            conn.execute("SELECT pg_advisory_xact_lock(726310)")
            conn.execute(SCHEMA)
            # Updates that carried nothing are no longer recorded; drop the ones
            # an earlier version wrote, so empty sources leave the dashboard.
            conn.execute(
                """
                DELETE FROM cycle
                 WHERE (kind = 'clients' AND aps_with_clients = 0 AND aps_zero_filled = 0)
                    OR (kind = 'inventory' AND aps_in_inventory = 0)
                """
            )
            for name, body in VIEWS.items():
                conn.execute(sql.SQL("DROP VIEW IF EXISTS {}").format(sql.Identifier(name)))
                conn.execute(
                    sql.SQL("CREATE VIEW {name} AS " + body).format(
                        name=sql.Identifier(name), tz=tz, match=sql.SQL(match_sql("a", "g.like_patterns"))
                    )
                )
            self._powerbi_role(conn)

    def _powerbi_role(self, conn):
        password = self.config.powerbi_db_password
        if not password:
            return
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'powerbi'").fetchone()
        verb = "ALTER" if exists else "CREATE"
        conn.execute(
            sql.SQL(verb + " ROLE powerbi LOGIN PASSWORD {}").format(sql.Literal(password))
        )
        dbname = conn.execute("SELECT current_database()").fetchone()[0]
        conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO powerbi").format(sql.Identifier(dbname)))
        conn.execute("GRANT USAGE ON SCHEMA public TO powerbi")
        for name in VIEWS:
            conn.execute(sql.SQL("GRANT SELECT ON {} TO powerbi").format(sql.Identifier(name)))

    # ── writes ───────────────────────────────────────────────────────────────
    def save_client_cycle(self, cycle):
        ts = _ts(cycle["ts_epoch"])
        wlc = cycle["wlc"]
        counts = cycle["counts"]
        with self.pool.connection() as conn, conn.cursor() as cur:
            self._insert_cycle(cur, cycle, ts)
            cur.executemany(
                "INSERT INTO ap_client_count (ts, wlc, ap_name, clients) VALUES (%s, %s, %s, %s)"
                " ON CONFLICT DO NOTHING",
                [(ts, wlc, ap, n) for ap, n in counts.items()],
            )
            cur.executemany(
                """
                INSERT INTO ap_client_count_hourly AS h
                       (hour, wlc, ap_name, samples, clients_sum, clients_min, clients_max)
                VALUES (date_trunc('hour', %s::timestamptz, 'UTC'), %s, %s, 1, %s, %s, %s)
                ON CONFLICT (hour, wlc, ap_name) DO UPDATE SET
                       samples     = h.samples + 1,
                       clients_sum = h.clients_sum + EXCLUDED.clients_sum,
                       clients_min = LEAST(h.clients_min, EXCLUDED.clients_min),
                       clients_max = GREATEST(h.clients_max, EXCLUDED.clients_max)
                """,
                [(ts, wlc, ap, n, n, n) for ap, n in counts.items()],
            )
            # An AP that carries clients exists, with or without an AP list.
            cur.executemany(
                """
                INSERT INTO ap (wlc, ap_name, first_seen, last_seen) VALUES (%s, %s, %s, %s)
                ON CONFLICT (wlc, ap_name) DO UPDATE SET last_seen = EXCLUDED.last_seen
                """,
                [(wlc, ap, ts, ts) for ap, n in counts.items() if n > 0],
            )

    def save_inventory(self, cycle):
        ts = _ts(cycle["ts_epoch"])
        wlc = cycle["wlc"]
        with self.pool.connection() as conn, conn.cursor() as cur:
            self._insert_cycle(cur, cycle, ts)
            cur.executemany(
                """
                INSERT INTO ap (wlc, ap_name, wtp_mac, ip, location, site_tag, policy_tag, rf_tag,
                                model, first_seen, last_seen, last_inventory)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (wlc, ap_name) DO UPDATE SET
                    wtp_mac = EXCLUDED.wtp_mac, ip = EXCLUDED.ip, location = EXCLUDED.location,
                    site_tag = EXCLUDED.site_tag, policy_tag = EXCLUDED.policy_tag,
                    rf_tag = EXCLUDED.rf_tag, model = EXCLUDED.model,
                    last_seen = EXCLUDED.last_seen, last_inventory = EXCLUDED.last_inventory
                """,
                [
                    (wlc, ap["ap_name"], ap["wtp_mac"], ap["ip"], ap["location"], ap["site_tag"],
                     ap["policy_tag"], ap["rf_tag"], ap["model"], ts, ts, ts)
                    for ap in cycle["aps"].values()
                ],
            )

    @staticmethod
    def _insert_cycle(cur, cycle, ts):
        clients = cycle["kind"] == "clients"
        cur.execute(
            """
            INSERT INTO cycle (ts, wlc, kind, subscription_id, encoding_path, messages, rows_received,
                               bytes_received, duration_ms, duplicates, clients_counted,
                               clients_skipped, aps_with_clients, aps_zero_filled, aps_in_inventory,
                               wlc_msg_timestamp)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                ts, cycle["wlc"], cycle["kind"], cycle["subscription_id"], cycle["encoding_path"],
                cycle["messages"], cycle["rows"], cycle["bytes"], cycle["duration_ms"],
                cycle.get("duplicates", 0),
                cycle["counted"] if clients else 0,
                cycle["not_counted"] if clients else 0,
                cycle["aps_with_clients"] if clients else 0,
                len(cycle["zero_filled"]) if clients else 0,
                0 if clients else len(cycle["aps"]),
                cycle["wlc_msg_timestamp"],
            ),
        )

    def prune(self):
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.config.retention_days)
        with self.pool.connection() as conn:
            counts = conn.execute("DELETE FROM ap_client_count WHERE ts < %s", (cutoff,)).rowcount
            cycles = conn.execute("DELETE FROM cycle WHERE ts < %s", (cutoff,)).rowcount
        if counts or cycles:
            log.info("retention: removed %s counts and %s cycles older than %s", counts, cycles, cutoff.date())

    # ── reads ────────────────────────────────────────────────────────────────
    def _all(self, query, params=()):
        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            return cur.fetchall()

    def known_aps(self):
        """(wlc, ap_name, last_seen epoch) for the zero-fill fallback."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.config.zero_fill_days)
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT wlc, ap_name, extract(epoch FROM last_seen)::float8 FROM ap WHERE last_seen >= %s",
                (cutoff,),
            ).fetchall()
        return rows

    LATEST = """
        WITH latest AS (
            SELECT DISTINCT ON (wlc) wlc, ts FROM cycle WHERE kind = 'clients' ORDER BY wlc, ts DESC
        ), current AS (
            SELECT c.ts, c.wlc, c.ap_name, c.clients,
                   COALESCE(a.location, '') AS location, COALESCE(a.site_tag, '') AS site_tag
              FROM latest l
              JOIN ap_client_count c ON c.wlc = l.wlc AND c.ts = l.ts
              LEFT JOIN ap a ON a.wlc = c.wlc AND a.ap_name = c.ap_name
        )
    """

    def latest_counts(self, like=None):
        """The newest count of every controller; `like` narrows it to what fits the filter."""
        return self._all(
            self.LATEST
            + """
            SELECT c.ts, c.wlc, c.ap_name, c.clients, c.location, c.site_tag
              FROM current c
             WHERE %(like)s::text[] IS NULL OR """
            + match_sql("c", "%(like)s::text[]")
            + """
             ORDER BY c.clients DESC, c.ap_name
            """,
            {"like": like or None},
        )

    def latest_size(self):
        return self._all(self.LATEST + "SELECT count(*) AS aps FROM current")[0]["aps"]

    def totals(self, hours=24, like=None):
        """Clients over time per controller: everything, or the matching access points."""
        if not like:
            return self._all(
                """
                SELECT ts, wlc, clients_counted AS clients
                  FROM cycle
                 WHERE kind = 'clients' AND ts >= now() - make_interval(hours => %s)
                 ORDER BY ts
                """,
                (hours,),
            )
        if hours <= 48:
            return self._all(
                """
                SELECT c.ts, c.wlc, sum(c.clients)::integer AS clients
                  FROM ap a
                  JOIN ap_client_count c ON c.wlc = a.wlc AND c.ap_name = a.ap_name
                 WHERE """
                + match_sql("a", "%(like)s::text[]")
                + """ AND c.ts >= now() - make_interval(hours => %(hours)s)
                 GROUP BY c.ts, c.wlc
                 ORDER BY c.ts
                """,
                {"like": like, "hours": hours},
            )
        # Longer ranges read the hourly table: the sum of the hourly averages.
        return self._all(
            """
            SELECT h.hour AS ts, h.wlc, round(sum(h.clients_sum::numeric / h.samples))::integer AS clients
              FROM ap a
              JOIN ap_client_count_hourly h ON h.wlc = a.wlc AND h.ap_name = a.ap_name
             WHERE """
            + match_sql("a", "%(like)s::text[]")
            + """ AND h.hour >= now() - make_interval(hours => %(hours)s)
             GROUP BY h.hour, h.wlc
             ORDER BY h.hour
            """,
            {"like": like, "hours": hours},
        )

    # ── groups ───────────────────────────────────────────────────────────────
    def groups(self):
        """Every group with what it holds right now."""
        return self._all(
            self.LATEST
            + """
            SELECT g.name, g.pattern,
                   count(c.ap_name)::integer             AS aps,
                   COALESCE(sum(c.clients), 0)::integer  AS clients
              FROM ap_group g
              LEFT JOIN current c ON """
            + match_sql("c", "g.like_patterns")
            + """
             GROUP BY g.name, g.pattern
             ORDER BY g.name
            """
        )

    def save_group(self, name, pattern, like):
        with self.pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO ap_group (name, pattern, like_patterns) VALUES (%s, %s, %s)
                ON CONFLICT (name) DO UPDATE SET pattern = EXCLUDED.pattern,
                                                 like_patterns = EXCLUDED.like_patterns
                """,
                (name, pattern, like),
            )

    def delete_group(self, name):
        with self.pool.connection() as conn:
            return conn.execute("DELETE FROM ap_group WHERE name = %s", (name,)).rowcount

    def ap_history(self, wlc, ap_name, hours=24):
        return self._all(
            """
            SELECT ts, clients FROM ap_client_count
             WHERE wlc = %s AND ap_name = %s AND ts >= now() - make_interval(hours => %s)
             ORDER BY ts
            """,
            (wlc, ap_name, hours),
        )

    def stats(self):
        rows = self._all(
            """
            SELECT (SELECT count(*) FROM ap) AS aps,
                   (SELECT count(*) FROM cycle WHERE kind = 'clients') AS client_cycles,
                   (SELECT min(ts) FROM cycle WHERE kind = 'clients') AS first_cycle,
                   (SELECT max(ts) FROM cycle WHERE kind = 'clients') AS last_cycle,
                   (SELECT reltuples::bigint FROM pg_class WHERE relname = 'ap_client_count') AS count_rows_estimate,
                   pg_database_size(current_database()) AS database_bytes
            """
        )
        return rows[0]

    def export(self, name, since=None, until=None, wlc=None, like=None, limit=None):
        """Yield (columns) once, then rows as tuples, straight from the view."""
        spec = EXPORTS[name]
        where, params = [], []
        if spec["time"]:
            if since is not None:
                where.append(sql.SQL("{} >= %s").format(sql.Identifier(spec["time"])))
                params.append(since)
            if until is not None:
                where.append(sql.SQL("{} < %s").format(sql.Identifier(spec["time"])))
                params.append(until)
        if wlc:
            where.append(sql.SQL("wlc = %s"))
            params.append(wlc)
        columns = [c for c in MATCH_COLUMNS if c in spec["columns"]]
        if like and columns:
            where.append(sql.SQL(match_sql("", "%s::text[]", columns)))
            params.extend([like] * len(columns))
        query = sql.SQL("SELECT {} FROM {}").format(
            sql.SQL(", ").join(sql.Identifier(c) for c in spec["columns"]),
            sql.Identifier(spec["view"]),
        )
        if where:
            query += sql.SQL(" WHERE ") + sql.SQL(" AND ").join(where)
        query += sql.SQL(" ORDER BY 1, 2, 3")
        if limit:
            query += sql.SQL(" LIMIT %s")
            params.append(limit)

        yield tuple(spec["columns"])
        with self.pool.connection() as conn:
            with conn.cursor(name="export") as cur:
                cur.itersize = 5000
                cur.execute(query, params)
                yield from cur
