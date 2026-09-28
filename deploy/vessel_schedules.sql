-- Carrier sailing schedule (Tresnamuda route PDF) for the PWA. The worker also
-- creates this table on startup (it's in db.py SCHEMA); this file adds the RLS
-- read policy the PWA needs. Run once in the Supabase SQL editor.

CREATE TABLE IF NOT EXISTS public.vessel_schedules (
    vessel_name  text NOT NULL,
    voyage       text NOT NULL,
    closing_at   text,
    eta_belawan  text,
    etd_belawan  text,
    updated_at   text,
    PRIMARY KEY (vessel_name, voyage)
);

ALTER TABLE public.vessel_schedules ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS app_read ON public.vessel_schedules;
CREATE POLICY app_read ON public.vessel_schedules
    FOR SELECT TO authenticated USING (true);   -- written only by the worker

NOTIFY pgrst, 'reload schema';
