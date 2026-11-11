package host

import (
	"bytes"
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func testService(t *testing.T) *Service {
	t.Helper()
	root := t.TempDir()
	s, e := NewService(Config{Roots: map[string]string{"work": root}, Capabilities: []string{"files", "exec", "desktop"}, SessionMinutes: 30, Key: filepath.Join(t.TempDir(), "agent-key.pem"), PeerFingerprint: strings.Repeat("a", 64)})
	if e != nil {
		t.Fatal(e)
	}
	return s
}
func args(v ...string) map[string]any {
	a := map[string]any{}
	for i := 0; i < len(v); i += 2 {
		a[v[i]] = v[i+1]
	}
	return a
}
func invoke(s *Service, name string, a map[string]any) Result {
	return s.Call(Call{randomID(), name, a})
}
func decodeResult(t *testing.T, r Result) map[string]any {
	t.Helper()
	if r.IsError {
		t.Fatal(r.Content)
	}
	var v map[string]any
	if e := json.Unmarshal([]byte(r.Content[0].Text), &v); e != nil {
		t.Fatal(e)
	}
	return v
}

func TestConfigRejectsBroadSources(t *testing.T) {
	s := testService(t)
	c := s.cfg
	c.Listen = "100.100.100.10:7443"
	c.PeerIP = "100.100.100.11"
	c.CA = "ca"
	c.Cert = "cert"
	if e := c.Validate(); e != nil {
		t.Fatal(e)
	}
	for _, ip := range []string{"0.0.0.0", "127.0.0.1", "192.168.1.1", "100.64.0.0/10", "::", "fd7a:115c:a1e0::/48", "example.com"} {
		cc := c
		cc.PeerIP = ip
		if cc.Validate() == nil {
			t.Fatalf("accepted source %q", ip)
		}
	}
	cc := c
	cc.Listen = "0.0.0.0:7443"
	if cc.Validate() == nil {
		t.Fatal("wildcard listener accepted")
	}
	cl := ClientConfig{Endpoint: "https://100.100.100.10:7443", CA: "ca", Cert: "cert", Key: "key", ServerFingerprint: strings.Repeat("a", 64)}
	if e := cl.Validate(); e != nil {
		t.Fatal(e)
	}
	for _, endpoint := range []string{"http://100.100.100.10:7443", "https://example.com", "https://100.100.100.10/a", "https://u:p@100.100.100.10"} {
		cl.Endpoint = endpoint
		if cl.Validate() == nil {
			t.Fatalf("accepted endpoint %q", endpoint)
		}
	}
}

func TestSessionWritesDedupAndPersistentRevocation(t *testing.T) {
	s := testService(t)
	call := Call{randomID(), "fs_write", args("workspace", "work", "path", "a.txt", "text", "hello")}
	if !s.Call(call).IsError {
		t.Fatal("paused session accepted write")
	}
	if e := s.Enable(1); e != nil {
		t.Fatal(e)
	}
	if s.Call(call).IsError || s.Call(call).IsError {
		t.Fatal("write or safe replay failed")
	}
	changed := call
	changed.Arguments = args("workspace", "work", "path", "a.txt", "text", "different")
	if !s.Call(changed).IsError {
		t.Fatal("operation ID collision accepted")
	}
	if !invoke(s, "fs_write", args("workspace", "work", "path", "a.txt", "text", "overwrite")).IsError {
		t.Fatal("overwrite without expected hash accepted")
	}
	read := decodeResult(t, invoke(s, "fs_read", args("workspace", "work", "path", "a.txt")))
	updated := decodeResult(t, invoke(s, "fs_write", args("workspace", "work", "path", "a.txt", "text", "updated", "expected_sha256", read["sha256"].(string))))
	backup := updated["backup"].(string)
	b, e := os.ReadFile(filepath.Join(s.cfg.Roots["work"], backup))
	if e != nil || string(b) != "hello" {
		t.Fatal("backup missing")
	}
	if e = s.Pause(true); e != nil {
		t.Fatal(e)
	}
	if s.Enable(1) == nil {
		t.Fatal("revoked enabled")
	}
	restarted, e := NewService(s.cfg)
	if e != nil {
		t.Fatal(e)
	}
	if restarted.Enable(1) == nil {
		t.Fatal("restart cleared revocation")
	}
}

func TestWorkspaceTraversalAndSymlink(t *testing.T) {
	s := testService(t)
	s.Enable(1)
	for _, p := range []string{"../secret", "/etc/passwd", "C:/Windows/win.ini", "a/../../b", "file.txt:stream", "a\\b", "name. ", "name."} {
		if !invoke(s, "fs_read", args("workspace", "work", "path", p)).IsError {
			t.Fatalf("path accepted %q", p)
		}
	}
	outside := t.TempDir()
	os.WriteFile(filepath.Join(outside, "secret"), []byte("secret"), 0600)
	if e := makeTestLink(outside, filepath.Join(s.cfg.Roots["work"], "escape")); e != nil {
		t.Log("symlink creation unavailable; traversal tests still executed:", e)
		return
	}
	if !invoke(s, "fs_read", args("workspace", "work", "path", "escape/secret")).IsError {
		t.Fatal("symlink escape read")
	}
	if !invoke(s, "fs_write", args("workspace", "work", "path", "escape/new", "text", "bad")).IsError {
		t.Fatal("symlink escape write")
	}
}

func TestExpiredAndDisabledCapabilities(t *testing.T) {
	s := testService(t)
	s.Enable(1)
	s.mu.Lock()
	s.deadline = time.Now().Add(-time.Second)
	s.mu.Unlock()
	if !invoke(s, "host_status", nil).IsError {
		t.Fatal("expired session accepted")
	}
	s.Enable(1)
	s.cfg.Capabilities = []string{"files"}
	if !invoke(s, "exec_start", nil).IsError {
		t.Fatal("disabled capability accepted")
	}
}

func TestMCPWireFormat(t *testing.T) {
	in := strings.NewReader("{bad}\n" + `{"jsonrpc":"2.0","id":1,"method":"tools/list"}` + "\n" + `{"jsonrpc":"2.0","id":2,"method":"initialize","params":{"protocolVersion":"2025-11-25"}}` + "\n" + `{"jsonrpc":"2.0","method":"notifications/initialized"}` + "\n" + `{"jsonrpc":"2.0","id":3,"method":"tools/list"}` + "\n" + `{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"host_status","arguments":{}}}` + "\n")
	var out bytes.Buffer
	count := 0
	e := RunMCP(in, &out, func(ctx context.Context, c Call) (Result, error) {
		count++
		if c.OperationID == "" {
			t.Fatal("missing ID")
		}
		return textResult(map[string]any{"active": true}), nil
	})
	if e != nil {
		t.Fatal(e)
	}
	dec := json.NewDecoder(&out)
	responses := []map[string]any{}
	for {
		var r map[string]any
		if e := dec.Decode(&r); e == io.EOF {
			break
		} else if e != nil {
			t.Fatal(e)
		}
		responses = append(responses, r)
	}
	if len(responses) != 5 || count != 1 {
		t.Fatal("protocol message count", len(responses), count)
	}
	tools := responses[3]["result"].(map[string]any)["tools"].([]any)
	if len(tools) != 18 {
		t.Fatalf("unexpected tools: %d", len(tools))
	}
}

func testTLSFiles(t *testing.T) (string, string, string, string, string, string, string) {
	t.Helper()
	dir := t.TempDir()
	caKey, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	ca := template("test-ca")
	ca.IsCA = true
	ca.BasicConstraintsValid = true
	ca.KeyUsage |= x509.KeyUsageCertSign
	der, e := x509.CreateCertificate(rand.Reader, ca, ca, &caKey.PublicKey, caKey)
	if e != nil {
		t.Fatal(e)
	}
	saveCert(filepath.Join(dir, "ca.pem"), der)
	create := func(name string, server bool) (string, string, string) {
		key, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
		c := template(name)
		if server {
			c.IPAddresses = []net.IP{net.ParseIP("127.0.0.1")}
			c.ExtKeyUsage = []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}
		} else {
			c.ExtKeyUsage = []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth}
		}
		der, e := x509.CreateCertificate(rand.Reader, c, ca, &key.PublicKey, caKey)
		if e != nil {
			t.Fatal(e)
		}
		certPath, keyPath := filepath.Join(dir, name+".pem"), filepath.Join(dir, name+"-key.pem")
		saveCert(certPath, der)
		kb, _ := x509.MarshalPKCS8PrivateKey(key)
		os.WriteFile(keyPath, pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: kb}), 0600)
		parsed, _ := x509.ParseCertificate(der)
		return certPath, keyPath, Fingerprint(parsed)
	}
	sc, sk, sf := create("server", true)
	cc, ck, cf := create("client", false)
	return filepath.Join(dir, "ca.pem"), sc, sk, sf, cc, ck, cf
}

func TestMutualTLSAndSourceFilter(t *testing.T) {
	ca, sc, sk, sf, cc, ck, cf := testTLSFiles(t)
	s := testService(t)
	s.cfg.PeerIP = "127.0.0.1"
	s.cfg.PeerFingerprint = cf
	s.Enable(1)
	tlsConfig, e := TLSConfig(ca, sc, sk, cf, true)
	if e != nil {
		t.Fatal(e)
	}
	server := httptest.NewUnstartedServer(s.Handler())
	server.TLS = tlsConfig
	server.StartTLS()
	defer server.Close()
	cfg := ClientConfig{server.URL, ca, cc, ck, sf}
	caller, e := NewCaller(cfg)
	if e != nil {
		t.Fatal(e)
	}
	call := Call{randomID(), "fs_write", args("workspace", "work", "path", "tls.txt", "text", "through TLS")}
	r, e := caller(context.Background(), call)
	if e != nil || r.IsError {
		t.Fatal("paired call", e, r)
	}
	cfg.ServerFingerprint = strings.Repeat("f", 64)
	bad, _ := NewCaller(cfg)
	if _, e = bad(context.Background(), Call{randomID(), "host_status", nil}); e == nil {
		t.Fatal("wrong server identity accepted")
	}
	s.cfg.PeerIP = "100.100.100.99"
	if _, e = caller(context.Background(), Call{randomID(), "host_status", nil}); e == nil {
		t.Fatal("wrong source accepted")
	}
	s.cfg.PeerIP = "127.0.0.1"
	// A certificate signed by the CA still needs the exact approved fingerprint.
	s.cfg.PeerFingerprint = strings.Repeat("e", 64)
	if _, e = caller(context.Background(), Call{randomID(), "host_status", nil}); e == nil {
		t.Fatal("unapproved client accepted")
	}
	noCert := &http.Client{Transport: &http.Transport{TLSClientConfig: &tls.Config{RootCAs: tlsConfig.ClientCAs}}, Timeout: time.Second}
	if resp, e := noCert.Post(server.URL+"/v1/call", "application/json", strings.NewReader("{}")); e == nil {
		resp.Body.Close()
		t.Fatal("missing client certificate accepted")
	}
}

func TestControlLocalOnlyAndNoCSRF(t *testing.T) {
	s := testService(t)
	address, e := s.StartControl()
	if e != nil {
		t.Fatal(e)
	}
	parts := strings.Split(address, "/#")
	base, token := parts[0], parts[1]
	request := func(path, auth, origin string) int {
		r, _ := http.NewRequest("POST", base+path, nil)
		if auth != "" {
			r.Header.Set("Authorization", "Bearer "+auth)
		}
		if origin != "" {
			r.Header.Set("Origin", origin)
		}
		resp, e := http.DefaultClient.Do(r)
		if e != nil {
			t.Fatal(e)
		}
		defer resp.Body.Close()
		return resp.StatusCode
	}
	if request("/enable?minutes=1", "", "") != 403 {
		t.Fatal("anonymous local enable")
	}
	if request("/enable?minutes=1", token, "https://evil.invalid") != 403 {
		t.Fatal("cross-origin enabled")
	}
	if request("/enable?minutes=1", token, base) != 200 || !s.active() {
		t.Fatal("local authorization failed")
	}
	if request("/pause", token, base) != 200 || s.active() {
		t.Fatal("pause failed")
	}
}

func TestExecutionOutputAndProcessTreeCancellation(t *testing.T) {
	if os.Getenv("HOSTCONTROL_TEST_HELPER") != "" {
		t.Skip("parent only")
	}
	s := testService(t)
	s.Enable(1)
	marker := filepath.Join(s.cfg.Roots["work"], "escaped.txt")
	t.Setenv("HOSTCONTROL_TEST_HELPER", "parent")
	t.Setenv("HOSTCONTROL_TEST_MARKER", marker)
	r := decodeResult(t, invoke(s, "exec_start", map[string]any{"workspace": "work", "program": os.Args[0], "args": []any{"-test.run=TestExecutionHelper$"}, "timeout_seconds": float64(10)}))
	id := r["task_id"].(string)
	deadline := time.Now().Add(8 * time.Second)
	for {
		state := decodeResult(t, invoke(s, "exec_poll", args("task_id", id)))
		if strings.Contains(state["stdout"].(string), "child-started") {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("helper did not start", state)
		}
		time.Sleep(20 * time.Millisecond)
	}
	if e := s.Pause(false); e != nil {
		t.Fatal(e)
	}
	s.tasksMu.Lock()
	task := s.tasks[id]
	s.tasksMu.Unlock()
	select {
	case <-task.done:
	case <-time.After(3 * time.Second):
		t.Fatal("process did not terminate")
	}
	time.Sleep(2300 * time.Millisecond)
	if _, e := os.Stat(marker); !os.IsNotExist(e) {
		t.Fatal("child escaped cancellation")
	}
}

func TestExecutionHelper(t *testing.T) {
	mode := os.Getenv("HOSTCONTROL_TEST_HELPER")
	if mode == "" {
		return
	}
	if mode == "child" {
		time.Sleep(2 * time.Second)
		_ = os.WriteFile(os.Getenv("HOSTCONTROL_TEST_MARKER"), []byte("escaped"), 0600)
		os.Exit(0)
	}
	cmd := exec.Command(os.Args[0], "-test.run=TestExecutionHelper$")
	env := []string{}
	for _, v := range os.Environ() {
		if !strings.HasPrefix(v, "HOSTCONTROL_TEST_HELPER=") {
			env = append(env, v)
		}
	}
	cmd.Env = append(env, "HOSTCONTROL_TEST_HELPER=child")
	if e := cmd.Start(); e != nil {
		os.Exit(3)
	}
	fmt.Println("child-started")
	time.Sleep(30 * time.Second)
	os.Exit(0)
}

func TestOutputLimit(t *testing.T) {
	var o limitedOutput
	b := bytes.Repeat([]byte("x"), 300<<10)
	if n, e := o.Write(b); e != nil || n != len(b) {
		t.Fatal(n, e)
	}
	text, truncated := o.state()
	if len(text) != 256<<10 || !truncated {
		t.Fatal("output unbounded")
	}
}

func TestPairingCLILibraryRoundTrip(t *testing.T) {
	dir := t.TempDir()
	agent := filepath.Join(dir, "agent")
	client := filepath.Join(dir, "client")
	if e := InitPKI(agent, "100.80.0.1"); e != nil {
		t.Fatal(e)
	}
	if e := RequestPKI(client); e != nil {
		t.Fatal(e)
	}
	if e := ApprovePKI(agent, filepath.Join(client, "client.csr"), filepath.Join(client, "client.pem")); e != nil {
		t.Fatal(e)
	}
	cf, e := CertificateFingerprint(filepath.Join(client, "client.pem"))
	if e != nil {
		t.Fatal(e)
	}
	sf, e := CertificateFingerprint(filepath.Join(agent, "agent.pem"))
	if e != nil {
		t.Fatal(e)
	}
	if _, e = TLSConfig(filepath.Join(agent, "ca.pem"), filepath.Join(agent, "agent.pem"), filepath.Join(agent, "agent-key.pem"), cf, true); e != nil {
		t.Fatal(e)
	}
	if _, e = TLSConfig(filepath.Join(agent, "ca.pem"), filepath.Join(client, "client.pem"), filepath.Join(client, "client-key.pem"), sf, false); e != nil {
		t.Fatal(e)
	}
	if e = InitPKI(agent, "100.80.0.1"); e == nil {
		t.Fatal("credential directory overwritten")
	}
	if e = ApprovePKI(agent, filepath.Join(client, "client.csr"), filepath.Join(client, "client.pem")); e == nil {
		t.Fatal("certificate overwritten")
	}
}

func TestArgumentValidationBeforeSideEffects(t *testing.T) {
	s := testService(t)
	s.Enable(1)
	for _, c := range []Call{
		{randomID(), "fs_write", map[string]any{"workspace": "work", "path": "bad.txt", "text": true}},
		{randomID(), "exec_start", map[string]any{"workspace": "work", "program": "bad", "args": []any{}, "timeout_seconds": 1.5}},
		{randomID(), "host_status", map[string]any{"extra": "no"}},
	} {
		if !s.Call(c).IsError {
			t.Fatal("invalid arguments accepted")
		}
	}
	if _, e := os.Stat(filepath.Join(s.cfg.Roots["work"], "bad.txt")); !os.IsNotExist(e) {
		t.Fatal("invalid write had side effect")
	}
}
