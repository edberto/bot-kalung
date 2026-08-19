-- Team forum for the PWA (shared notes + threaded replies). Applied directly to
-- Supabase (this table is cloud-only; the desktop SQLite app does not use it).
-- Self-referential: a top-level note has parent_id NULL and an optional resource
-- link (link_type shipment|voyage); a reply sets parent_id to the note it answers.
-- RLS grants the authenticated role full access (trusted 3-worker team), matching
-- the app_rw policy on the other tables.

CREATE TABLE IF NOT EXISTS public.board_notes (
    id           text PRIMARY KEY,
    parent_id    text,                 -- NULL = top-level note; else the note this replies to
    author_email text,
    body         text NOT NULL,
    link_type    text,                 -- NULL | 'shipment' | 'voyage'
    shipment_id  text,                 -- when link_type = 'shipment'
    vessel_name  text,                 -- when link_type = 'voyage'
    voyage       text,                 -- when link_type = 'voyage'
    created_at   text NOT NULL,
    edited_at    text
);
CREATE INDEX IF NOT EXISTS idx_board_notes_parent  ON public.board_notes(parent_id);
CREATE INDEX IF NOT EXISTS idx_board_notes_created ON public.board_notes(created_at);

ALTER TABLE public.board_notes ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS app_rw ON public.board_notes;
CREATE POLICY app_rw ON public.board_notes
    FOR ALL TO authenticated USING (true) WITH CHECK (true);

NOTIFY pgrst, 'reload schema';   -- make PostgREST expose the new table immediately
