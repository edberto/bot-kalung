"""Prove the email queue can save into a shipment folder: as the signed-in user
(tools/google_signin.py), upload a tiny text file into one folder, check the
bot's normal read access sees it, then delete it again.

    python tools/drive_write_test.py <drive folder id>
"""

import io
import json
import sys
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bot_kalung.services import drive_api  # noqa: E402

folder_id = sys.argv[1]
t = json.loads((ROOT / "secrets" / "google-oauth-token.json").read_text())
user = build("drive", "v3", credentials=Credentials(
    None, refresh_token=t["GOOGLE_OAUTH_REFRESH_TOKEN"], client_id=t["GOOGLE_OAUTH_CLIENT_ID"],
    client_secret=t["GOOGLE_OAUTH_CLIENT_SECRET"], token_uri="https://oauth2.googleapis.com/token"))

name = "bot-kalung-write-test.txt"
made = user.files().create(
    body={"name": name, "parents": [folder_id]},
    media_body=MediaIoBaseUpload(io.BytesIO(b"test - safe to delete"), mimetype="text/plain"),
    fields="id", supportsAllDrives=True).execute()
print(f"1. uploaded as {t['GMAIL_ADDRESS']}: {made['id']}")
try:
    bot = drive_api.DriveClient(str(ROOT / "secrets" / "bot-kalung-7861b7295b7d.json"))
    seen = any(n.id == made["id"] for n in bot.children(folder_id))
    print(f"2. visible to the bot's read access: {'yes' if seen else 'NO'}")
finally:
    user.files().delete(fileId=made["id"], supportsAllDrives=True).execute()
    print("3. deleted")
