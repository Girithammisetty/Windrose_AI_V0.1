// Package gcpkms implements the real GCP Cloud KMS Signer adapter (BYO Infra
// Hardening Phase 2, docs/design/byo-infra-hardening.md).
//
// Private keys never leave Cloud KMS: Generate creates an asymmetric-sign
// CryptoKey (RSA_SIGN_PKCS1_2048_SHA256) under a pre-provisioned KeyRing and
// reads back its public key, already PEM-encoded SPKI by the API (no manual
// re-encoding needed, unlike Azure); Sign asks Cloud KMS to produce the RS256
// signature over a locally-computed SHA-256 digest (Cloud KMS's
// AsymmetricSign, like Azure Key Vault's Sign, operates on a digest, not a
// raw message like AWS KMS). Selected by SECRETS_BACKEND=gcp
// (cmd/server/main.go).
//
// No local Cloud KMS emulator exists, so this adapter is unit-tested against
// an injected fake implementing the narrow client interface below (standard
// practice for Google's generated gRPC clients, which have no first-party
// fake/emulator for KMS) — see internal/keys/signer_contract_test.go. The
// adapter code itself makes real SDK calls; only the client is substituted.
package gcpkms

import (
	"context"
	"crypto/sha256"
	"fmt"
	"time"

	kms "cloud.google.com/go/kms/apiv1"
	"cloud.google.com/go/kms/apiv1/kmspb"
	"github.com/google/uuid"
	"google.golang.org/api/option"
)

// Client is the narrow slice of *kms.KeyManagementClient's surface this
// adapter uses. The real client structurally satisfies it (see realClient);
// tests inject a fake via NewWithClient (no emulator exists for Cloud KMS).
type Client interface {
	CreateCryptoKey(ctx context.Context, req *kmspb.CreateCryptoKeyRequest, opts ...gaxCallOption) (*kmspb.CryptoKey, error)
	GetCryptoKeyVersion(ctx context.Context, req *kmspb.GetCryptoKeyVersionRequest, opts ...gaxCallOption) (*kmspb.CryptoKeyVersion, error)
	GetPublicKey(ctx context.Context, req *kmspb.GetPublicKeyRequest, opts ...gaxCallOption) (*kmspb.PublicKey, error)
	AsymmetricSign(ctx context.Context, req *kmspb.AsymmetricSignRequest, opts ...gaxCallOption) (*kmspb.AsymmetricSignResponse, error)
}

// gaxCallOption avoids importing github.com/googleapis/gax-go/v2 into this
// file's public surface just for the variadic opts parameter shape; the real
// client's methods are variadic over gax.CallOption, which satisfies this via
// type identity at the call site (see realClient below).
type gaxCallOption = interface{}

// Signer implements keys.Signer against GCP Cloud KMS asymmetric keys.
type Signer struct {
	client   Client
	keyRing  string // projects/*/locations/*/keyRings/* — pre-provisioned
	pollWait time.Duration
}

// New builds a Signer against a real Cloud KMS key ring (keyRing must already
// exist — Cloud KMS key rings can't be deleted, so Windrose doesn't create
// one per boot). Uses application-default credentials unless opts override.
func New(ctx context.Context, keyRing string, opts ...option.ClientOption) (*Signer, error) {
	c, err := kms.NewKeyManagementClient(ctx, opts...)
	if err != nil {
		return nil, fmt.Errorf("gcp kms: new client: %w", err)
	}
	return NewWithClient(&realClient{c}, keyRing), nil
}

// NewWithClient builds a Signer against an explicit Client — the seam the
// contract test suite uses to inject a fake (no local Cloud KMS emulator
// exists). Production code should use New instead.
func NewWithClient(c Client, keyRing string) *Signer {
	return &Signer{client: c, keyRing: keyRing, pollWait: 500 * time.Millisecond}
}

// realClient adapts *kms.KeyManagementClient's gax.CallOption-variadic
// methods to the narrow `client` interface above.
type realClient struct{ c *kms.KeyManagementClient }

func (r *realClient) CreateCryptoKey(ctx context.Context, req *kmspb.CreateCryptoKeyRequest, _ ...gaxCallOption) (*kmspb.CryptoKey, error) {
	return r.c.CreateCryptoKey(ctx, req)
}

func (r *realClient) GetCryptoKeyVersion(ctx context.Context, req *kmspb.GetCryptoKeyVersionRequest, _ ...gaxCallOption) (*kmspb.CryptoKeyVersion, error) {
	return r.c.GetCryptoKeyVersion(ctx, req)
}

func (r *realClient) GetPublicKey(ctx context.Context, req *kmspb.GetPublicKeyRequest, _ ...gaxCallOption) (*kmspb.PublicKey, error) {
	return r.c.GetPublicKey(ctx, req)
}

func (r *realClient) AsymmetricSign(ctx context.Context, req *kmspb.AsymmetricSignRequest, _ ...gaxCallOption) (*kmspb.AsymmetricSignResponse, error) {
	return r.c.AsymmetricSign(ctx, req)
}

// Generate creates a new asymmetric-sign RSA-2048 CryptoKey (with its
// auto-created version 1) and returns the CryptoKeyVersion resource name as
// kid + its PEM public key. Polls briefly for the version to leave
// PENDING_GENERATION, since real Cloud KMS generates key material
// asynchronously (usually sub-second for RSA-2048, per GCP docs).
func (s *Signer) Generate(ctx context.Context) (string, string, error) {
	key, err := s.client.CreateCryptoKey(ctx, &kmspb.CreateCryptoKeyRequest{
		Parent:      s.keyRing,
		CryptoKeyId: "identity-" + uuid.NewString(),
		CryptoKey: &kmspb.CryptoKey{
			Purpose: kmspb.CryptoKey_ASYMMETRIC_SIGN,
			VersionTemplate: &kmspb.CryptoKeyVersionTemplate{
				Algorithm: kmspb.CryptoKeyVersion_RSA_SIGN_PKCS1_2048_SHA256,
			},
		},
	})
	if err != nil {
		return "", "", fmt.Errorf("gcp kms: create crypto key: %w", err)
	}
	versionName := key.Name + "/cryptoKeyVersions/1"

	for attempt := 0; attempt < 20; attempt++ {
		ver, err := s.client.GetCryptoKeyVersion(ctx, &kmspb.GetCryptoKeyVersionRequest{Name: versionName})
		if err != nil {
			return "", "", fmt.Errorf("gcp kms: get crypto key version: %w", err)
		}
		if ver.State == kmspb.CryptoKeyVersion_ENABLED {
			break
		}
		if attempt == 19 {
			return "", "", fmt.Errorf("gcp kms: crypto key version %s not ENABLED after polling (state=%s)", versionName, ver.State)
		}
		select {
		case <-ctx.Done():
			return "", "", ctx.Err()
		case <-time.After(s.pollWait):
		}
	}

	pub, err := s.client.GetPublicKey(ctx, &kmspb.GetPublicKeyRequest{Name: versionName})
	if err != nil {
		return "", "", fmt.Errorf("gcp kms: get public key: %w", err)
	}
	// Cloud KMS already returns PEM-encoded SPKI (unlike AWS/Azure, which
	// return raw DER/JWK components requiring local re-encoding).
	return versionName, pub.Pem, nil
}

// Sign returns the RS256 signature over signingString. Cloud KMS's
// AsymmetricSign operates on a digest (like Azure Key Vault, unlike AWS KMS's
// raw-message Sign), so the SHA-256 is computed locally first.
func (s *Signer) Sign(ctx context.Context, kid string, signingString []byte) ([]byte, error) {
	digest := sha256.Sum256(signingString)
	out, err := s.client.AsymmetricSign(ctx, &kmspb.AsymmetricSignRequest{
		Name:   kid,
		Digest: &kmspb.Digest{Digest: &kmspb.Digest_Sha256{Sha256: digest[:]}},
	})
	if err != nil {
		return nil, fmt.Errorf("gcp kms: asymmetric sign: %w", err)
	}
	return out.Signature, nil
}
