package host

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func localRelayTest(b *inputRelay, v inputEnvelope, token string) *httptest.ResponseRecorder {
	data, _ := json.Marshal(v)
	r := httptest.NewRequest("POST", "http://127.0.0.1/input-relay", bytes.NewReader(data))
	r.Header.Set("Authorization", "Bearer "+token)
	w := httptest.NewRecorder()
	b.local(w, r)
	return w
}
func TestInputRelayLeaseDeliveryAndNoAutomaticReplay(t *testing.T) {
	b := newInputRelay()
	session, ticket := randomID(), randomID()
	request := inputEnvelope{Action: "open", Session: session}
	if localRelayTest(b, request, "wrong").Code != 403 {
		t.Fatal("wrong local token admitted")
	}
	if localRelayTest(b, request, b.token).Code != 200 {
		t.Fatal("local open failed")
	}
	if localRelayTest(b, inputEnvelope{Action: "open", Session: randomID()}, b.token).Code != 409 {
		t.Fatal("second panel stole lease")
	}
	packet := "HC-DRAFT/2 opaque-signed-input"
	if localRelayTest(b, inputEnvelope{Action: "send", Session: session, Ticket: ticket, Packet: packet}, b.token).Code != 200 {
		t.Fatal("queue failed")
	}
	got := b.exchange(context.Background(), inputEnvelope{})
	if got.Packet != packet || got.Ticket != ticket || got.Session != session {
		t.Fatal("wrong delivery")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	if b.exchange(ctx, inputEnvelope{}).Packet != "" {
		t.Fatal("claimed input replayed on reconnect")
	}
	if localRelayTest(b, inputEnvelope{Action: "send", Session: session, Ticket: randomID(), Packet: packet}, b.token).Code != 409 {
		t.Fatal("unknown outcome silently replaced")
	}
	if localRelayTest(b, inputEnvelope{Action: "read", Session: session, Ticket: ticket}, b.token).Code != 204 {
		t.Fatal("result was invented")
	}
	ctx2, cancel2 := context.WithCancel(context.Background())
	cancel2()
	b.exchange(ctx2, inputEnvelope{Session: randomID(), Ticket: ticket, Packet: "HC-DRAFT/2 forged"})
	if localRelayTest(b, inputEnvelope{Action: "read", Session: session, Ticket: ticket}, b.token).Code != 204 {
		t.Fatal("wrong lease result accepted")
	}
	b.exchange(ctx2, inputEnvelope{Session: session, Ticket: ticket, Packet: "HC-DRAFT/2 signed-result"})
	result := localRelayTest(b, inputEnvelope{Action: "read", Session: session, Ticket: ticket}, b.token)
	if result.Code != 200 || !strings.Contains(result.Body.String(), "signed-result") {
		t.Fatal("ack unavailable")
	}
	if localRelayTest(b, inputEnvelope{Action: "send", Session: session, Ticket: ticket, Packet: packet}, b.token).Code != 409 {
		t.Fatal("reused ticket sent twice")
	}
	b.close()
	if localRelayTest(b, inputEnvelope{Action: "read", Session: session, Ticket: ticket}, b.token).Code != 409 {
		t.Fatal("closed lease survived")
	}
}
func TestInputRelayExpiryBoundsAndOptIn(t *testing.T) {
	b := newInputRelay()
	session := randomID()
	localRelayTest(b, inputEnvelope{Action: "open", Session: session}, b.token)
	for _, packet := range []string{"unrelated clipboard", "HC-DRAFT/3 " + strings.Repeat("x", 262144)} {
		if localRelayTest(b, inputEnvelope{Action: "send", Session: session, Ticket: randomID(), Packet: packet}, b.token).Code != 400 {
			t.Fatal("unbounded or non-protocol packet")
		}
	}
	b.expires = time.Now().Add(-time.Second)
	if localRelayTest(b, inputEnvelope{Action: "send", Session: session, Ticket: randomID(), Packet: "HC-DRAFT/2 a"}, b.token).Code != 409 {
		t.Fatal("expired panel admitted")
	}
	s := testService(t)
	if s.input != nil {
		t.Fatal("relay enabled by default")
	}
	c := s.cfg
	c.InputRelayState = filepath.Join(c.Roots["work"], "token.dpapi")
	if c.Validate() == nil {
		t.Fatal("local bearer was placed in exposed root")
	}
}
func TestInputRelayUsesExistingRemoteIdentityAndRevocationGate(t *testing.T) {
	s := testService(t)
	s.input = newInputRelay()
	s.cfg.PeerIP = "100.80.0.2"
	r := httptest.NewRequest("POST", "https://100.80.0.1/v1/input-relay", strings.NewReader("{}"))
	r.RemoteAddr = "100.80.0.2:30000"
	for _, tlsState := range []*tls.ConnectionState{nil, {}} {
		r.TLS = tlsState
		w := httptest.NewRecorder()
		s.Handler().ServeHTTP(w, r)
		if w.Code != 403 {
			t.Fatal("relay bypassed peer identity")
		}
	}
	s.revoked = true
	w := httptest.NewRecorder()
	s.remoteInput(w, r)
	if w.Code != 403 {
		t.Fatal("revoked relay survived")
	}
}

func TestInputRelayLocalStateCannotAuthorizeFileSession(t *testing.T) {
	s := testService(t)
	s.cfg.InputRelayState = filepath.Join(t.TempDir(), "relay-state.dpapi")
	s.input = newInputRelay()
	address, err := s.StartControl()
	if err != nil {
		t.Fatal(err)
	}
	data, err := loadPrivateKey(s.cfg.InputRelayState)
	if err != nil {
		t.Fatal(err)
	}
	var state struct{ URL, Token string }
	if err := json.Unmarshal(data, &state); err != nil {
		t.Fatal(err)
	}
	base := strings.Split(address, "/#")[0]
	if state.URL != base+"/input-relay" || state.Token != s.input.token {
		t.Fatal("wrong private relay state")
	}
	request, _ := http.NewRequest("POST", base+"/enable?minutes=1", nil)
	request.Header.Set("Authorization", "Bearer "+state.Token)
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != 403 || s.active() {
		t.Fatal("input-only bearer enabled Windows file tools")
	}
}
