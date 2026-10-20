-- F8: who is asking. A directory listing also says each person's title, department, office, city, state, and country,
-- and their manager's email, so answers fit the asker's role and place and contacts resolve to a role's current holder.
-- Metadata a company's directory already shows its people; the table's row-level security is 0037's.
SET search_path = kl, public;

ALTER TABLE directory_people
  ADD COLUMN title text CHECK (title IS NULL OR length(title) <= 200),
  ADD COLUMN department text CHECK (department IS NULL OR length(department) <= 200),
  ADD COLUMN office text CHECK (office IS NULL OR length(office) <= 200),
  ADD COLUMN city text CHECK (city IS NULL OR length(city) <= 200),
  ADD COLUMN state text CHECK (state IS NULL OR length(state) <= 200),
  ADD COLUMN country text CHECK (country IS NULL OR length(country) <= 200),
  ADD COLUMN manager_email text CHECK (manager_email IS NULL OR (length(manager_email) <= 254 AND manager_email = lower(manager_email)));
CREATE INDEX directory_people_title_idx ON directory_people (org_id, lower(title)) WHERE active;
