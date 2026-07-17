// Package awskms implements the real AWS KMS Signer adapter (BYO Infra
// Hardening Phase 2, docs/design/byo-infra-hardening.md).
//
// Private keys never leave KMS: Generate creates an asymmetric RSA-2048
// SIGN_VERIFY key and reads back only its public key (already SPKI/PKIX DER,
// the same shape keys.LocalSigner/vault.TransitSigner PEM-encode, so
// KeyManager's parsePublicPEM needs no special-casing); Sign asks KMS to
// produce the RSASSA_PKCS1_V1_5_SHA_256 signature. Selected by
// SECRETS_BACKEND=aws (cmd/server/main.go). Live-verified against a real
// local LocalStack container (KMS is fully emulated there, including
// asymmetric sign/verify) — see internal/keys/signer_contract_test.go.
package awskms

import (
	"context"
	"encoding/pem"
	"fmt"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/kms"
	"github.com/aws/aws-sdk-go-v2/service/kms/types"
)

// Signer implements keys.Signer against AWS KMS asymmetric keys.
type Signer struct {
	client *kms.Client
}

// Config carries the connection parameters. EndpointURL is set for a local
// LocalStack target; left empty it resolves to the real AWS KMS endpoint for
// Region via the default AWS SDK resolution chain.
type Config struct {
	Region          string
	EndpointURL     string // e.g. http://localhost:4566 for LocalStack; "" = real AWS
	AccessKeyID     string
	SecretAccessKey string
}

// New builds an AWSKMSSigner from explicit config (no reliance on ambient AWS
// credential files, so it's usable identically against LocalStack or real AWS).
func New(ctx context.Context, cfg Config) (*Signer, error) {
	region := cfg.Region
	if region == "" {
		region = "us-east-1"
	}
	optFns := []func(*config.LoadOptions) error{config.WithRegion(region)}
	if cfg.AccessKeyID != "" {
		optFns = append(optFns, config.WithCredentialsProvider(
			credentials.NewStaticCredentialsProvider(cfg.AccessKeyID, cfg.SecretAccessKey, ""),
		))
	}
	awsCfg, err := config.LoadDefaultConfig(ctx, optFns...)
	if err != nil {
		return nil, fmt.Errorf("aws kms: load config: %w", err)
	}
	client := kms.NewFromConfig(awsCfg, func(o *kms.Options) {
		if cfg.EndpointURL != "" {
			o.BaseEndpoint = aws.String(cfg.EndpointURL)
		}
	})
	return &Signer{client: client}, nil
}

// Generate creates a new asymmetric RSA-2048 SIGN_VERIFY KMS key and returns
// its KeyId (kid) + public key PEM. The private key material never leaves KMS.
func (s *Signer) Generate(ctx context.Context) (string, string, error) {
	created, err := s.client.CreateKey(ctx, &kms.CreateKeyInput{
		KeySpec:  types.KeySpecRsa2048,
		KeyUsage: types.KeyUsageTypeSignVerify,
	})
	if err != nil {
		return "", "", fmt.Errorf("aws kms: create key: %w", err)
	}
	kid := aws.ToString(created.KeyMetadata.KeyId)
	pub, err := s.client.GetPublicKey(ctx, &kms.GetPublicKeyInput{KeyId: aws.String(kid)})
	if err != nil {
		return "", "", fmt.Errorf("aws kms: get public key: %w", err)
	}
	// KMS returns the public key as DER-encoded SubjectPublicKeyInfo (PKIX) —
	// the exact shape keys.parsePublicPEM expects inside a PEM block.
	pemStr := string(pem.EncodeToMemory(&pem.Block{Type: "PUBLIC KEY", Bytes: pub.PublicKey}))
	return kid, pemStr, nil
}

// Sign returns the RS256 (RSASSA_PKCS1_V1_5_SHA_256) signature over
// signingString, computed inside KMS (RAW message type — KMS hashes it).
func (s *Signer) Sign(ctx context.Context, kid string, signingString []byte) ([]byte, error) {
	out, err := s.client.Sign(ctx, &kms.SignInput{
		KeyId:            aws.String(kid),
		Message:          signingString,
		MessageType:      types.MessageTypeRaw,
		SigningAlgorithm: types.SigningAlgorithmSpecRsassaPkcs1V15Sha256,
	})
	if err != nil {
		return nil, fmt.Errorf("aws kms: sign: %w", err)
	}
	return out.Signature, nil
}
