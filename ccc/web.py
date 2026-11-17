"""Web interface, JSON API and the export for Power BI."""

import csv
import hmac
import io
import json
import os
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from functools import wraps
from zoneinfo import ZoneInfo

from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    stream_with_context,
    url_for,
)

from ccc import patterns
from ccc.db import EXPORTS

LOGIN_WINDOW = 300
LOGIN_ATTEMPTS = 5


def _same(a, b):
    return hmac.compare_digest(a.encode(), b.encode())


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _parse_time(text, tz):
    """'2026-01-31' or '2026-01-31T08:00' (local time unless it carries an offset)."""
    value = datetime.fromisoformat(text)
    if value.tzinfo is None:
        value = value.replace(tzinfo=tz)
    return value.astimezone(timezone.utc)


def create_app(config, pipeline, store):
    app = Flask(__name__)
    app.secret_key = config.secret_key
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "").lower() in ("1", "true", "yes"),
        SESSION_COOKIE_NAME="ccc_session",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    )
    app.json.default = _json_default
    app.json.sort_keys = False  # raw messages keep the field order of the controller
    tz = ZoneInfo(config.tz)
    failures = {}

    # ── access ───────────────────────────────────────────────────────────────
    def login_required(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not session.get("user"):
                if request.path.startswith("/api/"):
                    return jsonify(error="not signed in"), 401
                return redirect(url_for("login", next=request.path))
            return view(*args, **kwargs)

        return wrapper

    def export_allowed():
        if session.get("user"):
            return True
        if not config.export_token:
            return False
        auth = request.authorization
        if auth is None:
            return False
        if auth.type == "bearer" and auth.token:
            return _same(auth.token, config.export_token)
        if auth.type == "basic" and auth.password:
            return _same(auth.password, config.export_token)
        return False

    @app.after_request
    def headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'",
        )
        if request.path.startswith(("/api/", "/export/")):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.context_processor
    def inject():
        return {"version": config.version, "anonymize": config.anonymize_clients}

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        if request.method == "POST":
            ip = request.remote_addr or "?"
            now = time.time()
            recent = [t for t in failures.get(ip, []) if now - t < LOGIN_WINDOW]
            if len(recent) >= LOGIN_ATTEMPTS:
                failures[ip] = recent
                return render_template("login.html", error="Too many attempts. Wait a few minutes."), 429
            if _same(request.form.get("password", ""), config.admin_password):
                failures.pop(ip, None)
                session.clear()
                session["user"] = "admin"
                session.permanent = True
                target = request.args.get("next", "")
                if not target.startswith("/") or target.startswith("//"):
                    target = url_for("dashboard")
                return redirect(target)
            recent.append(now)
            failures[ip] = recent
            error = "Wrong password."
        return render_template("login.html", error=error)

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ── pages ────────────────────────────────────────────────────────────────
    @app.get("/")
    @login_required
    def dashboard():
        return render_template("dashboard.html", page="dashboard")

    @app.get("/raw")
    @login_required
    def raw():
        return render_template("raw.html", page="raw")

    @app.get("/translation")
    @login_required
    def translation():
        return render_template("translation.html", page="translation")

    @app.get("/setup")
    @login_required
    def setup():
        host = request.host.rsplit(":", 1)[0] if ":" in request.host else request.host
        return render_template(
            "setup.html", page="setup", receiver_host=host.strip("[]"), telemetry_port=config.telemetry_public_port
        )

    @app.get("/powerbi")
    @login_required
    def powerbi():
        return render_template(
            "powerbi.html",
            page="powerbi",
            base_url=request.host_url.rstrip("/"),
            db_host=request.host.rsplit(":", 1)[0],
            db_port=config.db_public_port,
            token_set=bool(config.export_token),
            db_role_set=bool(config.powerbi_db_password),
            exports=EXPORTS,
            tz=config.tz,
            retention_days=config.retention_days,
        )

    # ── API for the pages ────────────────────────────────────────────────────
    @app.get("/api/status")
    @login_required
    def api_status():
        return jsonify(pipeline.status())

    @app.get("/api/dashboard")
    @login_required
    def api_dashboard():
        hours = min(max(request.args.get("hours", 24, type=int), 1), 24 * 31)
        text = patterns.normalize(request.args.get("filter", ""))
        like = patterns.like_patterns(text) or None
        latest = store.latest_counts(like)
        return jsonify(
            status=pipeline.status(),
            filter=text,
            latest=latest,
            latest_total=store.latest_size() if like else len(latest),
            totals=store.totals(hours, like),
            groups=store.groups(),
            database=store.stats(),
            hours=hours,
        )

    def changing(view):
        """State-changing API calls: signed in, and sent by this site's own script."""

        @wraps(view)
        def wrapper(*args, **kwargs):
            if not session.get("user"):
                return jsonify(error="not signed in"), 401
            if request.headers.get("X-Requested-With") != "ccc":
                return jsonify(error="bad request"), 400
            return view(*args, **kwargs)

        return wrapper

    @app.post("/api/groups")
    @changing
    def api_group_save():
        body = request.get_json(silent=True) or {}
        name = " ".join(str(body.get("name", "")).split())[:60]
        text = patterns.normalize(str(body.get("filter", "")))
        like = patterns.like_patterns(text)
        if not name:
            return jsonify(error="Give the group a name."), 400
        if not like:
            return jsonify(error="A group needs a filter."), 400
        store.save_group(name, text, like)
        return jsonify(groups=store.groups())

    @app.delete("/api/groups/<path:name>")
    @changing
    def api_group_delete(name):
        store.delete_group(name)
        return jsonify(groups=store.groups())

    @app.get("/api/ap-history")
    @login_required
    def api_ap_history():
        hours = min(max(request.args.get("hours", 24, type=int), 1), 24 * 31)
        wlc, ap = request.args.get("wlc", ""), request.args.get("ap", "")
        if not wlc or not ap:
            abort(400)
        return jsonify(wlc=wlc, ap=ap, hours=hours, points=store.ap_history(wlc, ap, hours))

    @app.get("/api/raw")
    @login_required
    def api_raw():
        limit = min(max(request.args.get("limit", 100, type=int), 1), 1000)
        return jsonify(
            messages=pipeline.raw_list(
                limit=limit,
                wlc=request.args.get("wlc") or None,
                path=request.args.get("path") or None,
                first_seq=request.args.get("first_seq", type=int),
                last_seq=request.args.get("last_seq", type=int),
            ),
            status=pipeline.status(),
        )

    @app.get("/api/raw/<int:seq>")
    @login_required
    def api_raw_one(seq):
        item = pipeline.raw_get(seq)
        if item is None:
            return jsonify(error="This message has left the buffer."), 404
        return jsonify(item)

    @app.get("/api/cycles")
    @login_required
    def api_cycles():
        return jsonify(cycles=pipeline.cycles(kind=request.args.get("kind") or None))

    @app.get("/api/cycles/<int:cycle_id>")
    @login_required
    def api_cycle(cycle_id):
        item = pipeline.cycle(cycle_id)
        if item is None:
            return jsonify(error="This cycle is no longer kept in memory."), 404
        out = {k: v for k, v in item.items() if k not in ("decisions", "aps")}
        if item["kind"] == "clients":
            states = {}
            for decision in item["decisions"]:
                key = decision["co_state"] or "(empty)"
                bucket = states.setdefault(key, {"co_state": key, "clients": 0, "counted": 0})
                bucket["clients"] += 1
                bucket["counted"] += decision["counted"]
            out["states"] = sorted(states.values(), key=lambda s: -s["clients"])
            sample = item.get("sample_row") or {}
            mac = str((sample.get("keys") or {}).get("client-mac") or "").lower()
            out["sample_decision"] = next(
                (d for d in item["decisions"] if d["client_mac"] == mac), None
            )
        else:
            out["aps"] = sorted(item["aps"].values(), key=lambda a: a["ap_name"])
        return jsonify(out)

    @app.get("/api/cycles/<int:cycle_id>/rows")
    @login_required
    def api_cycle_rows(cycle_id):
        item = pipeline.cycle(cycle_id)
        if item is None or item["kind"] != "clients":
            return jsonify(error="This cycle is no longer kept in memory."), 404
        ap = request.args.get("ap")
        counted = request.args.get("counted")
        limit = min(max(request.args.get("limit", 500, type=int), 1), 5000)
        rows = item["decisions"]
        if ap is not None:
            rows = [r for r in rows if r["ap_name"] == ap]
        if counted in ("0", "1"):
            rows = [r for r in rows if r["counted"] == (counted == "1")]
        return jsonify(total=len(rows), rows=rows[:limit])

    # ── export (Power BI, Excel, scripts) ────────────────────────────────────
    @app.get("/export/<name>.<fmt>")
    def export(name, fmt):
        if not export_allowed():
            return Response(
                "Sign in, or use the export token.\n",
                401,
                {"WWW-Authenticate": 'Basic realm="cisco-client-count export"'},
                mimetype="text/plain",
            )
        if name not in EXPORTS or fmt not in ("csv", "json"):
            abort(404)
        spec = EXPORTS[name]
        since = until = None
        try:
            if spec["time"]:
                if request.args.get("from"):
                    since = _parse_time(request.args["from"], tz)
                else:
                    default_days = 365 if name.endswith("hourly") else 30
                    days = min(max(request.args.get("days", default_days, type=int), 1), 3660)
                    since = datetime.now(timezone.utc) - timedelta(days=days)
                if request.args.get("to"):
                    until = _parse_time(request.args["to"], tz)
        except ValueError:
            return Response("from/to must look like 2026-01-31 or 2026-01-31T08:00\n", 400, mimetype="text/plain")

        rows = store.export(
            name,
            since=since,
            until=until,
            wlc=request.args.get("wlc") or None,
            like=patterns.like_patterns(request.args.get("filter", "")) or None,
        )
        columns = next(rows)

        def cell(value):
            if isinstance(value, datetime):
                if value.tzinfo is None:
                    return value.strftime("%Y-%m-%d %H:%M:%S")
                return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if isinstance(value, Decimal):
                return float(value)
            return value

        def as_csv():
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            writer.writerow(columns)
            for index, row in enumerate(rows, 1):
                writer.writerow([cell(v) for v in row])
                if index % 500 == 0:
                    yield buffer.getvalue()
                    buffer.seek(0)
                    buffer.truncate()
            yield buffer.getvalue()

        def as_json():
            yield "["
            first = True
            for row in rows:
                item = {c: cell(v) for c, v in zip(columns, row)}
                yield ("" if first else ",") + "\n" + json.dumps(item, default=str)
                first = False
            yield "\n]\n"

        if fmt == "csv":
            return Response(
                stream_with_context(as_csv()),
                mimetype="text/csv",
                headers={"Content-Disposition": f'inline; filename="{name}.csv"'},
            )
        return Response(stream_with_context(as_json()), mimetype="application/json")

    return app
