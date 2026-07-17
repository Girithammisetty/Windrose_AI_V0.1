// Package worm is audit-service's real object-storage adapter for WORM export
// (AUD-FR-020..023). It speaks the real S3 API against MinIO (deploy:
// localhost:9000) via minio-go, writing objects under Object-Lock compliance
// retention so sealed batches cannot be altered or deleted for the retention
// window. There is no in-memory mode in the runtime path.
package worm

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"net/url"
	"time"

	"github.com/minio/minio-go/v7"
	"github.com/minio/minio-go/v7/pkg/credentials"
)

// Client wraps a MinIO/S3 client bound to the audit bucket.
type Client struct {
	mc             *minio.Client
	bucket         string
	retentionYears int
}

// Config configures the client.
type Config struct {
	Endpoint       string // host:port, e.g. localhost:9000
	AccessKey      string
	SecretKey      string
	UseSSL         bool
	Bucket         string
	RetentionYears int // default 7 (AUD-FR-020)
}

// New builds a Client.
func New(cfg Config) (*Client, error) {
	if cfg.RetentionYears <= 0 {
		cfg.RetentionYears = 7
	}
	mc, err := minio.New(cfg.Endpoint, &minio.Options{
		Creds:  credentials.NewStaticV4(cfg.AccessKey, cfg.SecretKey, ""),
		Secure: cfg.UseSSL,
	})
	if err != nil {
		return nil, err
	}
	return &Client{mc: mc, bucket: cfg.Bucket, retentionYears: cfg.RetentionYears}, nil
}

// EnsureBucket creates the audit bucket with Object Lock enabled (idempotent).
// Object Lock requires versioning; MakeBucket with ObjectLocking sets both.
func (c *Client) EnsureBucket(ctx context.Context) error {
	exists, err := c.mc.BucketExists(ctx, c.bucket)
	if err != nil {
		return fmt.Errorf("bucket exists: %w", err)
	}
	if exists {
		return nil
	}
	if err := c.mc.MakeBucket(ctx, c.bucket, minio.MakeBucketOptions{ObjectLocking: true}); err != nil {
		return fmt.Errorf("make bucket: %w", err)
	}
	return nil
}

// Bucket returns the bucket name.
func (c *Client) Bucket() string { return c.bucket }

// PutWORM writes data under Object-Lock COMPLIANCE mode with the configured
// retention (AUD-FR-020/021): the object cannot be overwritten or deleted until
// retention expires — even by root.
func (c *Client) PutWORM(ctx context.Context, key string, data []byte, contentType string) (string, error) {
	retainUntil := time.Now().UTC().AddDate(c.retentionYears, 0, 0)
	info, err := c.mc.PutObject(ctx, c.bucket, key, bytes.NewReader(data), int64(len(data)),
		minio.PutObjectOptions{
			ContentType:  contentType,
			Mode:         minio.Compliance,
			RetainUntilDate: retainUntil,
		})
	if err != nil {
		return "", fmt.Errorf("put worm %s: %w", key, err)
	}
	return info.ETag, nil
}

// PutObject writes data without retention (compliance-pack zips live on a
// separate, non-locked prefix so they can expire on their own policy).
func (c *Client) PutObject(ctx context.Context, key string, data []byte, contentType string) (string, error) {
	info, err := c.mc.PutObject(ctx, c.bucket, key, bytes.NewReader(data), int64(len(data)),
		minio.PutObjectOptions{ContentType: contentType})
	if err != nil {
		return "", fmt.Errorf("put %s: %w", key, err)
	}
	return info.ETag, nil
}

// Get reads an object fully (verification/tests).
func (c *Client) Get(ctx context.Context, key string) ([]byte, error) {
	obj, err := c.mc.GetObject(ctx, c.bucket, key, minio.GetObjectOptions{})
	if err != nil {
		return nil, err
	}
	defer obj.Close()
	return io.ReadAll(obj)
}

// Retention reports the object-lock mode + retain-until for a key (proves WORM
// in tests/audits).
func (c *Client) Retention(ctx context.Context, key string) (string, *time.Time, error) {
	mode, until, err := c.mc.GetObjectRetention(ctx, c.bucket, key, "")
	if err != nil {
		return "", nil, err
	}
	modeStr := ""
	if mode != nil {
		modeStr = string(*mode)
	}
	return modeStr, until, nil
}

// PresignGet returns a signed download URL (AUD-FR-023/032 auditor access).
func (c *Client) PresignGet(ctx context.Context, key string, expiry time.Duration) (string, error) {
	u, err := c.mc.PresignedGetObject(ctx, c.bucket, key, expiry, url.Values{})
	if err != nil {
		return "", err
	}
	return u.String(), nil
}

// URI returns the s3:// URI for a key.
func (c *Client) URI(key string) string { return "s3://" + c.bucket + "/" + key }
