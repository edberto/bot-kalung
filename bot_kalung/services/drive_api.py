"""Google Drive API backend for the PC-free scan.

The desktop scans a mounted `G:\\` drive with pathlib. A server has no Drive
mounted, so this presents Drive folders/files as Path-like `DriveNode`s — the
small subset the scanner and headless reader use (`name`, `is_dir`, `is_file`,
`iterdir`, `/`-join, `read_bytes`) — backed by the Drive API. Read-only.

The service account only sees folders explicitly shared with it; those top-level
shares are the exporter folders, reached via `DriveClient.root()` (the scan's
`drive_root`). Google libraries are imported lazily so this module can be present
without pulling them into the desktop build — nothing on the desktop imports it.
"""

from __future__ import annotations

import io

FOLDER_MIME = "application/vnd.google-apps.folder"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# A scanned folder is stored (as shipments.folder_path) as a re-openable
# reference: a filesystem path for the local scan, or "drive:{id}" for a Drive
# scan. These resolve such a reference back to a folder the server can re-read.
REF_PREFIX = "drive:"


def is_drive_ref(ref: str | None) -> bool:
    return bool(ref) and ref.startswith(REF_PREFIX)


def node_from_ref(ref: str, client: DriveClient) -> DriveNode:
    """Reconstruct a folder DriveNode from a stored 'drive:{id}' reference (the
    name is not needed — the reader finds the workbook by listing children)."""
    return DriveNode(client, ref[len(REF_PREFIX):], "", True)


def folder_url(ref: str) -> str:
    """The Drive web URL for a 'drive:{id}' reference (for an 'open folder' link)."""
    return f"https://drive.google.com/drive/folders/{ref[len(REF_PREFIX):]}"


class DriveClient:
    """Thin Drive API wrapper: list a folder's children, download a file."""

    def __init__(self, credentials):
        """`credentials` is a path to the service-account JSON (local/VM deploy)
        or the parsed JSON as a dict (cloud hosts inject secrets as env vars)."""
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        if isinstance(credentials, dict):
            creds = service_account.Credentials.from_service_account_info(
                credentials, scopes=SCOPES)
        else:
            creds = service_account.Credentials.from_service_account_file(
                credentials, scopes=SCOPES)
        self._svc = build("drive", "v3", credentials=creds,
                          cache_discovery=False)

    def root(self) -> DriveNode:
        """A synthetic root whose children are the folders shared with the
        service account (the exporter folders)."""
        return DriveNode(self, None, "", True)

    def _list(self, query: str) -> list[DriveNode]:
        query += " and trashed=false"
        nodes: list[DriveNode] = []
        token = None
        while True:
            resp = self._svc.files().list(
                q=query, pageSize=1000, pageToken=token,
                fields="nextPageToken, files(id, name, mimeType)").execute()
            for f in resp.get("files", []):
                nodes.append(DriveNode(self, f["id"], f["name"],
                                       f["mimeType"] == FOLDER_MIME))
            token = resp.get("nextPageToken")
            if not token:
                return nodes

    def shared_roots(self) -> list[DriveNode]:
        return self._list(f"sharedWithMe=true and mimeType='{FOLDER_MIME}'")

    def children(self, folder_id: str) -> list[DriveNode]:
        return self._list(f"'{folder_id}' in parents")

    def download(self, file_id: str) -> bytes:
        from googleapiclient.http import MediaIoBaseDownload

        buffer = io.BytesIO()
        request = self._svc.files().get_media(fileId=file_id)
        downloader = MediaIoBaseDownload(buffer, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buffer.getvalue()


class DriveNode:
    """A Path-like handle on a Drive file/folder (the subset the scan uses).

    `file_id is None` marks the synthetic root, whose children are the shared
    exporter folders.
    """

    def __init__(self, client: DriveClient, file_id, name: str, is_folder: bool):
        self._client = client
        self.id = file_id
        self._name = name
        self._is_folder = is_folder
        self._children: list[DriveNode] | None = None

    # -- Path-like surface -------------------------------------------------

    @property
    def name(self) -> str:
        return self._name

    @property
    def ref(self) -> str:
        """The re-openable handle stored as shipments.folder_path ('drive:{id}')."""
        return f"{REF_PREFIX}{self.id}"

    def is_dir(self) -> bool:
        return self._is_folder

    def is_file(self) -> bool:
        return not self._is_folder

    def iterdir(self) -> list[DriveNode]:
        if self._children is None:
            if not self._is_folder:
                self._children = []
            elif self.id is None:
                self._children = self._client.shared_roots()
            else:
                self._children = self._client.children(self.id)
        return list(self._children)

    def __truediv__(self, child_name: str) -> DriveNode:
        for child in self.iterdir():
            if child.name == child_name:
                return child
        return DriveNode(self._client, None, child_name, False)  # a "missing" file

    def read_bytes(self) -> bytes:
        return self._client.download(self.id)

    # sorted() over a directory listing compares nodes; order by name like a Path.
    def __lt__(self, other) -> bool:
        return self._name < getattr(other, "name", "")

    def __fspath__(self) -> str:
        return self._name

    def __str__(self) -> str:
        return self._name
