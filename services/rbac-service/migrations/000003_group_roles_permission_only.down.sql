ALTER TABLE group_roles DROP CONSTRAINT IF EXISTS fk_group_roles_permission_group;
ALTER TABLE group_roles DROP COLUMN IF EXISTS group_type;
ALTER TABLE groups DROP CONSTRAINT IF EXISTS uq_groups_id_type;
