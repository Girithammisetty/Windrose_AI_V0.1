package store

import (
	"context"
	"errors"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	"github.com/windrose-ai/notification-service/internal/domain"
)

const templateCols = `id, tenant_id, key, channel, locale, version, subject_tpl, body_html_tpl, body_text_tpl, status, published_at, created_by, created_at`

func scanTemplate(row pgx.Row) (*domain.Template, error) {
	var t domain.Template
	err := row.Scan(&t.ID, &t.TenantID, &t.Key, &t.Channel, &t.Locale, &t.Version, &t.SubjectTpl,
		&t.BodyHTMLTpl, &t.BodyTextTpl, &t.Status, &t.PublishedAt, &t.CreatedBy, &t.CreatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	return &t, nil
}

// CreateTemplateVersion inserts a new template version. A nil tenant creates a
// platform default (written under the platform role).
func (s *PG) CreateTemplateVersion(ctx context.Context, t *domain.Template) error {
	run := func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `
			INSERT INTO templates (`+templateCols+`)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)`,
			t.ID, t.TenantID, t.Key, t.Channel, t.Locale, t.Version, t.SubjectTpl, t.BodyHTMLTpl,
			t.BodyTextTpl, t.Status, t.PublishedAt, t.CreatedBy, t.CreatedAt)
		if isUniqueViolation(err) {
			return ErrConflict
		}
		return err
	}
	if t.TenantID == nil {
		return s.withPlatform(ctx, run)
	}
	return s.withTenant(ctx, *t.TenantID, run)
}

// NextVersion returns the next version number for (tenant, key, channel, locale).
func (s *PG) NextVersion(ctx context.Context, tenant *uuid.UUID, key, channel, locale string) (int, error) {
	var v int
	run := func(tx pgx.Tx) error {
		coal := "00000000-0000-0000-0000-000000000000"
		if tenant != nil {
			coal = tenant.String()
		}
		return tx.QueryRow(ctx, `SELECT COALESCE(MAX(version),0)+1 FROM templates WHERE coalesce_tenant=$1 AND key=$2 AND channel=$3 AND locale=$4`,
			coal, key, channel, locale).Scan(&v)
	}
	var err error
	if tenant == nil {
		err = s.withPlatform(ctx, run)
	} else {
		err = s.withTenant(ctx, *tenant, run)
	}
	return v, err
}

// PublishTemplate marks a version published and archives prior published ones
// for the same (tenant,key,channel,locale) (NOTIF-FR-041).
func (s *PG) PublishTemplate(ctx context.Context, tenant *uuid.UUID, id uuid.UUID) (*domain.Template, error) {
	var out *domain.Template
	run := func(tx pgx.Tx) error {
		t, err := scanTemplate(tx.QueryRow(ctx, `SELECT `+templateCols+` FROM templates WHERE id=$1`, id))
		if err != nil {
			return err
		}
		coal := "00000000-0000-0000-0000-000000000000"
		if t.TenantID != nil {
			coal = t.TenantID.String()
		}
		if _, err := tx.Exec(ctx, `UPDATE templates SET status='archived' WHERE coalesce_tenant=$1 AND key=$2 AND channel=$3 AND locale=$4 AND status='published'`,
			coal, t.Key, t.Channel, t.Locale); err != nil {
			return err
		}
		if _, err := tx.Exec(ctx, `UPDATE templates SET status='published', published_at=now() WHERE id=$1`, id); err != nil {
			return err
		}
		out, err = scanTemplate(tx.QueryRow(ctx, `SELECT `+templateCols+` FROM templates WHERE id=$1`, id))
		return err
	}
	var err error
	if tenant == nil {
		err = s.withPlatform(ctx, run)
	} else {
		err = s.withTenant(ctx, *tenant, run)
	}
	return out, err
}

// ResolveTemplate resolves the published template for (key, channel, locale)
// with the tenant → platform precedence (NOTIF-FR-041), returning nil when none.
func (s *PG) ResolveTemplate(ctx context.Context, tenant uuid.UUID, key, channel, locale string) (*domain.Template, error) {
	var t *domain.Template
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		// Tenant override first, then platform default, highest version wins.
		row := tx.QueryRow(ctx, `
			SELECT `+templateCols+` FROM templates
			WHERE key=$1 AND channel=$2 AND locale=$3 AND status='published'
			  AND (tenant_id=$4 OR tenant_id IS NULL)
			ORDER BY (tenant_id IS NOT NULL) DESC, version DESC
			LIMIT 1`, key, channel, locale, tenant)
		var e error
		t, e = scanTemplate(row)
		if errors.Is(e, ErrNotFound) {
			t = nil
			return nil
		}
		return e
	})
	return t, err
}

// ListTemplateVersions lists versions for a key (tenant + platform).
func (s *PG) ListTemplateVersions(ctx context.Context, tenant uuid.UUID, key string) ([]*domain.Template, error) {
	var out []*domain.Template
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `SELECT `+templateCols+` FROM templates WHERE key=$1 AND (tenant_id=$2 OR tenant_id IS NULL) ORDER BY channel, locale, version DESC`, key, tenant)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			t, err := scanTemplate(rows)
			if err != nil {
				return err
			}
			out = append(out, t)
		}
		return rows.Err()
	})
	return out, err
}

// SeedTemplate is one platform-default template body set.
type SeedTemplate struct {
	Key, Channel, Locale, Subject, HTML, Text string
}

// SeedPlatformTemplates inserts+publishes platform-default templates (tenant
// NULL) idempotently under the platform role. Existing (key,channel,locale)
// published defaults are left untouched — safe to run every startup.
func (s *PG) SeedPlatformTemplates(ctx context.Context, seeds []SeedTemplate) error {
	return s.withPlatform(ctx, func(tx pgx.Tx) error {
		for _, sd := range seeds {
			var exists bool
			if err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM templates WHERE tenant_id IS NULL AND key=$1 AND channel=$2 AND locale=$3 AND status='published')`,
				sd.Key, sd.Channel, sd.Locale).Scan(&exists); err != nil {
				return err
			}
			if exists {
				continue
			}
			if _, err := tx.Exec(ctx, `
				INSERT INTO templates (id, tenant_id, key, channel, locale, version, subject_tpl, body_html_tpl, body_text_tpl, status, published_at, created_by)
				VALUES ($1, NULL, $2, $3, $4, 1, $5, $6, $7, 'published', now(), 'platform')`,
				domain.NewID(), sd.Key, sd.Channel, sd.Locale, sd.Subject, sd.HTML, sd.Text); err != nil {
				return err
			}
		}
		return nil
	})
}

// GetTemplate fetches a template by id (RLS-scoped).
func (s *PG) GetTemplate(ctx context.Context, tenant uuid.UUID, id uuid.UUID) (*domain.Template, error) {
	var t *domain.Template
	err := s.withTenant(ctx, tenant, func(tx pgx.Tx) error {
		var e error
		t, e = scanTemplate(tx.QueryRow(ctx, `SELECT `+templateCols+` FROM templates WHERE id=$1 AND (tenant_id=$2 OR tenant_id IS NULL)`, id, tenant))
		return e
	})
	return t, err
}
