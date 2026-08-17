# Deploy the server worker to Oracle Always-Free ($0)

The worker (`bot_kalung/server.py`) runs the Drive scan + BNCT poll into Supabase.
It runs as a plain Python venv under systemd (auto-restart, starts on boot). No
inbound ports are needed — the worker only makes outbound calls (Drive, BNCT,
Supabase).

## 1. Create the VM (~5 min)
Oracle Cloud console → **Compute → Instances → Create instance**:
- **Image:** Canonical **Ubuntu 22.04**.
- **Shape:** **VM.Standard.E2.1.Micro** (AMD, Always Free — 1 GB RAM, enough).
  (The ARM `A1.Flex` shape is bigger but often "out of capacity"; the AMD micro
  is usually available.)
- Add your SSH public key, create. Note the public IP.

No ingress rules needed (outbound only). SSH in:
```bash
ssh ubuntu@<PUBLIC_IP>
```

## 2. Install prerequisites
```bash
sudo apt update && sudo apt install -y python3-venv python3-pip git
```

## 3. Get the code + install deps
First push your local commits so GitHub has the server code (from your Windows
machine): `git push origin master`. Then on the VM:
```bash
sudo mkdir -p /opt/bot-kalung && sudo chown ubuntu:ubuntu /opt/bot-kalung
git clone https://github.com/edberto/bot-kalung.git /opt/bot-kalung
cd /opt/bot-kalung
python3 -m venv .venv
.venv/bin/pip install -r requirements-server.txt   # server deps only — NOT requirements.txt
```
(If the repo is private, use a deploy key or a personal-access-token URL; or
`scp` the working tree up instead of cloning.)

## 4. Put the secrets on the VM (never in git)
`secrets/` is git-ignored, so copy it up from your machine:
```bash
# from your Windows machine (Git Bash / scp):
scp secrets/bot-kalung-7861b7295b7d.json ubuntu@<PUBLIC_IP>:/opt/bot-kalung/secrets/
```
Then create `/opt/bot-kalung/secrets/server.env` on the VM:
```bash
mkdir -p /opt/bot-kalung/secrets
cat > /opt/bot-kalung/secrets/server.env <<'EOF'
SUPABASE_DB_URL=postgresql://postgres.<ref>:PASSWORD@aws-0-<region>.pooler.supabase.com:5432/postgres
DRIVE_CREDENTIALS=/opt/bot-kalung/secrets/bot-kalung-7861b7295b7d.json
SCAN_INTERVAL_MINUTES=30
POLL_INTERVAL_MINUTES=5
EOF
chmod 600 /opt/bot-kalung/secrets/server.env
```

## 5. Smoke-test one cycle
```bash
cd /opt/bot-kalung
set -a; . secrets/server.env; set +a
.venv/bin/python -m bot_kalung.server --once      # one scan + poll, then exits
```
You should see `scan: N imported ...` and `poll: ... vessels on portal ...`.

## 6. Install the service (auto-restart + start on boot)
```bash
sudo cp /opt/bot-kalung/deploy/bot-kalung-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bot-kalung-server
```

## 7. Watch it
```bash
systemctl status bot-kalung-server
journalctl -u bot-kalung-server -f        # live logs
```

## Updating later
```bash
cd /opt/bot-kalung && git pull
.venv/bin/pip install -r requirements-server.txt
sudo systemctl restart bot-kalung-server
```
