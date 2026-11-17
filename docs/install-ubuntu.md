# Install on an Ubuntu server

Tested layout: Ubuntu Server 22.04 or 24.04, the project in `/opt/cisco-client-count`,
everything in Docker. Nothing else is installed on the host.

## 1. Docker

Skip this when `docker compose version` already answers.

```bash
sudo apt-get update && sudo apt-get install -y ca-certificates curl git openssl
```

```bash
sudo install -m 0755 -d /etc/apt/keyrings && sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc && sudo chmod a+r /etc/apt/keyrings/docker.asc
```

```bash
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
```

```bash
sudo apt-get update && sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Docker starts at boot by default. Check:

```bash
sudo docker compose version
```

## 2. Get the project

```bash
sudo git clone https://github.com/andystevenscodes/cisco-client-count.git /opt/cisco-client-count
```

```bash
cd /opt/cisco-client-count
```

## 3. Settings

Generate `.env` with random passwords:

```bash
sudo ./scripts/init-env.sh
```

The script prints the web interface password and the export token once. Both stay
readable in `.env` (only root can read that file):

```bash
sudo grep -E '^(ADMIN_PASSWORD|EXPORT_TOKEN)=' .env
```

Edit `.env` when a default does not fit, for example another time zone, another
port, or a limit on who may send telemetry:

```bash
sudo nano .env
```

```
TZ=Europe/Amsterdam
ALLOWED_WLC_SOURCES=192.0.2.1
```

Every setting is explained in [.env.example](../.env.example).

## 4. Start

```bash
sudo docker compose up -d --build
```

The first start builds the image and takes a few minutes. Then:

```bash
sudo docker compose ps
```

Both `ccc-app` and `ccc-db` must show `healthy`. The log of the application:

```bash
sudo docker compose logs --tail 20 app
```

It should end with

```
ccc.receiver: listening for telemetry (gRPC dial-out) on port 57500
ccc: web interface on port 8080
```

Open `http://<server-ip>:8080` and sign in.

Both containers have `restart: unless-stopped`: they come back after a reboot and
after a crash. There is no systemd unit to install.

## 5. Firewall

When `ufw` is active, open the two ports, each only for who needs it. Replace the
example addresses.

Telemetry, from the controller:

```bash
sudo ufw allow from 192.0.2.1 to any port 57500 proto tcp
```

Web interface and export, from the management network:

```bash
sudo ufw allow from 198.51.100.0/24 to any port 8080 proto tcp
```

Only when Power BI connects straight to PostgreSQL (and `DB_BIND=0.0.0.0` is set):

```bash
sudo ufw allow from 198.51.100.0/24 to any port 5432 proto tcp
```

Note that Docker publishes ports past `ufw`. A port bound to `0.0.0.0` in `.env`
is reachable regardless of `ufw` rules; bind to `127.0.0.1` or to one interface
address (`WEB_BIND`, `DB_BIND`) when it must not be reachable, or filter in the
network in front of the server.

## 6. Connect the controller

Continue with [wlc-setup.md](wlc-setup.md), or try the tool first with the
[simulator](../README.md#try-it-without-a-controller).

## Updating

```bash
cd /opt/cisco-client-count && sudo git pull && sudo docker compose up -d --build
```

The database schema is brought up to date at start. Data stays in `./data/postgres`.

## Backup and restore

Backup:

```bash
sudo docker compose exec -T db pg_dump -U ccc ccc | gzip > ccc-$(date +%F).sql.gz
```

Restore into an empty installation:

```bash
gunzip -c ccc-2026-01-31.sql.gz | sudo docker compose exec -T db psql -U ccc ccc
```

Keep a copy of `.env` with the backup.

## https

The application speaks http. For https, put a reverse proxy on the same host and
keep the application itself on localhost.

In `.env`:

```
WEB_BIND=127.0.0.1
SESSION_COOKIE_SECURE=true
```

Example with Caddy (`sudo apt-get install -y caddy`), in `/etc/caddy/Caddyfile`,
using a certificate of your own CA:

```
clientcount.example.org {
    tls /etc/ssl/clientcount/fullchain.pem /etc/ssl/clientcount/privkey.pem
    reverse_proxy 127.0.0.1:8080
}
```

```bash
sudo docker compose up -d && sudo systemctl reload caddy
```

Power BI then uses `https://clientcount.example.org/export/...`.

## Removing

```bash
cd /opt/cisco-client-count && sudo docker compose down
```

This keeps the data. To delete the data as well, remove the directory
`/opt/cisco-client-count/data`.
