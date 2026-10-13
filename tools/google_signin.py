"""One-time Google sign-in for the email reader (doc automation Phase 2).

    python tools/google_signin.py              # sign in, save the token on this PC
    python tools/google_signin.py --to-worker  # ...and put it in the worker's server.env

Opens a browser: pick the account whose inbox the bot should read, click
through "Google hasn't verified this app" (Advanced -> Go to Bot Kalung), and
approve. Permissions asked:
  - gmail.readonly  read mail only — cannot send, delete or mark as read
  - drive           Drive, used ONLY to save an attachment into a shipment folder
                    when its queue entry is approved (the narrower drive.file
                    can't see existing folders, so it can't save into them)

The token is never printed. Locally it goes to secrets/google-oauth-token.json
(git-ignored); --to-worker sends it over SSH into /opt/bot-kalung/secrets/server.env.
Revoke anytime: Google Account -> Security -> Third-party access -> Bot Kalung.
"""

import json
import subprocess
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "secrets" / "google-oauth-client.json"
TOKEN = ROOT / "secrets" / "google-oauth-token.json"
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly",
          "https://www.googleapis.com/auth/drive"]
WORKER = "ubuntu@129.225.12.73"

flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT), SCOPES)
creds = flow.run_local_server(port=0, prompt="consent", access_type="offline",
                              authorization_prompt_message="Browser opened — sign in there.")
if not creds.refresh_token:
    sys.exit("Google returned no refresh token — run again.")

from googleapiclient.discovery import build  # noqa: E402

email = build("gmail", "v1", credentials=creds).users().getProfile(userId="me").execute()["emailAddress"]
client = json.loads(CLIENT.read_text())["installed"]
values = {"GOOGLE_OAUTH_CLIENT_ID": client["client_id"],
          "GOOGLE_OAUTH_CLIENT_SECRET": client["client_secret"],
          "GOOGLE_OAUTH_REFRESH_TOKEN": creds.refresh_token,
          "GMAIL_ADDRESS": email}
TOKEN.write_text(json.dumps(values))
print(f"Signed in as {email}. Token saved to {TOKEN.relative_to(ROOT)}.")

if "--to-worker" in sys.argv:
    remote = ("f=/opt/bot-kalung/secrets/server.env; "
              "while IFS== read -r k v; do sed -i \"/^$k=/d\" $f; echo \"$k=$v\" >> $f; done; "
              "chmod 600 $f; echo \"saved on worker ($(grep -c '^GOOGLE_OAUTH_\\|^GMAIL_ADDRESS' $f) lines)\"")
    payload = "".join(f"{k}={v}\n" for k, v in values.items())
    subprocess.run(["ssh", WORKER, remote], input=payload, text=True, check=True)
