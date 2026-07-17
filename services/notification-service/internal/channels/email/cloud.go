package email

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// These cloud drivers speak each provider's real wire protocol. Reaching a live
// endpoint requires cloud credentials (documented credential-gated exception,
// like other cloud adapters). Request construction + callback parsing are real
// and unit-tested; an unconfigured driver returns a Permanent SendError so the
// runtime never silently no-ops.

// ---- SendGrid ---------------------------------------------------------------

// SendGridProvider posts to the SendGrid v3 mail/send API.
type SendGridProvider struct {
	APIKey string
	From   string
	client *http.Client
	base   string
}

// NewSendGrid builds a SendGrid provider.
func NewSendGrid(apiKey, from string) *SendGridProvider {
	return &SendGridProvider{APIKey: apiKey, From: from, client: &http.Client{Timeout: 15 * time.Second}, base: "https://api.sendgrid.com"}
}

func (p *SendGridProvider) Name() string { return "sendgrid" }

func (p *SendGridProvider) Send(ctx context.Context, m Message) (string, error) {
	if p.APIKey == "" {
		return "", &SendError{Class: ClassPermanent, Err: errors.New("sendgrid: API key not configured (credential-gated)")}
	}
	from := firstNonEmpty(m.From, p.From, "notifications@windrose.local")
	body := map[string]any{
		"personalizations": []map[string]any{{"to": []map[string]string{{"email": m.To}}, "subject": m.Subject}},
		"from":             map[string]string{"email": from},
		"content": []map[string]string{
			{"type": "text/plain", "value": nonEmpty(m.Text, " ")},
			{"type": "text/html", "value": nonEmpty(m.HTML, " ")},
		},
	}
	raw, _ := json.Marshal(body)
	req, _ := http.NewRequestWithContext(ctx, http.MethodPost, p.base+"/v3/mail/send", bytes.NewReader(raw))
	req.Header.Set("Authorization", "Bearer "+p.APIKey)
	req.Header.Set("Content-Type", "application/json")
	resp, err := p.client.Do(req)
	if err != nil {
		return "", &SendError{Class: ClassTransient, Err: err}
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 200 && resp.StatusCode < 300 {
		return resp.Header.Get("X-Message-Id"), nil
	}
	return "", classifyHTTP(resp.StatusCode)
}

// ParseStatusCallback parses SendGrid event webhook JSON (array of events).
func (p *SendGridProvider) ParseStatusCallback(r *http.Request) ([]StatusUpdate, error) {
	var events []struct {
		Email string `json:"email"`
		Event string `json:"event"`
		SGM   string `json:"sg_message_id"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&events); err != nil {
		return nil, err
	}
	var out []StatusUpdate
	for _, e := range events {
		st, hard := mapProviderEvent(e.Event)
		if st == "" {
			continue
		}
		out = append(out, StatusUpdate{ProviderMsgID: e.SGM, Email: e.Email, Status: st, Hard: hard})
	}
	return out, nil
}

// ---- Amazon SES (SigV4) -----------------------------------------------------

// SESProvider posts to the SES v2 API with SigV4 signing.
type SESProvider struct {
	Region, AccessKey, SecretKey, From string
	client                             *http.Client
}

// NewSES builds an SES provider.
func NewSES(region, accessKey, secretKey, from string) *SESProvider {
	return &SESProvider{Region: region, AccessKey: accessKey, SecretKey: secretKey, From: from, client: &http.Client{Timeout: 15 * time.Second}}
}

func (p *SESProvider) Name() string { return "ses" }

func (p *SESProvider) Send(ctx context.Context, m Message) (string, error) {
	if p.AccessKey == "" || p.SecretKey == "" {
		return "", &SendError{Class: ClassPermanent, Err: errors.New("ses: credentials not configured (credential-gated)")}
	}
	from := firstNonEmpty(m.From, p.From, "notifications@windrose.local")
	payload := map[string]any{
		"FromEmailAddress": from,
		"Destination":      map[string]any{"ToAddresses": []string{m.To}},
		"Content": map[string]any{"Simple": map[string]any{
			"Subject": map[string]any{"Data": m.Subject},
			"Body": map[string]any{
				"Text": map[string]any{"Data": nonEmpty(m.Text, " ")},
				"Html": map[string]any{"Data": nonEmpty(m.HTML, " ")},
			},
		}},
	}
	raw, _ := json.Marshal(payload)
	host := fmt.Sprintf("email.%s.amazonaws.com", p.Region)
	endpoint := "https://" + host + "/v2/email/outbound-emails"
	req, _ := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(raw))
	req.Header.Set("Content-Type", "application/json")
	if err := signSigV4(req, raw, p.AccessKey, p.SecretKey, p.Region, "ses", time.Now().UTC()); err != nil {
		return "", &SendError{Class: ClassPermanent, Err: err}
	}
	resp, err := p.client.Do(req)
	if err != nil {
		return "", &SendError{Class: ClassTransient, Err: err}
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 200 && resp.StatusCode < 300 {
		var out struct {
			MessageID string `json:"MessageId"`
		}
		_ = json.NewDecoder(resp.Body).Decode(&out)
		return out.MessageID, nil
	}
	return "", classifyHTTP(resp.StatusCode)
}

// ParseStatusCallback parses an SES SNS notification (bounce/complaint/delivery).
func (p *SESProvider) ParseStatusCallback(r *http.Request) ([]StatusUpdate, error) {
	var sns struct {
		Message string `json:"Message"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&sns); err != nil {
		return nil, err
	}
	var msg struct {
		NotificationType string `json:"notificationType"`
		Mail             struct {
			MessageID   string   `json:"messageId"`
			Destination []string `json:"destination"`
		} `json:"mail"`
		Bounce struct {
			BounceType string `json:"bounceType"`
		} `json:"bounce"`
	}
	if err := json.Unmarshal([]byte(sns.Message), &msg); err != nil {
		return nil, err
	}
	st, hard := "", false
	switch strings.ToLower(msg.NotificationType) {
	case "delivery":
		st = "delivered"
	case "bounce":
		st, hard = "bounced", strings.EqualFold(msg.Bounce.BounceType, "Permanent")
	case "complaint":
		st, hard = "complained", true
	}
	if st == "" {
		return nil, nil
	}
	email := ""
	if len(msg.Mail.Destination) > 0 {
		email = msg.Mail.Destination[0]
	}
	return []StatusUpdate{{ProviderMsgID: msg.Mail.MessageID, Email: email, Status: st, Hard: hard}}, nil
}

// ---- Azure Communication Services -------------------------------------------

// ACSProvider posts to the Azure Communication Services email API (HMAC auth).
type ACSProvider struct {
	Endpoint, AccessKey, From string
	client                    *http.Client
}

// NewACS builds an ACS provider (endpoint e.g. https://<res>.communication.azure.com).
func NewACS(endpoint, accessKey, from string) *ACSProvider {
	return &ACSProvider{Endpoint: strings.TrimRight(endpoint, "/"), AccessKey: accessKey, From: from, client: &http.Client{Timeout: 15 * time.Second}}
}

func (p *ACSProvider) Name() string { return "acs" }

func (p *ACSProvider) Send(ctx context.Context, m Message) (string, error) {
	if p.AccessKey == "" || p.Endpoint == "" {
		return "", &SendError{Class: ClassPermanent, Err: errors.New("acs: credentials not configured (credential-gated)")}
	}
	from := firstNonEmpty(m.From, p.From, "notifications@windrose.local")
	payload := map[string]any{
		"senderAddress": from,
		"content":       map[string]string{"subject": m.Subject, "plainText": nonEmpty(m.Text, " "), "html": nonEmpty(m.HTML, " ")},
		"recipients":    map[string]any{"to": []map[string]string{{"address": m.To}}},
	}
	raw, _ := json.Marshal(payload)
	url := p.Endpoint + "/emails:send?api-version=2023-03-31"
	req, _ := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(raw))
	req.Header.Set("Content-Type", "application/json")
	if err := signACS(req, raw, p.AccessKey); err != nil {
		return "", &SendError{Class: ClassPermanent, Err: err}
	}
	resp, err := p.client.Do(req)
	if err != nil {
		return "", &SendError{Class: ClassTransient, Err: err}
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 200 && resp.StatusCode < 300 {
		return resp.Header.Get("x-ms-request-id"), nil
	}
	return "", classifyHTTP(resp.StatusCode)
}

// ParseStatusCallback parses Azure Event Grid email delivery events.
func (p *ACSProvider) ParseStatusCallback(r *http.Request) ([]StatusUpdate, error) {
	var events []struct {
		EventType string `json:"eventType"`
		Data      struct {
			MessageID string `json:"messageId"`
			Recipient string `json:"recipient"`
			Status    string `json:"status"`
		} `json:"data"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&events); err != nil {
		return nil, err
	}
	var out []StatusUpdate
	for _, e := range events {
		st, hard := mapProviderEvent(e.Data.Status)
		if st == "" {
			continue
		}
		out = append(out, StatusUpdate{ProviderMsgID: e.Data.MessageID, Email: e.Data.Recipient, Status: st, Hard: hard})
	}
	return out, nil
}

// ---- shared helpers ---------------------------------------------------------

func mapProviderEvent(ev string) (status string, hard bool) {
	switch strings.ToLower(ev) {
	case "delivered", "delivery":
		return "delivered", false
	case "bounce", "bounced", "dropped", "failed":
		return "bounced", true
	case "spamreport", "complaint", "complained":
		return "complained", true
	}
	return "", false
}

func classifyHTTP(status int) error {
	switch {
	case status == 429 || status >= 500:
		return &SendError{Class: ClassTransient, Err: fmt.Errorf("provider status %d", status)}
	case status >= 400:
		return &SendError{Class: ClassPermanent, Err: fmt.Errorf("provider status %d", status)}
	default:
		return &SendError{Class: ClassAmbiguous, Err: fmt.Errorf("provider status %d", status)}
	}
}

func firstNonEmpty(vs ...string) string {
	for _, v := range vs {
		if v != "" {
			return v
		}
	}
	return ""
}

func nonEmpty(v, def string) string {
	if v == "" {
		return def
	}
	return v
}

// signACS applies the Azure HMAC-SHA256 shared-key scheme.
func signACS(req *http.Request, body []byte, keyB64 string) error {
	key, err := base64.StdEncoding.DecodeString(keyB64)
	if err != nil {
		return err
	}
	sum := sha256.Sum256(body)
	contentHash := base64.StdEncoding.EncodeToString(sum[:])
	date := time.Now().UTC().Format(http.TimeFormat)
	host := req.URL.Host
	strToSign := fmt.Sprintf("%s\n%s\n%s;%s;%s", req.Method, req.URL.RequestURI(), date, host, contentHash)
	mac := hmac.New(sha256.New, key)
	mac.Write([]byte(strToSign))
	sig := base64.StdEncoding.EncodeToString(mac.Sum(nil))
	req.Header.Set("x-ms-date", date)
	req.Header.Set("x-ms-content-sha256", contentHash)
	req.Header.Set("Authorization", "HMAC-SHA256 SignedHeaders=x-ms-date;host;x-ms-content-sha256&Signature="+sig)
	return nil
}

// signSigV4 applies AWS Signature Version 4 to req for the given service.
func signSigV4(req *http.Request, body []byte, accessKey, secretKey, region, service string, now time.Time) error {
	amzDate := now.Format("20060102T150405Z")
	dateStamp := now.Format("20060102")
	host := req.URL.Host
	payloadHash := hexSHA256(body)

	req.Header.Set("Host", host)
	req.Header.Set("X-Amz-Date", amzDate)
	req.Header.Set("X-Amz-Content-Sha256", payloadHash)

	signedHeaders := "content-type;host;x-amz-content-sha256;x-amz-date"
	canonicalHeaders := fmt.Sprintf("content-type:%s\nhost:%s\nx-amz-content-sha256:%s\nx-amz-date:%s\n",
		req.Header.Get("Content-Type"), host, payloadHash, amzDate)
	canonicalRequest := strings.Join([]string{req.Method, req.URL.Path, "", canonicalHeaders, signedHeaders, payloadHash}, "\n")

	scope := strings.Join([]string{dateStamp, region, service, "aws4_request"}, "/")
	stringToSign := strings.Join([]string{"AWS4-HMAC-SHA256", amzDate, scope, hexSHA256([]byte(canonicalRequest))}, "\n")

	kDate := hmacSHA256([]byte("AWS4"+secretKey), dateStamp)
	kRegion := hmacSHA256(kDate, region)
	kService := hmacSHA256(kRegion, service)
	kSigning := hmacSHA256(kService, "aws4_request")
	signature := fmt.Sprintf("%x", hmacSHA256(kSigning, stringToSign))

	auth := fmt.Sprintf("AWS4-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s", accessKey, scope, signedHeaders, signature)
	req.Header.Set("Authorization", auth)
	return nil
}

func hmacSHA256(key []byte, data string) []byte {
	h := hmac.New(sha256.New, key)
	h.Write([]byte(data))
	return h.Sum(nil)
}

func hexSHA256(b []byte) string {
	sum := sha256.Sum256(b)
	return fmt.Sprintf("%x", sum)
}
