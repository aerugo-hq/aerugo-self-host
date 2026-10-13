-- Seed data for M0. Every chunk mentions "incident" so one search touches every document;
-- what each user gets back is decided purely by scope, ACLs, and the authorization engine.
--
--   document     workspace  index ACL          OpenFGA viewer               expected visibility
--   pay-runbook  payments   group:payments     via repo container           alice, carol
--   fin-close    finance    group:finance      group:finance                bob, carol
--   handbook     commons    org:acme           org:acme#member              everyone
--   pay-drift    payments   group:payments     (none: deliberate drift)     nobody (post-check drops it)
--   legal-hold   legal      org:acme           org:acme#member              dave only (legal is private)
SET search_path = kl, public;

INSERT INTO orgs (slug, name) VALUES ('acme', 'Acme Inc');

INSERT INTO workspaces (org_id, slug, name, discoverability)
SELECT o.id, v.slug, v.name, v.disc
FROM orgs o
CROSS JOIN (VALUES
  ('payments', 'Payments',    'org'),
  ('finance',  'Finance',     'org'),
  ('commons',  'Org commons', 'org'),
  ('legal',    'Legal',       'private')
) AS v(slug, name, disc)
WHERE o.slug = 'acme';

INSERT INTO documents (org_id, source_kind, source_uri, source_version, external_key, title,
                       content_hash, classification, acl_principals)
SELECT o.id, d.kind, d.uri, d.ver, d.key, d.title, md5(d.key || d.ver), d.cls, d.acl
FROM orgs o
CROSS JOIN (VALUES
  ('github',     'https://github.com/acme/payments-api/blob/main/docs/incident-response.md', 'a1b2c3d',
   'pay-runbook', 'Payments API incident response', 'internal',     ARRAY['group:payments']),
  ('gdrive',     'https://drive.google.com/file/d/fin-close-checklist', 'r42',
   'fin-close',   'Month-end close checklist',      'confidential', ARRAY['group:finance']),
  ('confluence', 'https://wiki.acme.test/security/incident-reporting', 'v7',
   'handbook',    'Security incident reporting',    'internal',     ARRAY['org:acme']),
  ('github',     'https://github.com/acme/payments-api/blob/main/docs/legacy-runbook.md', '9f8e7d6',
   'pay-drift',   'Legacy payments runbook',        'internal',     ARRAY['group:payments']),
  ('gdrive',     'https://drive.google.com/file/d/litigation-hold', 'r3',
   'legal-hold',  'Litigation hold procedure',      'confidential', ARRAY['org:acme'])
) AS d(kind, uri, ver, key, title, cls, acl)
WHERE o.slug = 'acme';

INSERT INTO workspace_documents (workspace_id, document_id, org_id)
SELECT w.id, d.id, d.org_id
FROM documents d
JOIN workspaces w ON w.org_id = d.org_id
JOIN (VALUES
  ('pay-runbook', 'payments'),
  ('fin-close',   'finance'),
  ('handbook',    'commons'),
  ('pay-drift',   'payments'),
  ('legal-hold',  'legal')
) AS m(key, ws) ON m.key = d.external_key AND m.ws = w.slug;

INSERT INTO chunks (org_id, document_id, ordinal, heading_path, body, acl_principals)
SELECT d.org_id, d.id, c.ord, c.heading, c.body, d.acl_principals
FROM documents d
JOIN (VALUES
  ('pay-runbook', 0, 'Incident response > Paging',
   'When a payments incident is declared, page the payments on-call and open an incident channel.'),
  ('pay-runbook', 1, 'Incident response > Ledger',
   'During an incident, check ledger-db replication lag before failing over the payments API.'),
  ('fin-close',   0, 'Close > Exceptions',
   'Any incident that delays month-end close must be logged with the controller within one business day.'),
  ('handbook',    0, 'Security > Reporting',
   'Report any security incident to the security team within one hour of discovery.'),
  ('pay-drift',   0, 'Legacy > Escalation',
   'Old incident escalation path for the payments API, kept for reference only.'),
  ('legal-hold',  0, 'Legal > Holds',
   'When an incident may lead to litigation, legal issues a hold notice to every custodian.')
) AS c(key, ord, heading, body) ON c.key = d.external_key;
