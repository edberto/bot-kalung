"""Delete a shipment's folder (send it to the Recycle Bin).

Folder/document *construction* was removed with the folder-scan refactor; only
the delete path remains, used by the shipment detail and history screens.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class FolderDeleteResult:
    """Outcome of deleting a shipment's folder."""
    removed: bool                       # the folder is gone (or was never there)
    recycled: bool                      # went to the Recycle Bin, so recoverable
    existed: bool                       # there was a folder to remove
    message: str = ""


def delete_shipment_folder(folder) -> FolderDeleteResult:
    """Send a shipment folder to the Windows Recycle Bin. Never raises.

    Uses the shell SHFileOperation with FOF_ALLOWUNDO so the delete is
    recoverable. Google Drive's virtual G: drive does not always honour the
    Recycle Bin and may delete permanently regardless; there is no reliable way
    to know up front, so the caller warns that recovery is not guaranteed.
    """
    if not folder:
        return FolderDeleteResult(True, False, False,
                                  "Pengiriman ini tidak punya folder.")
    path = Path(folder)
    if not path.exists():
        return FolderDeleteResult(True, False, False,
                                  "Folder sudah tidak ada.")
    if not path.is_dir():
        return FolderDeleteResult(False, False, True,
                                  f"Bukan folder: {path}")

    try:
        from win32com.shell import shell, shellcon
    except ImportError:      # non-Windows / missing pywin32 — never in prod
        return FolderDeleteResult(
            False, False, True,
            "Penghapusan ke Recycle Bin tidak tersedia di sistem ini.")

    flags = (shellcon.FOF_ALLOWUNDO | shellcon.FOF_NOCONFIRMATION
             | shellcon.FOF_SILENT | shellcon.FOF_NOERRORUI)
    try:
        result, aborted = shell.SHFileOperation(
            (0, shellcon.FO_DELETE, str(path.resolve()), None, flags, None, None))
    except Exception as exc:  # noqa: BLE001 - reported, delete must not crash the app
        return FolderDeleteResult(False, False, True,
                                  f"Gagal menghapus folder: {exc}")

    if aborted or result != 0 or path.exists():
        return FolderDeleteResult(
            False, False, True,
            "Folder tidak dapat dihapus. Mungkin sedang dibuka di Excel atau "
            "Explorer. Tutup lalu coba lagi.")
    return FolderDeleteResult(True, True, True, "Folder dipindahkan ke Recycle Bin.")
