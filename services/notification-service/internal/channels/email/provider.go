// Package email is the email channel: a provider abstraction (SES, SendGrid,
// ACS, SMTP) with per-provider failover and circuit breaking (NOTIF-FR-021).
// The runtime default is the SMTP driver, exercised end-to-end against a real
// local SMTP capture (Mailpit) so the email path is genuinely sent, not mocked.
// SES/SendGrid/ACS are real drivers but require cloud credentials to reach a
// live endpoint (documented credential-gated exception, like other cloud
// adapters); their request-building code is real and unit-tested.
package email

import (
	"context"
	"net/http"
)

// ErrorClass classifies a provider send failure for the failover matrix (BR-9).
type ErrorClass int

const (
	// ClassNone means the send succeeded.
	ClassNone ErrorClass = iota
	// ClassPermanent: reject (bad address, auth) — fail delivery, no failover.
	ClassPermanent
	// ClassTransient: connect/5xx before accept — retry then failover.
	ClassTransient
	// ClassAmbiguous: timeout after submit — no failover, retry same provider.
	ClassAmbiguous
)

func (c ErrorClass) String() string {
	switch c {
	case ClassPermanent:
		return "permanent"
	case ClassTransient:
		return "transient"
	case ClassAmbiguous:
		return "ambiguous"
	default:
		return "none"
	}
}

// SendError wraps a provider error with its class.
type SendError struct {
	Class ErrorClass
	Err   error
}

func (e *SendError) Error() string {
	if e.Err == nil {
		return e.Class.String()
	}
	return e.Class.String() + ": " + e.Err.Error()
}

// Message is one email to send.
type Message struct {
	From    string
	To      string
	Subject string
	HTML    string
	Text    string
}

// StatusUpdate is a delivered/bounced/complained event parsed from a provider
// status callback (NOTIF-FR-021).
type StatusUpdate struct {
	ProviderMsgID string
	Email         string
	Status        string // delivered | bounced | complained
	Hard          bool   // hard bounce/complaint → suppression
}

// Provider is the normative email driver interface (BRD 19 §5).
type Provider interface {
	Name() string
	Send(ctx context.Context, m Message) (providerMsgID string, err error)
	ParseStatusCallback(r *http.Request) ([]StatusUpdate, error)
}
