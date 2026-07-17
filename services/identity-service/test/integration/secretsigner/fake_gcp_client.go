//go:build integration

package secretsigner

import (
	"context"
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"encoding/pem"
	"fmt"
	"strings"
	"sync"

	"cloud.google.com/go/kms/apiv1/kmspb"
)

// fakeGCPClient implements gcpkms.Client entirely in-memory with REAL RSA
// key generation and RS256 signing (crypto/rsa), so the contract test's
// "signature verifies against the generated public key" assertion is a
// genuine cryptographic check, not a rubber stamp. Only the KMS network
// transport is faked — no local Cloud KMS emulator exists (per BYO Infra
// Hardening Phase 2's honesty note), this is the standard substitute.
type fakeGCPClient struct {
	mu   sync.Mutex
	keys map[string]*rsa.PrivateKey // cryptoKey resource name -> private key
}

func newFakeGCPClient() *fakeGCPClient {
	return &fakeGCPClient{keys: map[string]*rsa.PrivateKey{}}
}

func (f *fakeGCPClient) CreateCryptoKey(_ context.Context, req *kmspb.CreateCryptoKeyRequest, _ ...interface{}) (*kmspb.CryptoKey, error) {
	priv, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		return nil, err
	}
	name := fmt.Sprintf("%s/cryptoKeys/%s", req.Parent, req.CryptoKeyId)
	f.mu.Lock()
	f.keys[name] = priv
	f.mu.Unlock()
	return &kmspb.CryptoKey{Name: name, Purpose: req.CryptoKey.Purpose}, nil
}

func (f *fakeGCPClient) GetCryptoKeyVersion(_ context.Context, req *kmspb.GetCryptoKeyVersionRequest, _ ...interface{}) (*kmspb.CryptoKeyVersion, error) {
	// Fake key material is generated synchronously (real Cloud KMS is
	// asynchronous — the adapter's polling loop just no-ops here since the
	// version is ENABLED on the first check).
	return &kmspb.CryptoKeyVersion{Name: req.Name, State: kmspb.CryptoKeyVersion_ENABLED}, nil
}

func (f *fakeGCPClient) GetPublicKey(_ context.Context, req *kmspb.GetPublicKeyRequest, _ ...interface{}) (*kmspb.PublicKey, error) {
	priv, ok := f.keys[cryptoKeyNameFromVersion(req.Name)]
	if !ok {
		return nil, fmt.Errorf("fake gcp kms: unknown key for version %s", req.Name)
	}
	der, err := x509.MarshalPKIXPublicKey(&priv.PublicKey)
	if err != nil {
		return nil, err
	}
	pemStr := string(pem.EncodeToMemory(&pem.Block{Type: "PUBLIC KEY", Bytes: der}))
	return &kmspb.PublicKey{Pem: pemStr}, nil
}

func (f *fakeGCPClient) AsymmetricSign(_ context.Context, req *kmspb.AsymmetricSignRequest, _ ...interface{}) (*kmspb.AsymmetricSignResponse, error) {
	priv, ok := f.keys[cryptoKeyNameFromVersion(req.Name)]
	if !ok {
		return nil, fmt.Errorf("fake gcp kms: unknown key for version %s", req.Name)
	}
	sha256Digest, ok := req.Digest.Digest.(*kmspb.Digest_Sha256)
	if !ok {
		return nil, fmt.Errorf("fake gcp kms: only sha256 digests supported")
	}
	sig, err := rsa.SignPKCS1v15(rand.Reader, priv, crypto.SHA256, sha256Digest.Sha256)
	if err != nil {
		return nil, err
	}
	return &kmspb.AsymmetricSignResponse{Signature: sig}, nil
}

func cryptoKeyNameFromVersion(versionName string) string {
	if i := strings.Index(versionName, "/cryptoKeyVersions/"); i >= 0 {
		return versionName[:i]
	}
	return versionName
}
