-- Status history per action item (who changed what, when, from which evidence).
-- A trigger records every status change, whatever made it: the worker (Drive
-- evidence, email replies, the approval queue) or someone in the PWA — so no
-- code path can forget to log. Source:
--   drive   the bot, from a file / red marks (auto_* changed with it)
--   email   the bot, from an email (mail_* changed with it)
--   manual  a person in the PWA (their login is in `who`)
--   bot     any other automatic change
-- Run once in the Supabase SQL editor. Safe to re-run.

CREATE TABLE IF NOT EXISTS public.action_item_history (
    id           bigserial PRIMARY KEY,
    item_id      text NOT NULL,
    shipment_id  text,
    from_status  text,
    to_status    text NOT NULL,
    at           text NOT NULL,          -- UTC, ISO like the rest of the app
    source       text NOT NULL,
    who          text,
    detail       text
);
CREATE INDEX IF NOT EXISTS idx_action_item_history_item ON public.action_item_history(item_id, at);

ALTER TABLE public.action_item_history ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS app_read ON public.action_item_history;
CREATE POLICY app_read ON public.action_item_history
    FOR SELECT TO authenticated USING (true);     -- written only by the trigger

CREATE OR REPLACE FUNCTION public.log_action_item_status() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    src text;
    det text;
    person text := nullif(current_setting('request.jwt.claims', true), '')::json ->> 'email';
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.status IS NOT DISTINCT FROM OLD.status THEN
        RETURN NEW;
    END IF;
    IF TG_OP = 'INSERT' THEN
        src := 'baru';
    ELSIF NEW.mail_at IS DISTINCT FROM OLD.mail_at THEN
        src := 'email';  det := NEW.mail_evidence;
    ELSIF NEW.auto_status IS DISTINCT FROM OLD.auto_status
          OR NEW.auto_evidence_ref IS DISTINCT FROM OLD.auto_evidence_ref THEN
        src := 'drive';  det := concat_ws(' · ', NEW.auto_evidence, NEW.auto_detail);
    ELSIF person IS NOT NULL THEN
        src := 'manual';
    ELSE
        src := 'bot';
    END IF;
    INSERT INTO public.action_item_history
        (item_id, shipment_id, from_status, to_status, at, source, who, detail)
    VALUES (NEW.id, NEW.shipment_id,
            CASE WHEN TG_OP = 'UPDATE' THEN OLD.status END, NEW.status,
            to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS'),
            src, coalesce(person, 'bot'), det);
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_action_item_status ON public.action_items;
CREATE TRIGGER trg_action_item_status
    AFTER INSERT OR UPDATE OF status ON public.action_items
    FOR EACH ROW EXECUTE FUNCTION public.log_action_item_status();

NOTIFY pgrst, 'reload schema';
