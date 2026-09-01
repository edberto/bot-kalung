# Bot Kalung — mobile PWA

A small, installable web app that reads the hosted Supabase DB the server worker
fills. No desktop, no Excel, no `G:` drive — it talks straight to Supabase over
HTTPS. Five tabs across the bottom:

- **Monitor** (home) — the operational view: active **voyages grouped by BNCT
  phase** (Akan Berangkat / Sudah Sandar / Terjadwal / Belum Terjadwal / Belum
  ada kapal). Each voyage card shows its status ops line (loading/discharge sisa,
  ATB, or open-stack/closing) + ETD, and under it every shipment riding that
  voyage with a **doc-progress bar** (n/n final) and **container-at-stack** count
  (📦 n/m). Answers "what's each voyage's status and are its shipments ready?" at
  a glance. Supersedes the old standalone Kapal board.
- **Aktif** — active shipments grouped by exporter, sorted by ETD → tap for
  detail: live **BNCT vessel status** (loading/discharge/restow, ETD, ATB,
  closing, departing), **action items** (status dropdown, add/delete),
  **containers** (live status, copy number, edit, "Buka di BNCT"), **notes**.
  Header actions: **Ubah ETD** (whole voyage), **Buka Folder** (Drive link),
  **Tandai Selesai**.
- **Kalender** — month grid of ETDs + action-item due dates, tap a day for its
  agenda.
- **Notif** — notification feed with an unread badge, mark-read, tap-to-shipment.
- **Lainnya** — **Forum** (shared team notes: post a note, optionally tie it to a
  shipment or a voyage — voyage notes list the impacted shipments — and reply in a
  thread; everyone on the team sees them), **Kelola Kapal** (add/remove monitored
  vessels + voyages; the server fills each vessel's 3-voyage window on its next
  poll), Riwayat (completed shipments + filters), Cari (container / party search),
  Log Aktivitas (audit trail), **Pengaturan** (dark mode, account / change
  password, ntfy toggle, server status), and sign-out.

Staying **desktop-only** (by design): delete shipment & resequence (they rewrite
Drive folders / Excel / PDFs), open-Excel-locally, and manual scan (the server
already scans every 30 min).

It is plain static files (`index.html` + a service worker + a manifest). Nothing
to build. It cannot be a Claude Artifact — the Artifact sandbox blocks calls to
Supabase — so it's hosted as static files instead.

## What's in here
| File | Purpose |
|------|---------|
| `index.html` | The whole app (UI + logic + `supabase-js` from a CDN). |
| `manifest.webmanifest` | Makes it installable ("Add to Home Screen"). |
| `sw.js` | Service worker — caches the app shell so it opens offline. |
| `icon-192.png`, `icon-512.png` | App icons. |

The Supabase **URL** and **publishable key** are hard-coded near the top of
`index.html`. That's correct and safe: the publishable key is designed to ship in
the browser — Row-Level Security (already enabled on every table) is what
actually protects the data, and only a signed-in user gets read/write.

## 1. Create your login (one-time, ~1 min)
The app requires you to sign in; there's no public access. Create your account in
the Supabase dashboard (this keeps your password out of any chat/transcript):

1. Supabase dashboard → **Authentication** → **Users** → **Add user** →
   **Create new user**.
2. Enter your email + a password. Tick **Auto Confirm User** (so you can log in
   immediately without an email round-trip).
3. That email + password is what you type on the app's login screen.

## 2. Try it locally first
From this `pwa/` folder:
```bash
python -m http.server 8080
```
Open `http://localhost:8080` on your PC, sign in, confirm your shipments load.
(A service worker needs `http://localhost` or HTTPS — opening `index.html` as a
`file://` won't register it, but the app still works.)

## 3. Host it (free, HTTPS, installable)
Any static host works. Two easy $0 options:

### Cloudflare Pages (drag-and-drop)
1. <https://dash.cloudflare.com> → **Workers & Pages** → **Create** → **Pages** →
   **Upload assets**.
2. Drag this whole `pwa/` folder in. Deploy.
3. You get `https://<name>.pages.dev`. Open it on your phone.

### Netlify (drag-and-drop)
1. <https://app.netlify.com/drop>.
2. Drag the `pwa/` folder onto the page. Done — you get an HTTPS URL.

Either way: open the URL on your phone → browser menu → **Add to Home Screen**.
It then launches full-screen like a native app, with the BK icon.

## 4. Continuous deployment from GitHub (recommended — no more manual uploads)
Link the repo to Netlify once, and every push to **master** deploys the PWA
automatically. The repo root has a `netlify.toml` that publishes only `pwa/` with
no build step, so nothing else in the repo is served.

**Link your existing site (keeps the same URL):**
1. Netlify → your site → **Site configuration → Build & deploy → Continuous
   deployment** → **Link repository** (a.k.a. "Link site to Git").
2. Authorize **GitHub** and pick **`edberto/bot-kalung`**.
3. Netlify reads `netlify.toml` automatically — **Publish directory** `pwa`,
   **Build command** empty, **Production branch** `master`. Save.
4. Push to master → Netlify builds and deploys within ~30s. Watch **Deploys**.

(Or **Add new site → Import an existing project → GitHub** to create a fresh site
from the repo; you can rename it afterward.)

When you change `index.html`, bump the `SHELL` cache name in `sw.js` (e.g.
`kalung-shell-v11`) so phones fetch the new shell instead of a stale cached copy.

**Manual fallback:** you can still drag the `pwa/` folder onto
<https://app.netlify.com/drop> if you ever want a one-off deploy.

## Troubleshooting
- **Login fails ("Invalid login credentials")** — the user wasn't created, or
  wasn't confirmed. Re-check step 1 and tick *Auto Confirm User*.
- **"Could not find a relationship between 'shipments' and 'action_items'"** —
  PostgREST's schema cache is stale. In the Supabase dashboard → **Database** →
  (or SQL editor) run `NOTIFY pgrst, 'reload schema';`, or just toggle any column
  comment; it refreshes within a minute.
- **Empty list but you know shipments are active** — the server worker may not
  have run its first scan yet, or RLS is blocking. Confirm you're signed in (the
  `authenticated` role is what the RLS policies grant).
- **Edits don't stick** — same RLS check; the `app_rw` policy must exist on that
  table for the `authenticated` role.
