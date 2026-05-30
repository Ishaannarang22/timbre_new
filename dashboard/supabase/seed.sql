-- timbre dashboard — synthetic postpartum patient roster.
-- Paste into Supabase SQL editor AFTER running schema.sql.
-- ALL DATA IS FICTITIOUS. No real PHI. Names, phones, addresses, insurance: synthetic.

-- Stable UUIDs so the agent code can reference them by id.
-- (Generated once; safe to re-run because of `on conflict do nothing`.)

-- ----- PATIENTS -----------------------------------------------------------
insert into patient (id, name, preferred_name, dob, language, phone, email, city, state, zip, insurance, primary_provider, doula_assigned, birth_date, birth_type, discharge_date, hospital, notes) values
  ('11111111-1111-1111-1111-111111111111', 'María García López', 'María', '1992-04-18', 'es', '+18148268818', 'maria.garcia@example.com', 'Brooklyn', 'NY', '11211', 'BlueCross BlueShield NY', 'Dr. Anne Whitford', 'Carmen Reyes (Raya Doula)', '2026-05-24', 'c_section', '2026-05-27', 'Raya Memorial', 'Bilingual ES/EN. 6 days post c-section. First-time mom. Mild postpartum anxiety noted at discharge.'),
  ('22222222-2222-2222-2222-222222222222', 'Jessica Williams',     'Jess',  '1988-09-02', 'en', '+12125551402', 'jess.w@example.com',      'Queens',   'NY', '11378', 'Aetna PPO',              'Dr. Priya Shah',     'Naomi Brooks (Raya Doula)',  '2026-05-16', 'vaginal',   '2026-05-17', 'Raya Memorial', '14 days post. Second pregnancy. Smooth recovery so far.'),
  ('33333333-3333-3333-3333-333333333333', 'Aisha Patel',          'Aisha', '1994-12-21', 'en', '+19175550104', 'aisha.p@example.com',     'Manhattan','NY', '10025', 'UnitedHealthcare',       'Dr. Marcus Lee',     'Carmen Reyes (Raya Doula)',  '2026-05-26', 'c_section', '2026-05-29', 'Raya Memorial', '4 days post c-section. Combo feeding. Pain controlled on tylenol/ibuprofen.'),
  ('44444444-4444-4444-4444-444444444444', 'Lily Chen',            'Lily',  '1990-06-14', 'en', '+14155550110', 'lily.chen@example.com',   'Brooklyn', 'NY', '11215', 'Cigna OAP',              'Dr. Anne Whitford',  'Naomi Brooks (Raya Doula)',  '2026-05-09', 'vbac',      '2026-05-11', 'Raya Memorial', '21 days post VBAC (vaginal birth after c-section). Recovery going well.'),
  ('55555555-5555-5555-5555-555555555555', 'Sofía Rodríguez Mejía','Sofía', '1991-02-08', 'es', '+17185550117', 'sofia.r@example.com',     'Bronx',    'NY', '10458', 'Medicaid Managed Care',  'Dr. Priya Shah',     'Carmen Reyes (Raya Doula)',  '2026-05-20', 'c_section', '2026-05-23', 'Raya Memorial', '10 days post c-section. Spanish-preferred. Inquired about lactation help at discharge.'),
  ('66666666-6666-6666-6666-666666666666', 'Emma Thompson',        'Emma',  '1986-11-30', 'en', '+19295550133', 'emma.t@example.com',      'Manhattan','NY', '10003', 'Empire BCBS',            'Dr. Marcus Lee',     'Naomi Brooks (Raya Doula)',  '2026-05-23', 'vaginal',   '2026-05-24', 'Raya Memorial', '7 days post. History of depression. On sertraline 50mg. PHQ flagged on previous call.'),
  ('77777777-7777-7777-7777-777777777777', 'Priya Kumar',          'Priya', '1989-08-12', 'en', '+13475550144', 'priya.k@example.com',     'Queens',   'NY', '11355', 'Oscar Health',           'Dr. Anne Whitford',  'Carmen Reyes (Raya Doula)',  '2026-05-27', 'c_section', '2026-05-30', 'Raya Memorial', '3 days post c-section. Discharged today. First call scheduled tomorrow.'),
  ('88888888-8888-8888-8888-888888888888', 'Destiny Johnson',      'Destiny','1996-03-19','en', '+19175550155', 'destiny.j@example.com',   'Bronx',    'NY', '10456', 'Medicaid Managed Care',  'Dr. Priya Shah',     'Naomi Brooks (Raya Doula)',  '2026-05-24', 'vaginal',   '2026-05-25', 'Raya Memorial', '6 days post. Single mom. Flagged for cost-barrier counseling at intake.'),
  ('99999999-9999-9999-9999-999999999999', 'Hannah Kim',           'Hannah','1993-07-26', 'en', '+16465550166', 'hannah.k@example.com',    'Brooklyn', 'NY', '11201', 'Aetna PPO',              'Dr. Marcus Lee',     'Carmen Reyes (Raya Doula)',  '2026-05-12', 'vaginal',   '2026-05-13', 'Raya Memorial', '18 days post. Doing well; mild perineal soreness noted at last check.'),
  ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'Beatriz Hernández',    'Bea',   '1987-10-04', 'es', '+19295550177', 'beatriz.h@example.com',   'Queens',   'NY', '11375', 'Healthfirst Medicaid',   'Dr. Anne Whitford',  'Carmen Reyes (Raya Doula)',  '2026-05-18', 'c_section', '2026-05-21', 'Raya Memorial', '12 days post c-section. Spanish-preferred. Asked about pain med taper.')
on conflict (id) do nothing;

-- ----- NEWBORNS -----------------------------------------------------------
insert into newborn (id, patient_id, name, dob, sex, birth_weight_g, gestational_age_weeks, feeding_type, pediatrician) values
  ('b0000001-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'Sebastián García', '2026-05-24', 'M', 3402, 39.0, 'breast', 'Dr. Karen Liu'),
  ('b0000002-0000-0000-0000-000000000002', '22222222-2222-2222-2222-222222222222', 'Olivia Williams',  '2026-05-16', 'F', 3175, 40.1, 'breast', 'Dr. Karen Liu'),
  ('b0000003-0000-0000-0000-000000000003', '33333333-3333-3333-3333-333333333333', 'Arjun Patel',      '2026-05-26', 'M', 2950, 38.4, 'combo',  'Dr. Karen Liu'),
  ('b0000004-0000-0000-0000-000000000004', '44444444-4444-4444-4444-444444444444', 'Mei Chen',         '2026-05-09', 'F', 3530, 40.0, 'breast', 'Dr. Samuel Adeyemi'),
  ('b0000005-0000-0000-0000-000000000005', '55555555-5555-5555-5555-555555555555', 'Luis Rodríguez',   '2026-05-20', 'M', 3290, 39.2, 'combo',  'Dr. Samuel Adeyemi'),
  ('b0000006-0000-0000-0000-000000000006', '66666666-6666-6666-6666-666666666666', 'Oliver Thompson',  '2026-05-23', 'M', 3060, 38.6, 'breast', 'Dr. Karen Liu'),
  ('b0000007-0000-0000-0000-000000000007', '77777777-7777-7777-7777-777777777777', 'Aria Kumar',       '2026-05-27', 'F', 2880, 38.0, 'formula','Dr. Samuel Adeyemi'),
  ('b0000008-0000-0000-0000-000000000008', '88888888-8888-8888-8888-888888888888', 'Malik Johnson',    '2026-05-24', 'M', 3100, 39.4, 'combo',  'Dr. Karen Liu'),
  ('b0000009-0000-0000-0000-000000000009', '99999999-9999-9999-9999-999999999999', 'Yuna Kim',         '2026-05-12', 'F', 3220, 39.5, 'breast', 'Dr. Karen Liu'),
  ('b000000a-0000-0000-0000-00000000000a', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'Diego Hernández',  '2026-05-18', 'M', 3410, 39.6, 'breast', 'Dr. Samuel Adeyemi')
on conflict (id) do nothing;

-- ----- BILLING ------------------------------------------------------------
-- Mix of statuses so "where is my bill?" and "how much is it?" demos show variety.
-- María García has a 'processing' bill so the FAQ demo "it's currently processing" hits.
insert into billing (patient_id, service_description, amount_cents, status, service_date, due_date, paid_date, processing_notes) values
  ('11111111-1111-1111-1111-111111111111', 'Cesarean delivery + 3 day hospital stay', 124750, 'processing', '2026-05-24', '2026-06-24', null, 'Insurance claim filed 2026-05-28. Awaiting Anthem adjudication; estimated 7-10 business days. Patient responsibility will be the remaining balance after EOB.'),
  ('11111111-1111-1111-1111-111111111111', 'Anesthesia services',                       42000,'processing', '2026-05-24', '2026-06-24', null, 'Bundled with delivery claim. Same insurance review window.'),
  ('22222222-2222-2222-2222-222222222222', 'Vaginal delivery + 1 day stay',             68900, 'paid',       '2026-05-16', '2026-06-16', '2026-05-25', null),
  ('33333333-3333-3333-3333-333333333333', 'Cesarean delivery + 3 day hospital stay', 132000, 'due',        '2026-05-26', '2026-06-26', null, 'Patient responsibility: $612.40 after insurance. Statement mailed 2026-05-29.'),
  ('44444444-4444-4444-4444-444444444444', 'VBAC delivery + 2 day stay',                72500, 'paid',       '2026-05-09', '2026-06-09', '2026-05-20', null),
  ('55555555-5555-5555-5555-555555555555', 'Cesarean delivery + 3 day hospital stay', 119800, 'processing', '2026-05-20', '2026-06-20', null, 'Medicaid claim filed 2026-05-24. Routine processing.'),
  ('66666666-6666-6666-6666-666666666666', 'Vaginal delivery + 1 day stay',             71200, 'in_dispute', '2026-05-23', '2026-06-23', null, 'Patient disputed lactation consultant charge — $185. Sent to billing dept 2026-05-27.'),
  ('77777777-7777-7777-7777-777777777777', 'Cesarean delivery + 3 day hospital stay', 128400, 'processing', '2026-05-27', '2026-06-27', null, 'Just filed. ETA 10 business days.'),
  ('88888888-8888-8888-8888-888888888888', 'Vaginal delivery + 1 day stay',             64500, 'overdue',    '2026-05-24', '2026-06-24', null, 'Patient responsibility: $48.20 after Medicaid. Patient mentioned cost concerns at discharge — flagged for financial counseling.'),
  ('99999999-9999-9999-9999-999999999999', 'Vaginal delivery + 1 day stay',             69100, 'paid',       '2026-05-12', '2026-06-12', '2026-05-22', null),
  ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'Cesarean delivery + 3 day hospital stay', 121900, 'processing', '2026-05-18', '2026-06-18', null, 'Medicaid Healthfirst claim filed 2026-05-22.');

-- ----- APPOINTMENTS -------------------------------------------------------
-- Past discharge + upcoming postpartum follow-up + pediatric well-checks.
insert into appointment (patient_id, provider_name, provider_specialty, scheduled_at, duration_min, appointment_type, status, location, notes) values
  ('11111111-1111-1111-1111-111111111111', 'Dr. Anne Whitford',  'OB/GYN',         '2026-06-07 10:30:00-04', 30, 'postpartum_6week', 'scheduled', 'Raya Memorial OB Clinic',     '6-week postpartum visit.'),
  ('11111111-1111-1111-1111-111111111111', 'Dr. Karen Liu',      'Pediatrics',     '2026-06-01 14:00:00-04', 30, 'newborn_2week',     'scheduled', 'Raya Memorial Peds',         '2-week well-baby check.'),
  ('11111111-1111-1111-1111-111111111111', 'Carmen Reyes',       'Doula',          '2026-06-02 16:00:00-04', 60, 'doula_home_visit',  'scheduled', 'Patient home',                'Follow-up doula visit.'),
  ('22222222-2222-2222-2222-222222222222', 'Dr. Priya Shah',     'OB/GYN',         '2026-06-27 09:00:00-04', 30, 'postpartum_6week', 'scheduled', 'Raya Memorial OB Clinic',     '6-week postpartum visit.'),
  ('33333333-3333-3333-3333-333333333333', 'Dr. Marcus Lee',     'OB/GYN',         '2026-07-07 11:00:00-04', 30, 'postpartum_6week', 'scheduled', 'Raya Memorial OB Clinic',     '6-week postpartum visit.'),
  ('55555555-5555-5555-5555-555555555555', 'Carmen Reyes',       'Lactation',      '2026-06-01 13:00:00-04', 45, 'lactation_consult', 'scheduled', 'Patient home',                'Lactation consult (Spanish).'),
  ('66666666-6666-6666-6666-666666666666', 'Dr. Marcus Lee',     'OB/GYN',         '2026-07-04 10:00:00-04', 30, 'postpartum_6week', 'scheduled', 'Raya Memorial OB Clinic',     '6-week postpartum visit.'),
  ('66666666-6666-6666-6666-666666666666', 'Dr. Hannah Becker',  'Psychiatry',     '2026-06-06 15:00:00-04', 45, 'mental_health',     'scheduled', 'Raya Memorial Behavioral Health', 'Postpartum depression follow-up.'),
  ('88888888-8888-8888-8888-888888888888', 'Dr. Priya Shah',     'OB/GYN',         '2026-07-05 09:30:00-04', 30, 'postpartum_6week', 'scheduled', 'Raya Memorial OB Clinic',     '6-week postpartum visit.');

-- ----- PRESCRIPTIONS ------------------------------------------------------
-- Postpartum-common meds: iron, prenatal continuation, NSAIDs, opioid taper, SSRI, BP meds.
insert into prescription (patient_id, medication, dosage, instructions, prescribed_date, prescribed_by, status, pharmacy, pickup_status, notes) values
  ('11111111-1111-1111-1111-111111111111', 'Oxycodone 5mg',          '5mg tablet',  'One every 6 hours as needed for pain. Do not exceed 4 per day. Taper as tolerated.', '2026-05-27', 'Dr. Anne Whitford', 'active', 'CVS Bushwick',          'ready',         'Short course post c-section.'),
  ('11111111-1111-1111-1111-111111111111', 'Ferrous Sulfate 325mg',  '325mg tab',   'Take twice daily with food.',                                                          '2026-05-27', 'Dr. Anne Whitford', 'active', 'CVS Bushwick',          'ready',         'Iron repletion postpartum.'),
  ('11111111-1111-1111-1111-111111111111', 'Prenatal vitamin',       '1 tab daily', 'Continue throughout breastfeeding.',                                                   '2026-05-27', 'Dr. Anne Whitford', 'active', 'CVS Bushwick',          'picked_up',     null),
  ('22222222-2222-2222-2222-222222222222', 'Ibuprofen 600mg',        '600mg tab',   'Every 6 hours as needed for soreness.',                                                '2026-05-17', 'Dr. Priya Shah',    'active', 'Walgreens Astoria',     'picked_up',     null),
  ('33333333-3333-3333-3333-333333333333', 'Oxycodone 5mg',          '5mg tablet',  'One every 6 hours as needed. Taper after week 1.',                                     '2026-05-29', 'Dr. Marcus Lee',    'active', 'Duane Reade UWS',       'ready',         null),
  ('33333333-3333-3333-3333-333333333333', 'Stool softener',         '100mg',       'Daily until off opioids.',                                                             '2026-05-29', 'Dr. Marcus Lee',    'active', 'Duane Reade UWS',       'ready',         null),
  ('55555555-5555-5555-5555-555555555555', 'Ferrous Sulfate 325mg',  '325mg tab',   'Twice daily with food.',                                                               '2026-05-23', 'Dr. Priya Shah',    'active', 'Duane Reade Tremont',   'processing',    'Pharmacy noted insurance prior-auth check 2026-05-29.'),
  ('66666666-6666-6666-6666-666666666666', 'Sertraline 50mg',        '50mg tab',    'One daily. Continue through breastfeeding (safe).',                                    '2026-05-24', 'Dr. Marcus Lee',    'active', 'CVS Union Square',      'picked_up',     'Patient has hx of depression — continued per psychiatry.'),
  ('77777777-7777-7777-7777-777777777777', 'Oxycodone 5mg',          '5mg tablet',  'Every 6 hours as needed for pain.',                                                    '2026-05-30', 'Dr. Anne Whitford', 'active', 'CVS Flushing',          'ready',         'Discharged today; first call scheduled tomorrow.'),
  ('88888888-8888-8888-8888-888888888888', 'Ferrous Sulfate 325mg',  '325mg tab',   'Twice daily with food.',                                                               '2026-05-25', 'Dr. Priya Shah',    'active', 'Rite Aid Concourse',    'not_picked_up', 'Patient mentioned cost concerns — generic available, $4/month.'),
  ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'Methyldopa 250mg',       '250mg tab',  'Twice daily. Continue 6 weeks postpartum.',                                            '2026-05-21', 'Dr. Anne Whitford', 'active', 'CVS Forest Hills',      'ready',         'BP elevation late in pregnancy — taper at 6 wk visit.');

-- ----- PRE-SEED ONE LIVE-LOOKING CALL FOR THE DASHBOARD DEMO --------------
-- A queued call for María García so the "today's queue" page has someone to click into.
insert into call (id, patient_id, direction, status, language, scheduled_at, flow_name) values
  ('c0000001-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'outbound', 'queued', 'es', now() + interval '5 minutes', 'postpartum_v1'),
  ('c0000002-0000-0000-0000-000000000002', '33333333-3333-3333-3333-333333333333', 'outbound', 'queued', 'en', now() + interval '20 minutes', 'postpartum_v1'),
  ('c0000003-0000-0000-0000-000000000003', '55555555-5555-5555-5555-555555555555', 'outbound', 'queued', 'es', now() + interval '40 minutes', 'postpartum_v1'),
  ('c0000004-0000-0000-0000-000000000004', '66666666-6666-6666-6666-666666666666', 'outbound', 'queued', 'en', now() + interval '1 hour',   'postpartum_v1'),
  ('c0000005-0000-0000-0000-000000000005', '88888888-8888-8888-8888-888888888888', 'outbound', 'queued', 'en', now() + interval '1 hour 20 minutes', 'postpartum_v1')
on conflict (id) do nothing;

-- ----- A couple of HISTORICAL escalations + feedback so dashboards aren't empty -----
insert into escalation (call_id, patient_id, severity, category, trigger_phrase, trigger_text, transcript_excerpt, status, acknowledged_at, resolved_at) values
  (null, '66666666-6666-6666-6666-666666666666', 'urgent',  'crisis',   'suicidal ideation', 'PHQ-9 flagged 18 with positive Q9. Patient: "I just feel like everyone would be better off without me sometimes."', '...Emma: I''ve been having dark thoughts. Sometimes I think everyone would be better off without me...', 'resolved', now() - interval '3 days', now() - interval '3 days'),
  (null, '11111111-1111-1111-1111-111111111111', 'warning', 'maternal', 'incision pain',     'Mild surge in pain at incision site reported on day 5 call. No fever, no drainage. Routed for nurse callback within 4hrs.', '...María: It''s a little more sore today than yesterday but I think it''s okay...', 'resolved', now() - interval '1 day',  now() - interval '1 day');

insert into feedback (call_id, patient_id, category, note, sentiment) values
  (null, '22222222-2222-2222-2222-222222222222', 'staff',         'Nurse Maya was incredible — explained latching like five different ways until something clicked.', 'positive'),
  (null, '44444444-4444-4444-4444-444444444444', 'communication', 'Discharge papers were overwhelming. Would have helped to have one page of "call if you see X" big and clear.', 'negative'),
  (null, '99999999-9999-9999-9999-999999999999', 'scheduling',    'Took forever to get a callback when I left a voicemail about my stitches.', 'negative'),
  (null, 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'clinical',      'Carmen the doula visiting at home was the single best thing about the whole experience.', 'positive'),
  (null, '88888888-8888-8888-8888-888888888888', 'billing',       'I was confused about how much I''d owe — would love an estimate before the bill comes.', 'neutral');
