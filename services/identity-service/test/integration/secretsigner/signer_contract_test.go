//go:build integration

// Shared Signer contract suite (BYO Infra Hardening Phase 2,
// docs/design/byo-infra-hardening.md): runs the SAME behavioral assertions
// against every keys.Signer implementation so a new backend can't silently
// drift from LocalSigner/TransitSigner's semantics — Generate must produce a
// usable key (parseable PKIX public key) and Sign must produce a signature
// that genuinely verifies against it (real crypto/rsa verification, not a
// byte-length check).
//
//   - LocalSigner        — always runs (no infra).
//   - TransitSigner       — real Vault transit engine; skips if
//     localhost:8200 (the dev stack's Vault) is unreachable.
//   - AWSKMSSigner        — real AWS KMS wire protocol (CreateKey,
//     GetPublicKey, Sign) against a real local LocalStack container (this
//     package's TestMain spins one up, since KMS isn't part of
//     docker-compose.dev.yml). Skips if Docker is unavailable. A genuine live
//     backend, not a mock.
//   - AzureKeyVaultSigner — the Azure SDK's own documented fake transport
//     (azkeys/fake.Server + fake.NewServerTransport), backed here by REAL
//     RSA generation/signing so the crypto assertions are genuine; only the
//     HTTP transport is faked (no Key Vault emulator exists).
//   - GCPKMSSigner        — an injected fake Client (gcpkms.NewWithClient)
//     also backed by real RSA generation/signing (see fake_gcp_client.go);
//     no Cloud KMS emulator exists either.
//
// Run with: go test -tags integration ./test/integration/secretsigner/...
package secretsigner

import (
	"context"
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/pem"
	"fmt"
	"math/big"
	"net/http"
	"net/url"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/Azure/azure-sdk-for-go/sdk/azcore"
	azfake "github.com/Azure/azure-sdk-for-go/sdk/azcore/fake"
	"github.com/Azure/azure-sdk-for-go/sdk/security/keyvault/azkeys"
	"github.com/Azure/azure-sdk-for-go/sdk/security/keyvault/azkeys/fake"
	"github.com/google/uuid"
	tc "github.com/testcontainers/testcontainers-go"
	"github.com/testcontainers/testcontainers-go/modules/localstack"

	"github.com/windrose-ai/identity-service/internal/adapters/awskms"
	"github.com/windrose-ai/identity-service/internal/adapters/azurekeyvault"
	"github.com/windrose-ai/identity-service/internal/adapters/gcpkms"
	"github.com/windrose-ai/identity-service/internal/adapters/vault"
	"github.com/windrose-ai/identity-service/internal/keys"
)

var (
	awsEndpoint  string
	awsSkipError error
)

func TestMain(m *testing.M) {
	ctx := context.Background()
	container, err := localstack.Run(ctx, "localstack/localstack:3.4",
		tc.WithEnv(map[string]string{"SERVICES": "kms"}),
	)
	if err != nil {
		awsSkipError = fmt.Errorf("Docker unavailable — skipping LocalStack-backed cases: %w", err)
		fmt.Println(awsSkipError)
		os.Exit(m.Run())
	}
	defer func() { _ = tc.TerminateContainer(container) }()

	endpoint, err := container.PortEndpoint(ctx, "4566/tcp", "http")
	if err != nil {
		awsSkipError = err
		os.Exit(m.Run())
	}
	awsEndpoint = endpoint
	os.Exit(m.Run())
}

func reachable(host string, port string) bool {
	// Minimal TCP reachability probe, mirroring the Python suite's `_reachable`.
	u := &url.URL{Scheme: "http", Host: host + ":" + port}
	req, _ := http.NewRequest(http.MethodGet, u.String(), nil)
	client := &http.Client{Timeout: time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return false
	}
	_ = resp.Body.Close()
	return true
}

// --------------------------------------------------------------------------
// Azure fake server: the Azure SDK's own documented fake transport
// (azkeys/fake), backed by real crypto/rsa so Sign genuinely verifies.
// --------------------------------------------------------------------------

type azureFakeBackend struct {
	mu   sync.Mutex
	keys map[string]*rsa.PrivateKey // key name -> private key (version ignored — see below)
}

func newAzureSigner(t *testing.T) keys.Signer {
	t.Helper()
	backend := &azureFakeBackend{keys: map[string]*rsa.PrivateKey{}}
	srv := fake.Server{
		CreateKey: func(ctx context.Context, name string, parameters azkeys.CreateKeyParameters, options *azkeys.CreateKeyOptions) (azfake.Responder[azkeys.CreateKeyResponse], azfake.ErrorResponder) {
			var resp azfake.Responder[azkeys.CreateKeyResponse]
			priv, err := rsa.GenerateKey(rand.Reader, 2048)
			if err != nil {
				var errResp azfake.ErrorResponder
				errResp.SetError(err)
				return resp, errResp
			}
			version := "v1"
			backend.mu.Lock()
			backend.keys[name] = priv
			backend.mu.Unlock()
			kid := azkeys.ID(fmt.Sprintf("https://fake.vault.azure.net/keys/%s/%s", name, version))
			resp.SetResponse(http.StatusOK, azkeys.CreateKeyResponse{
				KeyBundle: azkeys.KeyBundle{
					Key: &azkeys.JSONWebKey{
						KID: &kid,
						N:   priv.PublicKey.N.Bytes(),
						E:   big.NewInt(int64(priv.PublicKey.E)).Bytes(),
					},
				},
			}, nil)
			return resp, azfake.ErrorResponder{}
		},
		// version is intentionally ignored beyond stripping it back off `name`
		// below — this fake only ever creates one version per key name
		// (Windrose's usage never asks Key Vault for a specific older
		// version). Note: azkeys/fake@v1.5.0's generated router folds the
		// {name}/{version} path segments into `name` for the Sign route
		// instead of splitting them (verified empirically: Client.Sign(ctx,
		// "mykey", "v1", ...) invokes this handler with name="mykey/v1",
		// version="") — a router quirk in the SDK's own fake, not something
		// under this adapter's control, so the test fake defensively
		// re-derives the real key name instead of trusting the split.
		Sign: func(ctx context.Context, name string, version string, parameters azkeys.SignParameters, options *azkeys.SignOptions) (azfake.Responder[azkeys.SignResponse], azfake.ErrorResponder) {
			var resp azfake.Responder[azkeys.SignResponse]
			keyName := name
			if idx := strings.Index(keyName, "/"); idx >= 0 {
				keyName = keyName[:idx]
			}
			backend.mu.Lock()
			priv, ok := backend.keys[keyName]
			backend.mu.Unlock()
			if !ok {
				var errResp azfake.ErrorResponder
				errResp.SetError(fmt.Errorf("unknown key %s", keyName))
				return resp, errResp
			}
			sig, err := rsa.SignPKCS1v15(rand.Reader, priv, crypto.SHA256, parameters.Value)
			if err != nil {
				var errResp azfake.ErrorResponder
				errResp.SetError(err)
				return resp, errResp
			}
			resp.SetResponse(http.StatusOK, azkeys.SignResponse{
				KeyOperationResult: azkeys.KeyOperationResult{Result: sig},
			}, nil)
			return resp, azfake.ErrorResponder{}
		},
	}
	s, err := azurekeyvault.New("https://fake.vault.azure.net", &azfake.TokenCredential{}, &azkeys.ClientOptions{
		ClientOptions: azcore.ClientOptions{Transport: fake.NewServerTransport(&srv)},
	})
	if err != nil {
		t.Fatalf("azure key vault signer: %v", err)
	}
	return s
}

// --------------------------------------------------------------------------
// The shared contract, table-driven over every Signer implementation.
// --------------------------------------------------------------------------

type signerCase struct {
	name  string
	build func(t *testing.T) keys.Signer
}

func cases() []signerCase {
	return []signerCase{
		{
			name: "local",
			build: func(t *testing.T) keys.Signer {
				return keys.NewLocalSigner()
			},
		},
		{
			name: "vault_transit (real, real Vault)",
			build: func(t *testing.T) keys.Signer {
				addr := "http://localhost:8200"
				if a := os.Getenv("VAULT_ADDR"); a != "" {
					addr = a
				}
				if !reachable("localhost", "8200") {
					t.Skip("Vault not reachable at localhost:8200 — is the dev infra up?")
				}
				s, err := vault.New(addr, "windrose_dev_root", "")
				if err != nil {
					t.Skipf("vault unavailable: %v", err)
				}
				return s
			},
		},
		{
			name: "aws_kms (real, real LocalStack)",
			build: func(t *testing.T) keys.Signer {
				if awsSkipError != nil {
					t.Skip(awsSkipError.Error())
				}
				s, err := awskms.New(context.Background(), awskms.Config{
					Region: "us-east-1", EndpointURL: awsEndpoint,
					AccessKeyID: "test", SecretAccessKey: "test",
				})
				if err != nil {
					t.Fatalf("aws kms signer: %v", err)
				}
				return s
			},
		},
		{
			name:  "azure_key_vault (mock-tested, SDK fake transport — no emulator available)",
			build: newAzureSigner,
		},
		{
			name: "gcp_kms (mock-tested, injected fake client — no emulator available)",
			build: func(t *testing.T) keys.Signer {
				return gcpkms.NewWithClient(newFakeGCPClient(), "projects/wr-test/locations/us/keyRings/wr-test-ring")
			},
		},
	}
}

func TestSigner_GenerateProducesUsableKey(t *testing.T) {
	for _, c := range cases() {
		t.Run(c.name, func(t *testing.T) {
			s := c.build(t)
			kid, pubPEM, err := s.Generate(context.Background())
			if err != nil {
				t.Fatalf("generate: %v", err)
			}
			if kid == "" {
				t.Fatal("empty kid")
			}
			pub := parsePublicPEM(t, pubPEM)
			if pub.N.BitLen() < 2000 {
				t.Fatalf("unexpectedly small RSA key: %d bits", pub.N.BitLen())
			}
		})
	}
}

func TestSigner_SignProducesVerifiableSignature(t *testing.T) {
	for _, c := range cases() {
		t.Run(c.name, func(t *testing.T) {
			s := c.build(t)
			kid, pubPEM, err := s.Generate(context.Background())
			if err != nil {
				t.Fatalf("generate: %v", err)
			}
			pub := parsePublicPEM(t, pubPEM)

			signingString := []byte("header." + uuid.NewString() + ".payload")
			sig, err := s.Sign(context.Background(), kid, signingString)
			if err != nil {
				t.Fatalf("sign: %v", err)
			}
			digest := sha256.Sum256(signingString)
			if err := rsa.VerifyPKCS1v15(pub, crypto.SHA256, digest[:], sig); err != nil {
				t.Fatalf("signature does not verify against the generated public key: %v", err)
			}

			// Tampering must invalidate the signature (real crypto, not a stub).
			tamperedDigest := sha256.Sum256(append(signingString, 'x'))
			if err := rsa.VerifyPKCS1v15(pub, crypto.SHA256, tamperedDigest[:], sig); err == nil {
				t.Fatal("signature verified against tampered content")
			}
		})
	}
}

func parsePublicPEM(t *testing.T, pemStr string) *rsa.PublicKey {
	t.Helper()
	block, _ := pem.Decode([]byte(pemStr))
	if block == nil {
		t.Fatalf("invalid PEM: %q", pemStr)
	}
	pub, err := x509.ParsePKIXPublicKey(block.Bytes)
	if err != nil {
		t.Fatalf("parse PKIX public key: %v", err)
	}
	rsaPub, ok := pub.(*rsa.PublicKey)
	if !ok {
		t.Fatalf("not an RSA public key: %T", pub)
	}
	return rsaPub
}
