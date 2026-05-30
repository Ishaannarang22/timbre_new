-- timbre dashboard — postgres schema for the postpartum maternal voice-agent demo.
-- Paste this into the Supabase SQL editor (project > SQL > new query) and run.
-- Demo-grade: synthetic patients only. No real PHI. Service-role-only writes from the
-- Next.js API routes; anon reads gated by RLS for the dashboard UI.

create extension if not exists "pgcrypto";

-- ---------- ENUMS -----------------------------------------------------------
do $$ begin
  create type language_code as enum ('en', 'es');
  exception when duplicate_object then null;
end $$;
do $$ begin
  create type birth_type as enum ('vaginal', 'c_section', 'vbac');
  exception when duplicate_object then null;
end $$;
do $$ begin
  create type feeding_type as enum ('breast', 'formula', 'combo');
  exception when duplicate_object then null;
end $$;
do $$ begin
  create type billing_status as enum ('paid', 'processing', 'due', 'overdue', 'in_dispute');
  exception when duplicate_object then null;
end $$;
do $$ begin
  create type appointment_status as enum ('scheduled', 'completed', 'cancelled', 'no_show');
  exception when duplicate_object then null;
end $$;
do $$ begin
  create type prescription_status as enum ('active', 'discontinued', 'expired');
  exception when duplicate_object then null;
end $$;
do $$ begin
  create type pickup_status as enum ('ready', 'processing', 'picked_up', 'not_picked_up', 'on_backorder');
  exception when duplicate_object then null;
end $$;
do $$ begin
  create type call_direction as enum ('inbound', 'outbound');
  exception when duplicate_object then null;
end $$;
do $$ begin
  create type call_status as enum ('queued', 'in_progress', 'completed', 'escalated', 'abandoned', 'failed');
  exception when duplicate_object then null;
end $$;
do $$ begin
  create type bleeding_level as enum ('none', 'spotting', 'light', 'moderate', 'heavy', 'concerning');
  exception when duplicate_object then null;
end $$;
do $$ begin
  create type phq_instrument as enum ('phq2', 'phq9', 'epds');
  exception when duplicate_object then null;
end $$;
do $$ begin
  create type adherence_barrier as enum ('cost', 'transport', 'side_effects', 'forgot', 'no_pharmacy', 'concerns', 'other', 'none');
  exception when duplicate_object then null;
end $$;
do $$ begin
  create type escalation_severity as enum ('urgent', 'warning', 'info');
  exception when duplicate_object then null;
end $$;
do $$ begin
  create type escalation_category as enum ('maternal', 'pediatric', 'crisis', 'concierge');
  exception when duplicate_object then null;
end $$;
do $$ begin
  create type escalation_status as enum ('new', 'acknowledged', 'resolved', 'dismissed');
  exception when duplicate_object then null;
end $$;
do $$ begin
  create type feedback_category as enum ('clinical', 'billing', 'scheduling', 'facilities', 'staff', 'communication', 'other');
  exception when duplicate_object then null;
end $$;
do $$ begin
  create type feedback_sentiment as enum ('positive', 'neutral', 'negative');
  exception when duplicate_object then null;
end $$;
do $$ begin
  create type eval_persona as enum ('the_contradiction', 'cost_blocker', 'proxy_responder', 'ambiguous_healer');
  exception when duplicate_object then null;
end $$;
do $$ begin
  create type eval_criterion as enum ('node_transition_accuracy', 'context_strategy', 'tool_call_latency_ms', 'global_function_reliability', 'pii_redaction', 'escalation_correctness');
  exception when duplicate_object then null;
end $$;


-- ---------- CORE PATIENT TABLES --------------------------------------------
create table if not exists patient (
  id              uuid primary key default gen_random_uuid(),
  name            text not null,
  preferred_name  text,
  dob             date not null,
  language        language_code not null default 'en',
  phone           text not null,
  email           text,
  address_line    text,
  city            text,
  state           text,
  zip             text,
  insurance       text,
  primary_provider text,
  doula_assigned  text,
  birth_date      date,
  birth_type      birth_type,
  discharge_date  date,
  hospital        text default 'Raya Memorial',
  notes           text,
  created_at      timestamptz not null default now()
);

create table if not exists newborn (
  id              uuid primary key default gen_random_uuid(),
  patient_id      uuid not null references patient(id) on delete cascade,
  name            text,
  dob             date not null,
  sex             text,
  birth_weight_g  integer,
  gestational_age_weeks numeric(4,1),
  feeding_type    feeding_type default 'breast',
  pediatrician    text,
  notes           text,
  created_at      timestamptz not null default now()
);

create table if not exists billing (
  id              uuid primary key default gen_random_uuid(),
  patient_id      uuid not null references patient(id) on delete cascade,
  service_description text not null,
  amount_cents    integer not null,
  status          billing_status not null default 'processing',
  service_date    date,
  due_date        date,
  paid_date       date,
  processing_notes text,
  insurance_claim_id text,
  created_at      timestamptz not null default now()
);

create table if not exists appointment (
  id              uuid primary key default gen_random_uuid(),
  patient_id      uuid not null references patient(id) on delete cascade,
  provider_name   text not null,
  provider_specialty text,
  scheduled_at    timestamptz not null,
  duration_min    integer default 30,
  appointment_type text,
  status          appointment_status not null default 'scheduled',
  location        text,
  notes           text,
  created_at      timestamptz not null default now()
);

create table if not exists prescription (
  id              uuid primary key default gen_random_uuid(),
  patient_id      uuid not null references patient(id) on delete cascade,
  medication      text not null,
  dosage          text,
  instructions    text,
  prescribed_date date not null default current_date,
  prescribed_by   text,
  status          prescription_status not null default 'active',
  pharmacy        text,
  pickup_status   pickup_status default 'ready',
  notes           text,
  created_at      timestamptz not null default now()
);


-- ---------- CALL + ANSWER TABLES -------------------------------------------
create table if not exists call (
  id              uuid primary key default gen_random_uuid(),
  patient_id      uuid not null references patient(id) on delete cascade,
  call_sid        text unique,
  direction       call_direction not null default 'outbound',
  status          call_status not null default 'queued',
  language        language_code not null default 'en',
  scheduled_at    timestamptz,
  started_at      timestamptz,
  ended_at        timestamptz,
  current_node    text,
  transcript_redacted text,
  summary         text,
  flow_name       text default 'postpartum_v1',
  created_at      timestamptz not null default now()
);

create table if not exists recovery_answer (
  id              uuid primary key default gen_random_uuid(),
  call_id         uuid not null references call(id) on delete cascade,
  patient_id      uuid not null references patient(id) on delete cascade,
  bleeding        bleeding_level,
  pain_score      integer check (pain_score between 0 and 10),
  incision_status text,
  mobility_status text,
  urination_status text,
  emotional_state text,
  notes           text,
  recorded_at     timestamptz not null default now()
);

create table if not exists newborn_answer (
  id              uuid primary key default gen_random_uuid(),
  call_id         uuid not null references call(id) on delete cascade,
  newborn_id      uuid not null references newborn(id) on delete cascade,
  feeding_count_24h integer,
  wet_diapers_24h integer,
  dirty_diapers_24h integer,
  jaundice_observed boolean,
  fever           boolean,
  fever_temp_f    numeric(4,1),
  sleep_pattern   text,
  weight_check_oz integer,
  notes           text,
  recorded_at     timestamptz not null default now()
);

create table if not exists phq_score (
  id              uuid primary key default gen_random_uuid(),
  call_id         uuid not null references call(id) on delete cascade,
  patient_id      uuid not null references patient(id) on delete cascade,
  instrument      phq_instrument not null,
  score           integer not null,
  responses       jsonb,
  elevated        boolean not null default false,
  suicidal_ideation boolean not null default false,
  recorded_at     timestamptz not null default now()
);

create table if not exists adherence (
  id              uuid primary key default gen_random_uuid(),
  call_id         uuid not null references call(id) on delete cascade,
  patient_id      uuid not null references patient(id) on delete cascade,
  prescription_id uuid references prescription(id) on delete set null,
  medication      text,
  picked_up       boolean,
  taking_as_prescribed boolean,
  barrier         adherence_barrier default 'none',
  barrier_notes   text,
  recorded_at     timestamptz not null default now()
);

create table if not exists csat (
  id              uuid primary key default gen_random_uuid(),
  call_id         uuid not null references call(id) on delete cascade,
  patient_id      uuid not null references patient(id) on delete cascade,
  rating          integer not null check (rating between 1 and 5),
  qualitative_summary text,
  recorded_at     timestamptz not null default now()
);

create table if not exists escalation (
  id              uuid primary key default gen_random_uuid(),
  call_id         uuid references call(id) on delete set null,
  patient_id      uuid not null references patient(id) on delete cascade,
  severity        escalation_severity not null default 'urgent',
  category        escalation_category not null default 'maternal',
  trigger_phrase  text,
  trigger_text    text,
  transcript_excerpt text,
  status          escalation_status not null default 'new',
  assigned_to     text,
  acknowledged_at timestamptz,
  resolved_at     timestamptz,
  resolution_notes text,
  created_at      timestamptz not null default now()
);

create table if not exists feedback (
  id              uuid primary key default gen_random_uuid(),
  call_id         uuid references call(id) on delete set null,
  patient_id      uuid not null references patient(id) on delete cascade,
  category        feedback_category not null default 'other',
  note            text not null,
  sentiment       feedback_sentiment default 'neutral',
  quote_friendly  boolean default true,
  created_at      timestamptz not null default now()
);


-- ---------- CEKURA EVAL TABLES ---------------------------------------------
create table if not exists eval_run (
  id              uuid primary key default gen_random_uuid(),
  persona         eval_persona not null,
  flow_name       text not null default 'postpartum_v1',
  started_at      timestamptz not null default now(),
  completed_at    timestamptz,
  overall_score   numeric(5,2),
  status          text not null default 'running',
  cekura_run_id   text,
  transcript      text,
  notes           text
);

create table if not exists eval_result (
  id              uuid primary key default gen_random_uuid(),
  eval_run_id     uuid not null references eval_run(id) on delete cascade,
  criterion       eval_criterion not null,
  passed          boolean not null,
  score           numeric(5,2),
  details         jsonb,
  notes           text,
  created_at      timestamptz not null default now()
);


-- ---------- INDEXES ---------------------------------------------------------
create index if not exists idx_call_patient on call(patient_id);
create index if not exists idx_call_started on call(started_at desc);
create index if not exists idx_call_status on call(status);
create index if not exists idx_escalation_status on escalation(status, severity);
create index if not exists idx_escalation_created on escalation(created_at desc);
create index if not exists idx_feedback_category on feedback(category, created_at desc);
create index if not exists idx_billing_patient on billing(patient_id, status);
create index if not exists idx_appointment_patient on appointment(patient_id, scheduled_at);
create index if not exists idx_prescription_patient on prescription(patient_id, status);


-- ---------- REALTIME PUBLICATION -------------------------------------------
-- Enable Supabase Realtime on the tables the dashboard subscribes to.
-- If the publication already exists with these tables, Postgres no-ops.
alter publication supabase_realtime add table call;
alter publication supabase_realtime add table escalation;
alter publication supabase_realtime add table recovery_answer;
alter publication supabase_realtime add table newborn_answer;
alter publication supabase_realtime add table phq_score;
alter publication supabase_realtime add table adherence;
alter publication supabase_realtime add table csat;
alter publication supabase_realtime add table feedback;
alter publication supabase_realtime add table eval_run;
alter publication supabase_realtime add table eval_result;


-- ---------- ROW-LEVEL SECURITY ---------------------------------------------
-- Demo-grade: anon role gets READ on dashboard tables (so the SPA can subscribe);
-- writes are SERVICE-ROLE-ONLY (the Next.js API routes use the service-role key).
-- For real PHI you'd scope reads by org_id with JWT claims — out of scope for the demo.

alter table patient enable row level security;
alter table newborn enable row level security;
alter table billing enable row level security;
alter table appointment enable row level security;
alter table prescription enable row level security;
alter table call enable row level security;
alter table recovery_answer enable row level security;
alter table newborn_answer enable row level security;
alter table phq_score enable row level security;
alter table adherence enable row level security;
alter table csat enable row level security;
alter table escalation enable row level security;
alter table feedback enable row level security;
alter table eval_run enable row level security;
alter table eval_result enable row level security;

-- Anon read-only policies (demo). Replace with auth.uid()-scoped policies for prod.
do $$
declare t text;
begin
  for t in
    select unnest(array[
      'patient','newborn','billing','appointment','prescription',
      'call','recovery_answer','newborn_answer','phq_score','adherence',
      'csat','escalation','feedback','eval_run','eval_result'
    ])
  loop
    execute format('drop policy if exists "anon read %1$s" on %1$I', t);
    execute format('create policy "anon read %1$s" on %1$I for select using (true)', t);
  end loop;
end $$;
