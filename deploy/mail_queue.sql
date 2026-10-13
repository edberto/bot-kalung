-- Email approval queue (doc automation Phase 2, services/mail_inbox) for the
-- PWA's "Kotak Masuk". The worker creates the table on startup (db.py SCHEMA);
-- this file adds what the PWA needs: read every entry, and update one to
-- approve / reject it (or correct its type, folder, shipment). Inserts and the
-- saving stay with the worker. Run once in the Supabase SQL editor, AFTER the
-- worker has restarted with the new version.

ALTER TABLE public.mail_queue ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS app_read ON public.mail_queue;
CREATE POLICY app_read ON public.mail_queue
    FOR SELECT TO authenticated USING (true);
DROP POLICY IF EXISTS app_decide ON public.mail_queue;
CREATE POLICY app_decide ON public.mail_queue
    FOR UPDATE TO authenticated USING (true) WITH CHECK (true);

NOTIFY pgrst, 'reload schema';
