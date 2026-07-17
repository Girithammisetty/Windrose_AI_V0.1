// Package blob is case-service's real object-storage adapter for case evidence
// attachments (task #77). It speaks the real S3 API against MinIO (deploy:
// localhost:9000) via minio-go — the same adapter audit-service uses for WORM
// export, minus the object-lock retention (evidence is deletable). There is no
// in-memory mode in the runtime path; the tenant-isolated pointer/metadata rows
// live in Postgres (case_evidence), the bytes live here.
package blob

import (
	"bytes"
	"context"
	"fmt"
	"io"

	"github.com/minio/minio-go/v7"
	"github.com/minio/minio-go/v7/pkg/credentials"
)

// Config configures the evidence object store.
type Config struct {
	Endpoint  string // host:port, e.g. localhost:9000
	AccessKey string
	SecretKey string
	UseSSL    bool
	Bucket    string // e.g. windrose-case-evidence
}

// MinioEvidence is a MinIO/S3-backed evidence store bound to one bucket.
type MinioEvidence struct {
	mc     *minio.Client
	bucket string
}

// NewMinioEvidence builds the client and ensures the bucket exists.
func NewMinioEvidence(ctx context.Context, cfg Config) (*MinioEvidence, error) {
	mc, err := minio.New(cfg.Endpoint, &minio.Options{
		Creds:  credentials.NewStaticV4(cfg.AccessKey, cfg.SecretKey, ""),
		Secure: cfg.UseSSL,
	})
	if err != nil {
		return nil, err
	}
	c := &MinioEvidence{mc: mc, bucket: cfg.Bucket}
	if err := c.ensureBucket(ctx); err != nil {
		return nil, err
	}
	return c, nil
}

func (c *MinioEvidence) ensureBucket(ctx context.Context) error {
	exists, err := c.mc.BucketExists(ctx, c.bucket)
	if err != nil {
		return fmt.Errorf("bucket exists: %w", err)
	}
	if exists {
		return nil
	}
	if err := c.mc.MakeBucket(ctx, c.bucket, minio.MakeBucketOptions{}); err != nil {
		return fmt.Errorf("make bucket: %w", err)
	}
	return nil
}

// Put writes an evidence object at key with its declared content type.
func (c *MinioEvidence) Put(ctx context.Context, key string, data []byte, contentType string) error {
	_, err := c.mc.PutObject(ctx, c.bucket, key, bytes.NewReader(data), int64(len(data)),
		minio.PutObjectOptions{ContentType: contentType})
	if err != nil {
		return fmt.Errorf("put %s: %w", key, err)
	}
	return nil
}

// Get reads an evidence object fully (streamed to the caller by the handler).
func (c *MinioEvidence) Get(ctx context.Context, key string) ([]byte, error) {
	obj, err := c.mc.GetObject(ctx, c.bucket, key, minio.GetObjectOptions{})
	if err != nil {
		return nil, err
	}
	defer func() { _ = obj.Close() }()
	return io.ReadAll(obj)
}
