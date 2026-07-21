# Bot Kalung — manual integration test

A run-through of the whole app against real data, to confirm the pieces work
*together* (the automated suite tests them in isolation). Do this on a release
build: `dist\Bot Kalung.exe`.

Tick each box. If something fails, note the step number and what you saw.

**Before you start**
- [ ] Google Drive is synced and `G:\My Drive` is reachable.
- [ ] A valid `ANTHROPIC_API_KEY` is set in Settings (needed for DO extraction).
- [ ] You have a real DO PDF to import (use a spare / test shipment, not a live one you care about).
- [ ] Windows Focus Assist / Do Not Disturb is **off** (so tray notifications show).

> Parts that need the live BNCT portal are marked **[portal]**; parts that need
> the API key are marked **[llm]**. Skip those cleanly if unavailable.

---

## 1. Launch & migration
- [ ] Double-click `dist\Bot Kalung.exe`. It opens to the dashboard with no error dialog.
- [ ] (If this is the first launch of a new build on an old database) it does **not** show a "no such table" error — the schema migrated silently.
- [ ] The window title/taskbar shows the Bot Kalung icon.

## 2. New shipment  **[llm]**
- [ ] Click **+ Pengiriman Baru**. Pick an exporter, drop in the DO PDF.
- [ ] Extraction fills booking no., vessel, voyage, ETD, destination, container qty/size. Fields that couldn't be read show a warning, not a crash.
- [ ] Carrier (OOCL / Evergreen / Yang Ming) is detected correctly for this DO.
- [ ] Continue through the wizard; on finish it creates the folder under the right exporter path, renames the workbooks (e.g. `AMJ24-…`), pre-fills the Excel, and auto-prints the SI (or warns if no printer).
- [ ] It lands on the new shipment's detail view; A1 and B1 are already ticked.

## 3. Dashboard & sidebar
- [ ] Go Home. The new shipment shows as a **card**; hovering it highlights (border), and clicking **anywhere on the card** opens it.
- [ ] The sidebar lists it under Pengiriman Aktif; hovering shows an **outline**; the vessel name is fully visible (not clipped); clicking opens it.

## 4. Checklist & actions
- [ ] On the detail view, the header shows badge, vessel/voyage, ETD (red if ≤3 days), booking, qty×size.
- [ ] If the destination is a quarantine country, the karantina banner shows.
- [ ] **Buka Folder** and **Buka Excel** open the right locations.
- [ ] A WhatsApp step's button opens WhatsApp (Web or app) and does **not** tick the step.
- [ ] An email step's button opens a Gmail draft with the two teammates pre-filled and subject `{exporter}{seq}` (e.g. `AMJ24`), and **does** tick the step.
- [ ] Ticking a manual step persists; unticking is silent.

## 5. PDF export (E4)
- [ ] Click **Ekspor PDF** on step E4.
- [ ] The `PDF` subfolder gets five files named `SI - AMJ24.pdf`, `VGM - …`, `Inv Buyer - …`, `Inv BC - …`, `PL - …`.
- [ ] E4 ticks itself; the workbook is unchanged. Re-exporting overwrites cleanly.

## 6. BNCT monitoring  **[portal]**
Pick a vessel currently on the portal (open `portal.bnct-id.com/sso/` and read the Vessel Schedule / Vessel Alongside lists), and set a test shipment's vessel + voyage to match it.
- [ ] On the shipment's BNCT panel, click **Periksa Sekarang**. The banner **"Memeriksa BNCT…"** appears, then resolves to **"Pemeriksaan BNCT selesai."**
- [ ] The banner is **dismissable** via the **×** in its top-right.
- [ ] The panel shows the vessel's status: *Terjadwal* (with ETD / Open Billing / Open Stack) or *Sudah sandar* (with Loading/Discharge/Restow figures), or *belum terjadwal* if not found.
- [ ] A **tray notification** pops for the first sighting (schedule) or berth (alongside). If Focus Assist hid it, check the Windows notification center.

## 7. Notification centre
- [ ] The sidebar **Notifikasi** item shows a red **(N)** counter after a notification.
- [ ] Opening it lists the notifications, newest first, colour-dotted by type.
- [ ] Clicking a notification **marks it read** (counter drops) **and opens its shipment**.
- [ ] Clicking a **tray toast** (or the departure dialog's *Buka Pengiriman*) also jumps to the shipment.
- [ ] **Tandai semua dibaca** clears the counter.

### 6b. Departure alert (simulated) — optional
No real vessel will be under the threshold on demand, so to see the *departing* alert + "pay LOLO" dialog, run the demo (safe — temp database, never touches Drive):
```
.venv\Scripts\python tools\bnct_notify_demo.py
```
- [ ] A red tray toast and a **"bayar LOLO penuh ke Indra"** dialog appear; *Buka Pengiriman* opens the shipment.

## 8. History & delete
- [ ] Complete a (throwaway) shipment, or open **Riwayat**. Search, exporter chips, and year filter narrow the list.
- [ ] **Hapus** on a history row (and **Hapus Pengiriman** on the detail view) asks to confirm, names the shipment + folder.
- [ ] On confirm, the folder goes to the **Recycle Bin** (check it's there and recoverable), the record disappears, and its notifications/checks are gone too.
- [ ] A shipment whose folder is open in Excel/Explorer refuses to delete and says so, keeping the record.

## 9. Settings & theme
- [ ] Change the **BNCT interval** and Save; the next auto-poll respects it.
- [ ] Toggle **theme** (Terang/Gelap) and Save. The whole app re-themes; banners, cards, sidebar hover, and the × all remain readable in both.
- [ ] Reopen the app — Drive path, API key, theme, and interval all persisted.

## 10. Shared-DB sanity (if more than one machine)
- [ ] A second worker's app sees the same active shipments and notifications (shared DB on Drive).

---

**Result:** _____ / all boxes. Failures: _______________________________
