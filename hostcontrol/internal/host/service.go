package host

import (
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

type Content struct {
	Type     string `json:"type"`
	Text     string `json:"text,omitempty"`
	Data     string `json:"data,omitempty"`
	MimeType string `json:"mimeType,omitempty"`
}
type Result struct {
	Content []Content `json:"content"`
	IsError bool      `json:"isError,omitempty"`
}

func textResult(v any) Result {
	b, _ := json.Marshal(v)
	return Result{Content: []Content{{Type: "text", Text: string(b)}}}
}
func errorResult(e error) Result {
	return Result{Content: []Content{{Type: "text", Text: e.Error()}}, IsError: true}
}
func randomID() string {
	b := make([]byte, 16)
	if _, e := rand.Read(b); e != nil {
		panic(e)
	}
	return hex.EncodeToString(b)
}

type Call struct {
	OperationID string         `json:"operation_id"`
	Name        string         `json:"name"`
	Arguments   map[string]any `json:"arguments"`
}
type cached struct {
	hash   [32]byte
	result Result
	at     time.Time
}
type Service struct {
	cfg      Config
	mu       sync.Mutex
	deadline time.Time
	revoked  bool
	cache    map[string]cached
	callMu   sync.Mutex
	tasksMu  sync.Mutex
	tasks    map[string]*Task
	desktop  desktopState
	audit    *log.Logger
}

func NewService(c Config) (*Service, error) {
	s := &Service{cfg: c, cache: map[string]cached{}, tasks: map[string]*Task{}}
	if _, e := os.Stat(s.revocationPath()); e == nil {
		s.revoked = true
	} else if !os.IsNotExist(e) {
		return nil, e
	}
	if c.Audit != "" {
		f, e := os.OpenFile(c.Audit, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
		if e != nil {
			return nil, e
		}
		s.audit = log.New(f, "", log.LstdFlags|log.LUTC)
	}
	return s, nil
}
func (s *Service) Enable(minutes int) error {
	s.callMu.Lock()
	defer s.callMu.Unlock()
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.revoked {
		return fmt.Errorf("pairing revoked; edit approved fingerprint and restart to re-pair")
	}
	if minutes < 1 || minutes > s.cfg.SessionMinutes {
		return fmt.Errorf("duration exceeds configured session limit")
	}
	s.deadline = defaultDeadline(minutes)
	return nil
}
func (s *Service) revocationPath() string {
	return filepath.Join(filepath.Dir(s.cfg.Key), ".revoked-"+s.cfg.PeerFingerprint)
}
func (s *Service) Pause(revoke bool) error {
	s.callMu.Lock()
	defer s.callMu.Unlock()
	s.mu.Lock()
	s.deadline = time.Time{}
	if revoke {
		s.revoked = true
	}
	s.mu.Unlock()
	s.CancelTasks("session ended")
	if revoke {
		if e := writeNew(s.revocationPath(), []byte("revoked\n")); e != nil && !os.IsExist(e) {
			return fmt.Errorf("paused but persistent revocation failed; stop agent and remove client authorization")
		}
	}
	return nil
}
func (s *Service) active() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return !s.revoked && time.Now().Before(s.deadline)
}
func (s *Service) Status() map[string]any {
	s.mu.Lock()
	defer s.mu.Unlock()
	return map[string]any{"version": Version, "active": !s.revoked && time.Now().Before(s.deadline), "expires_at": s.deadline, "revoked": s.revoked, "capabilities": s.cfg.Capabilities}
}
func (s *Service) Watch(stop <-chan struct{}) {
	t := time.NewTicker(time.Second)
	defer t.Stop()
	for {
		select {
		case <-stop:
			return
		case <-t.C:
			s.callMu.Lock()
			if !s.active() {
				s.CancelTasks("session expired")
			}
			s.callMu.Unlock()
		}
	}
}

func capability(name string) string {
	switch {
	case strings.HasPrefix(name, "fs_"):
		return "files"
	case strings.HasPrefix(name, "exec_"):
		return "exec"
	case strings.HasPrefix(name, "desktop_") || name == "browser_open":
		return "desktop"
	}
	return ""
}

func (s *Service) Call(c Call) Result {
	if len(c.OperationID) != 32 {
		return errorResult(fmt.Errorf("operation_id must be 16 random bytes in hex"))
	}
	if _, e := hex.DecodeString(c.OperationID); e != nil {
		return errorResult(fmt.Errorf("invalid operation_id"))
	}
	s.callMu.Lock()
	defer s.callMu.Unlock()
	if !s.active() {
		return errorResult(fmt.Errorf("local session is paused, expired or revoked"))
	}
	if !validToolName(c.Name) {
		return errorResult(fmt.Errorf("invalid tool name"))
	}
	if e := validateArguments(c.Name, c.Arguments); e != nil {
		return errorResult(e)
	}
	b, _ := json.Marshal(c)
	h := sha256.Sum256(b)
	if v, ok := s.cache[c.OperationID]; ok {
		if v.hash != h {
			return errorResult(fmt.Errorf("operation_id reused with different arguments"))
		}
		return v.result
	}
	if len(s.cache) >= 2048 {
		for k, v := range s.cache {
			if time.Since(v.at) > 8*time.Hour {
				delete(s.cache, k)
			}
		}
		if len(s.cache) >= 2048 {
			return errorResult(fmt.Errorf("operation capacity reached; restart after ending session"))
		}
	}
	cap := capability(c.Name)
	if cap != "" && !s.cfg.Has(cap) {
		return errorResult(fmt.Errorf("capability disabled locally"))
	}
	var v any
	var e error
	var result Result
	switch {
	case c.Name == "host_status" || c.Name == "session_info":
		v = s.Status()
	case strings.HasPrefix(c.Name, "fs_"):
		v, e = s.fileCall(c.Name, c.Arguments)
	case c.Name == "exec_start" || c.Name == "exec_poll" || c.Name == "exec_cancel":
		v, e = s.taskCall(c.Name, c.Arguments)
	case cap == "desktop":
		result, e = s.desktopCall(c.Name, c.Arguments)
	default:
		e = fmt.Errorf("unknown tool")
	}
	if e != nil {
		result = errorResult(e)
	} else if result.Content == nil {
		result = textResult(v)
	}
	stored := result
	encoded, _ := json.Marshal(result)
	if len(encoded) > 4096 {
		stored = errorResult(fmt.Errorf("operation already completed; large result is not retained for replay; make a fresh read/capture/poll without replaying side effects"))
	}
	if !readOnlyTool(c.Name) {
		s.cache[c.OperationID] = cached{h, stored, time.Now()}
	}
	if s.audit != nil {
		s.audit.Printf("tool=%s error=%t", c.Name, result.IsError)
	}
	return result
}

func (s *Service) Handler() http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("Content-Type", "application/json")
		ip, _, e := net.SplitHostPort(r.RemoteAddr)
		if e != nil || canonicalIP(ip) != canonicalIP(s.cfg.PeerIP) || r.TLS == nil || len(r.TLS.PeerCertificates) == 0 || Fingerprint(r.TLS.PeerCertificates[0]) != s.cfg.PeerFingerprint || r.Header.Get("Origin") != "" {
			http.Error(w, "forbidden", 403)
			return
		}
		if r.URL.Path != "/v1/call" || r.Method != "POST" {
			http.NotFound(w, r)
			return
		}
		d := json.NewDecoder(http.MaxBytesReader(w, r.Body, 2<<20))
		d.DisallowUnknownFields()
		var c Call
		if e = d.Decode(&c); e != nil {
			http.Error(w, "invalid request", 400)
			return
		}
		if e = d.Decode(new(any)); e != io.EOF {
			http.Error(w, "one request required", 400)
			return
		}
		_ = json.NewEncoder(w).Encode(s.Call(c))
	})
}

type filteredListener struct {
	net.Listener
	peer string
}

func (l filteredListener) Accept() (net.Conn, error) {
	for {
		c, e := l.Listener.Accept()
		if e != nil {
			return nil, e
		}
		ip, _, e := net.SplitHostPort(c.RemoteAddr().String())
		if e == nil && canonicalIP(ip) == canonicalIP(l.peer) {
			return c, nil
		}
		c.Close()
	}
}

func (s *Service) Serve() error {
	tc, e := TLSConfig(s.cfg.CA, s.cfg.Cert, s.cfg.Key, s.cfg.PeerFingerprint, true)
	if e != nil {
		return e
	}
	l, e := net.Listen("tcp", s.cfg.Listen)
	if e != nil {
		return e
	}
	server := &http.Server{Handler: s.Handler(), ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 15 * time.Second, WriteTimeout: 30 * time.Second, IdleTimeout: 30 * time.Second, MaxHeaderBytes: 8192}
	return server.Serve(tls.NewListener(filteredListener{l, s.cfg.PeerIP}, tc))
}
