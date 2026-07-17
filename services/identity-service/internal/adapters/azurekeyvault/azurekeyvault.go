// Package azurekeyvault implements the real Azure Key Vault Signer adapter
// (BYO Infra Hardening Phase 2, docs/design/byo-infra-hardening.md).
//
// Private keys never leave Key Vault: Generate creates an RSA-2048 key and
// reads back its JSON Web Key (n/e), which is re-encoded as SPKI/PKIX PEM so
// KeyManager's parsePublicPEM stays backend-agnostic; Sign asks Key Vault to
// produce the RS256 signature over a locally-computed SHA-256 digest (Key
// Vault's Sign API operates on a digest, unlike AWS KMS's raw-message Sign).
// Selected by SECRETS_BACKEND=azure (cmd/server/main.go).
//
// No local Key Vault emulator exists, so this adapter is unit-tested against
// the Azure SDK's own documented fake transport
// (azkeys/fake.Server + fake.NewServerTransport, see
// internal/keys/signer_contract_test.go) rather than live-verified — the
// adapter code itself makes real SDK calls; only the fake stands in for the
// network transport, the standard pattern for this SDK without a real tenant.
package azurekeyvault

import (
	"context"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/pem"
	"fmt"
	"math/big"
	"strings"

	"github.com/Azure/azure-sdk-for-go/sdk/azcore"
	"github.com/Azure/azure-sdk-for-go/sdk/azcore/to"
	"github.com/Azure/azure-sdk-for-go/sdk/azidentity"
	"github.com/Azure/azure-sdk-for-go/sdk/security/keyvault/azkeys"
	"github.com/google/uuid"
)

// Signer implements keys.Signer against Azure Key Vault key operations.
type Signer struct {
	client *azkeys.Client
}

// New builds a Signer against a real Key Vault (vaultURL, e.g.
// https://<vault>.vault.azure.net) using DefaultAzureCredential (or an
// explicit credential for tests/alternate auth).
func New(vaultURL string, credential azcore.TokenCredential, options *azkeys.ClientOptions) (*Signer, error) {
	var err error
	if credential == nil {
		credential, err = azidentity.NewDefaultAzureCredential(nil)
		if err != nil {
			return nil, fmt.Errorf("azure key vault: default credential: %w", err)
		}
	}
	client, err := azkeys.NewClient(vaultURL, credential, options)
	if err != nil {
		return nil, fmt.Errorf("azure key vault: new client: %w", err)
	}
	return &Signer{client: client}, nil
}

// kid encodes both the Key Vault key name and version, since Sign/GetKey both
// need both and keys.Signer only carries a single opaque kid string.
func encodeKID(name, version string) string { return name + "/" + version }

func decodeKID(kid string) (name, version string, err error) {
	parts := strings.SplitN(kid, "/", 2)
	if len(parts) != 2 || parts[0] == "" || parts[1] == "" {
		return "", "", fmt.Errorf("azure key vault: malformed kid %q", kid)
	}
	return parts[0], parts[1], nil
}

// Generate creates a new RSA-2048 key in Key Vault and returns an encoded
// kid (name/version) + public key PEM (re-encoded PKIX from the JWK n/e).
func (s *Signer) Generate(ctx context.Context) (string, string, error) {
	name := "identity-" + uuid.NewString()
	resp, err := s.client.CreateKey(ctx, name, azkeys.CreateKeyParameters{
		Kty:     to.Ptr(azkeys.KeyTypeRSA),
		KeySize: to.Ptr(int32(2048)),
	}, nil)
	if err != nil {
		return "", "", fmt.Errorf("azure key vault: create key: %w", err)
	}
	jwk := resp.Key
	if jwk == nil || jwk.KID == nil || len(jwk.N) == 0 || len(jwk.E) == 0 {
		return "", "", fmt.Errorf("azure key vault: create key: incomplete response")
	}
	version := jwk.KID.Version()
	pub := &rsa.PublicKey{
		N: new(big.Int).SetBytes(jwk.N),
		E: int(new(big.Int).SetBytes(jwk.E).Int64()),
	}
	der, err := x509.MarshalPKIXPublicKey(pub)
	if err != nil {
		return "", "", fmt.Errorf("azure key vault: marshal public key: %w", err)
	}
	pemStr := string(pem.EncodeToMemory(&pem.Block{Type: "PUBLIC KEY", Bytes: der}))
	return encodeKID(name, version), pemStr, nil
}

// Sign returns the RS256 signature over signingString. Key Vault's Sign API
// operates on a digest (unlike AWS KMS's raw-message Sign), so the SHA-256 of
// signingString is computed locally before the call — only the private key
// operation itself happens inside Key Vault.
func (s *Signer) Sign(ctx context.Context, kid string, signingString []byte) ([]byte, error) {
	name, version, err := decodeKID(kid)
	if err != nil {
		return nil, err
	}
	digest := sha256.Sum256(signingString)
	resp, err := s.client.Sign(ctx, name, version, azkeys.SignParameters{
		Algorithm: to.Ptr(azkeys.SignatureAlgorithmRS256),
		Value:     digest[:],
	}, nil)
	if err != nil {
		return nil, fmt.Errorf("azure key vault: sign: %w", err)
	}
	return resp.Result, nil
}
