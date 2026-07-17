package email

import (
	"context"
	"crypto/tls"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/smtp"
	"net/textproto"
	"strings"
	"time"

	"github.com/google/uuid"
)

// SMTPProvider sends via a real SMTP server. In the dev/test runtime this is a
// local SMTP capture (Mailpit); in production a relay. It speaks the real SMTP
// wire protocol — no mock.
type SMTPProvider struct {
	Addr string // host:port
	Auth smtp.Auth
	TLS  bool
}

// NewSMTP builds an SMTP provider. When user is empty, no AUTH is attempted
// (Mailpit accepts unauthenticated mail).
func NewSMTP(addr, user, pass string, useTLS bool) *SMTPProvider {
	var auth smtp.Auth
	if user != "" {
		host := addr
		if h, _, err := net.SplitHostPort(addr); err == nil {
			host = h
		}
		auth = smtp.PlainAuth("", user, pass, host)
	}
	return &SMTPProvider{Addr: addr, Auth: auth, TLS: useTLS}
}

// Name identifies the provider.
func (p *SMTPProvider) Name() string { return "smtp" }

// Send delivers the message and returns a synthesized provider message id
// (Message-ID). Connection/greeting failures classify Transient; a rejected
// recipient (5xx) classifies Permanent; a timeout after DATA is Ambiguous.
func (p *SMTPProvider) Send(ctx context.Context, m Message) (string, error) {
	msgID := fmt.Sprintf("<%s@windrose>", uuid.NewString())
	raw := buildMIME(m, msgID)

	d := net.Dialer{Timeout: 10 * time.Second}
	conn, err := d.DialContext(ctx, "tcp", p.Addr)
	if err != nil {
		return "", &SendError{Class: ClassTransient, Err: fmt.Errorf("dial: %w", err)}
	}
	host := p.Addr
	if h, _, e := net.SplitHostPort(p.Addr); e == nil {
		host = h
	}
	c, err := smtp.NewClient(conn, host)
	if err != nil {
		_ = conn.Close()
		return "", &SendError{Class: ClassTransient, Err: fmt.Errorf("greeting: %w", err)}
	}
	defer func() { _ = c.Close() }()

	if p.TLS {
		if ok, _ := c.Extension("STARTTLS"); ok {
			if err := c.StartTLS(&tls.Config{ServerName: host, MinVersion: tls.VersionTLS12}); err != nil {
				return "", &SendError{Class: ClassTransient, Err: fmt.Errorf("starttls: %w", err)}
			}
		}
	}
	if p.Auth != nil {
		if ok, _ := c.Extension("AUTH"); ok {
			if err := c.Auth(p.Auth); err != nil {
				return "", &SendError{Class: ClassPermanent, Err: fmt.Errorf("auth: %w", err)}
			}
		}
	}
	from := m.From
	if from == "" {
		from = "notifications@windrose.local"
	}
	if err := c.Mail(from); err != nil {
		return "", classifySMTP(err, "MAIL FROM")
	}
	if err := c.Rcpt(m.To); err != nil {
		return "", classifySMTP(err, "RCPT")
	}
	w, err := c.Data()
	if err != nil {
		return "", classifySMTP(err, "DATA")
	}
	if _, err := w.Write([]byte(raw)); err != nil {
		return "", &SendError{Class: ClassAmbiguous, Err: fmt.Errorf("write body: %w", err)}
	}
	if err := w.Close(); err != nil {
		return "", &SendError{Class: ClassAmbiguous, Err: fmt.Errorf("close body: %w", err)}
	}
	_ = c.Quit()
	return msgID, nil
}

// ParseStatusCallback: SMTP has no async status webhook; delivery is synchronous.
func (p *SMTPProvider) ParseStatusCallback(*http.Request) ([]StatusUpdate, error) {
	return nil, errors.New("smtp has no status callback")
}

func classifySMTP(err error, stage string) error {
	// 5xx = permanent reject; 4xx = transient (real net/textproto.Error codes).
	var tp *textproto.Error
	if errors.As(err, &tp) {
		if tp.Code >= 500 {
			return &SendError{Class: ClassPermanent, Err: fmt.Errorf("%s: %w", stage, err)}
		}
		return &SendError{Class: ClassTransient, Err: fmt.Errorf("%s: %w", stage, err)}
	}
	if strings.HasPrefix(err.Error(), "5") {
		return &SendError{Class: ClassPermanent, Err: fmt.Errorf("%s: %w", stage, err)}
	}
	return &SendError{Class: ClassTransient, Err: fmt.Errorf("%s: %w", stage, err)}
}

func buildMIME(m Message, msgID string) string {
	from := m.From
	if from == "" {
		from = "notifications@windrose.local"
	}
	var b strings.Builder
	boundary := "windrose-" + strings.ReplaceAll(uuid.NewString(), "-", "")
	fmt.Fprintf(&b, "From: %s\r\n", from)
	fmt.Fprintf(&b, "To: %s\r\n", m.To)
	fmt.Fprintf(&b, "Subject: %s\r\n", m.Subject)
	fmt.Fprintf(&b, "Message-ID: %s\r\n", msgID)
	fmt.Fprintf(&b, "List-Unsubscribe: <mailto:unsubscribe@windrose.local>\r\n") // NOTIF-FR-021
	b.WriteString("MIME-Version: 1.0\r\n")
	fmt.Fprintf(&b, "Content-Type: multipart/alternative; boundary=%q\r\n\r\n", boundary)
	if m.Text != "" {
		fmt.Fprintf(&b, "--%s\r\nContent-Type: text/plain; charset=UTF-8\r\n\r\n%s\r\n", boundary, m.Text)
	}
	if m.HTML != "" {
		fmt.Fprintf(&b, "--%s\r\nContent-Type: text/html; charset=UTF-8\r\n\r\n%s\r\n", boundary, m.HTML)
	}
	fmt.Fprintf(&b, "--%s--\r\n", boundary)
	return b.String()
}
