-- BRD §4: group_roles bindings are permission-groups-only. Previously enforced
-- only in the application layer (store.BindGroupRole); this pins it in the
-- schema via a composite FK to (groups.id, groups.group_type) with the type
-- fixed to 'permission', so a content group can never be bound to a role even
-- if a code path forgets the check.

ALTER TABLE groups ADD CONSTRAINT uq_groups_id_type UNIQUE (id, group_type);

-- Existing rows are all permission-group bindings (system seeds + API path),
-- so the DEFAULT backfills them correctly and the CHECK holds.
ALTER TABLE group_roles
    ADD COLUMN group_type TEXT NOT NULL DEFAULT 'permission'
    CHECK (group_type = 'permission');

ALTER TABLE group_roles
    ADD CONSTRAINT fk_group_roles_permission_group
    FOREIGN KEY (group_id, group_type)
    REFERENCES groups (id, group_type) ON DELETE CASCADE;
