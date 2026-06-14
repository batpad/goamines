# Deploying goamines (Debian 13 + nginx)

Runbook to serve the goamines Datasette site at
**https://goamines.whydidweevendothis.com** on a Debian 13 box that already runs nginx
(with certbot installed).

**Pattern:** a dedicated `goamines` unix user holds the code in its home dir; **systemd**
runs Datasette on `127.0.0.1:8001`; **nginx** reverse-proxies the subdomain and certbot
terminates TLS.

Files referenced below live in this `deploy/` folder:
- `goamines.service` — systemd unit (runs the venv's `datasette`, immutable mode)
- `nginx-goamines.conf` — nginx server block (HTTP-first; certbot adds 443)
- `update.sh` — pull + sync + rebuild/refresh helper

Datasette (1.0a) serves from the working dir using `metadata.yaml` (descriptive metadata) +
`datasette.yaml` (settings, plugins, canned queries), with `goamines.db` and the
`static/` pages (`dashboard.html`, `routes_map.html`). The config files and
`static/dashboard.html` arrive via `git`; the raw data (`data1/`, `data2/`),
`goamines.db`, and the generated `static/routes_map.html` are git-ignored, so the DB +
route map are provisioned out-of-band (step 5).

---

## 0. DNS
Point an `A` (and `AAAA` if you have IPv6) record:
```
goamines.whydidweevendothis.com  ->  <server IP>
```
Confirm it resolves before requesting a cert (step 8).

## 1. Create the service user
```bash
sudo adduser --disabled-password --gecos "" goamines
```

## 2. Install uv (as the goamines user)
```bash
sudo -u goamines bash -lc 'curl -LsSf https://astral.sh/uv/install.sh | sh'
```
uv lands in `~/.local/bin`. It will fetch Python 3.12 itself (per `.python-version`), so no
system Python setup is needed.

## 3. Clone the repo
```bash
sudo -u goamines git clone https://github.com/batpad/goamines.git /home/goamines/goamines
```

## 4. Install dependencies (creates `.venv/`)
```bash
sudo -u goamines bash -lc 'cd ~/goamines && uv sync'
```
This produces `/home/goamines/goamines/.venv/bin/datasette`, which the systemd unit runs.

## 5. Provision the database

**Copy-up path (go live fast)** — from your laptop, in the repo with a freshly built
`goamines.db`:
```bash
scp goamines.db          goamines@SERVER:~/goamines/goamines.db
ssh goamines@SERVER 'mkdir -p ~/goamines/static'
scp static/routes_map.html goamines@SERVER:~/goamines/static/routes_map.html
```
(If you can't scp as `goamines` directly, copy to a temp dir and
`sudo install -o goamines -g goamines <file> /home/goamines/goamines/...`.)

**Rebuild-on-server path (for later refreshes)** — upload the raw data once, then build:
```bash
# from laptop:
rsync -avz data1 data2 goamines@SERVER:~/goamines/
# on server, as goamines:
cd ~/goamines && uv run python ingest.py        # rebuilds goamines.db + static/routes_map.html
```

## 6. systemd service
```bash
sudo cp /home/goamines/goamines/deploy/goamines.service /etc/systemd/system/goamines.service
sudo systemctl daemon-reload
sudo systemctl enable --now goamines
# smoke test (should print the page <title>):
curl -s localhost:8001/ | grep -o '<title>[^<]*</title>'
sudo journalctl -u goamines -n 30 --no-pager      # check for a clean start
```

## 7. nginx site
```bash
sudo cp /home/goamines/goamines/deploy/nginx-goamines.conf /etc/nginx/sites-available/goamines
sudo ln -s ../sites-available/goamines /etc/nginx/sites-enabled/goamines
sudo nginx -t && sudo systemctl reload nginx
```

## 8. TLS (certbot)
```bash
sudo certbot --nginx -d goamines.whydidweevendothis.com
```
certbot edits the nginx config to add the `:443` server block and an HTTP→HTTPS redirect,
and installs an auto-renewal timer.

## 9. Verify
- `https://goamines.whydidweevendothis.com/` loads (Datasette homepage, valid cert).
- **Route map**: `/static/routes_map.html` renders the curved arcs.
- **Point map**: `/goamines/location_map` shows the cluster map.
- A canned query works, e.g. `/goamines/negative_balance_storage`.

## 10. Downloads (source data via nginx)
The homepage links three downloads: the **DB** (`/goamines.db`, served by Datasette — nothing
to do), the **code** (GitHub), and the **source RTI spreadsheets** (a static zip served by
nginx). To publish the source zip:

```bash
# a) create the downloads dir (once)
sudo mkdir -p /var/www/goamines/downloads

# b) build the zip on your LAPTOP (data spreadsheets only) and copy it up:
#    cd <repo>; zip -r goamines-source-data.zip data1 data2 \
#      -x 'data2/Annexure I.pdf' 'data2/Manuals.rar' 'data2/drive-download*'
#    scp goamines-source-data.zip sanj@HOST:/tmp/
sudo install -m 644 /tmp/goamines-source-data.zip /var/www/goamines/downloads/
rm /tmp/goamines-source-data.zip

# c) add the /downloads/ location to the LIVE nginx config (certbot-managed), inside the
#    `listen 443 ssl` server block — see deploy/nginx-goamines.conf for the snippet:
#        location /downloads/ { alias /var/www/goamines/downloads/; autoindex on; }
sudo nano /etc/nginx/sites-available/goamines
sudo nginx -t && sudo systemctl reload nginx
```
Verify: `curl -sI https://goamines.whydidweevendothis.com/downloads/goamines-source-data.zip | head -1`
→ `200`. **To refresh** later, rebuild the zip and re-run step (b).

## 11. Updating later
```bash
sudo -u goamines /home/goamines/goamines/deploy/update.sh
sudo systemctl restart goamines
```
`update.sh` pulls, runs `uv sync`, and rebuilds the DB if `data1/`+`data2/` are present
(otherwise just refreshes the route map from the existing DB). If the systemd unit changed,
also re-copy it (step 6) and `daemon-reload` before restarting.

---

## Troubleshooting
- **Service won't start** — `sudo journalctl -u goamines -n 50`. Usual causes: `.venv`
  missing (re-run step 4), or `goamines.db` not present/owned by `goamines` (step 5).
- **502 from nginx** — Datasette isn't up on 8001; check the service and `curl localhost:8001/`.
- **Port 8001 in use** — change it in *both* `goamines.service` (ExecStart) and
  `nginx-goamines.conf` (proxy_pass), then `daemon-reload`/restart and reload nginx.
- **Mixed-content / http links** — the unit already sets `--setting force_https_urls 1`.
- **Permissions** — nginx never touches the filesystem here (Datasette serves the static
  map via `--static`), so only the `goamines` user needs to read `~/goamines`.
