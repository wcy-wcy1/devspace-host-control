package host

import (
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/hex"
	"encoding/pem"
	"fmt"
	"math/big"
	"net"
	"os"
	"path/filepath"
	"time"
)

func Fingerprint(cert *x509.Certificate) string {
	h := sha256.Sum256(cert.Raw)
	return hex.EncodeToString(h[:])
}
func serial() *big.Int {
	n, e := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	if e != nil {
		panic(e)
	}
	return n
}
func template(name string) *x509.Certificate {
	return &x509.Certificate{SerialNumber: serial(), Subject: pkix.Name{CommonName: name}, NotBefore: time.Now().Add(-time.Minute), NotAfter: time.Now().AddDate(0, 3, 0), KeyUsage: x509.KeyUsageDigitalSignature | x509.KeyUsageKeyEncipherment}
}
func readCert(path string) (*x509.Certificate, error) {
	b, e := os.ReadFile(path)
	if e != nil {
		return nil, e
	}
	p, _ := pem.Decode(b)
	if p == nil {
		return nil, fmt.Errorf("invalid certificate PEM")
	}
	return x509.ParseCertificate(p.Bytes)
}
func writeNew(path string, b []byte) error {
	f, e := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if e != nil {
		return e
	}
	_, e = f.Write(b)
	ce := f.Close()
	if e != nil {
		return e
	}
	return ce
}
func privateDir(dir string) error {
	if dir == "" {
		return fmt.Errorf("new empty credential directory required")
	}
	if entries, e := os.ReadDir(dir); e == nil && len(entries) != 0 {
		return fmt.Errorf("credential directory must be empty; do not use a workspace or user home")
	} else if e != nil && !os.IsNotExist(e) {
		return e
	}
	if e := os.MkdirAll(dir, 0700); e != nil {
		return e
	}
	return protectDir(dir)
}
func saveKey(path string, k *rsa.PrivateKey) error {
	b, e := x509.MarshalPKCS8PrivateKey(k)
	if e != nil {
		return e
	}
	b, e = protectKey(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: b}))
	if e != nil {
		return e
	}
	return writeNew(path, b)
}
func saveCert(path string, b []byte) error {
	return writeNew(path, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: b}))
}

// InitPKI runs on the Windows host. Only public ca.pem/agent.pem are exported.
func InitPKI(dir, ip string) error {
	if !IsVPN(ip) {
		return fmt.Errorf("a real Tailscale IP is required")
	}
	if e := privateDir(dir); e != nil {
		return e
	}
	caKey, e := rsa.GenerateKey(rand.Reader, 3072)
	if e != nil {
		return e
	}
	ca := template("hostcontrol-ca")
	ca.IsCA = true
	ca.BasicConstraintsValid = true
	ca.KeyUsage |= x509.KeyUsageCertSign
	ca.NotAfter = time.Now().AddDate(1, 0, 0)
	der, e := x509.CreateCertificate(rand.Reader, ca, ca, &caKey.PublicKey, caKey)
	if e != nil {
		return e
	}
	if e = saveKey(filepath.Join(dir, "ca-key.pem"), caKey); e != nil {
		return e
	}
	if e = saveCert(filepath.Join(dir, "ca.pem"), der); e != nil {
		return e
	}
	key, e := rsa.GenerateKey(rand.Reader, 3072)
	if e != nil {
		return e
	}
	cert := template("hostcontrol-agent")
	cert.IPAddresses = []net.IP{net.ParseIP(ip)}
	cert.ExtKeyUsage = []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}
	der, e = x509.CreateCertificate(rand.Reader, cert, ca, &key.PublicKey, caKey)
	if e != nil {
		return e
	}
	if e = saveKey(filepath.Join(dir, "agent-key.pem"), key); e != nil {
		return e
	}
	return saveCert(filepath.Join(dir, "agent.pem"), der)
}

// RequestPKI runs on Ubuntu. client-key.pem must never leave that machine.
func RequestPKI(dir string) error {
	if e := privateDir(dir); e != nil {
		return e
	}
	key, e := rsa.GenerateKey(rand.Reader, 3072)
	if e != nil {
		return e
	}
	if e = saveKey(filepath.Join(dir, "client-key.pem"), key); e != nil {
		return e
	}
	b, e := x509.CreateCertificateRequest(rand.Reader, &x509.CertificateRequest{Subject: pkix.Name{CommonName: "hostcontrol-controller"}}, key)
	if e != nil {
		return e
	}
	return writeNew(filepath.Join(dir, "client.csr"), pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE REQUEST", Bytes: b}))
}

func ApprovePKI(caDir, csrPath, output string) error {
	ca, e := readCert(filepath.Join(caDir, "ca.pem"))
	if e != nil {
		return e
	}
	b, e := loadPrivateKey(filepath.Join(caDir, "ca-key.pem"))
	if e != nil {
		return e
	}
	p, _ := pem.Decode(b)
	if p == nil {
		return fmt.Errorf("invalid CA key")
	}
	key, e := x509.ParsePKCS8PrivateKey(p.Bytes)
	if e != nil {
		return e
	}
	b, e = os.ReadFile(csrPath)
	if e != nil {
		return e
	}
	p, _ = pem.Decode(b)
	if p == nil {
		return fmt.Errorf("invalid CSR")
	}
	csr, e := x509.ParseCertificateRequest(p.Bytes)
	if e != nil {
		return e
	}
	if e = csr.CheckSignature(); e != nil {
		return e
	}
	c := template("hostcontrol-controller")
	c.ExtKeyUsage = []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth}
	der, e := x509.CreateCertificate(rand.Reader, c, ca, csr.PublicKey, key)
	if e != nil {
		return e
	}
	return saveCert(output, der)
}

func CertificateFingerprint(path string) (string, error) {
	c, e := readCert(path)
	if e != nil {
		return "", e
	}
	return Fingerprint(c), nil
}

func TLSConfig(caPath, certPath, keyPath, peer string, server bool) (*tls.Config, error) {
	certBytes, e := os.ReadFile(certPath)
	if e != nil {
		return nil, e
	}
	keyBytes, e := loadPrivateKey(keyPath)
	if e != nil {
		return nil, e
	}
	pair, e := tls.X509KeyPair(certBytes, keyBytes)
	if e != nil {
		return nil, e
	}
	b, e := os.ReadFile(caPath)
	if e != nil {
		return nil, e
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(b) {
		return nil, fmt.Errorf("invalid CA")
	}
	c := &tls.Config{MinVersion: tls.VersionTLS13, Certificates: []tls.Certificate{pair}, RootCAs: pool, NextProtos: []string{"http/1.1"}}
	if server {
		c.ClientCAs = pool
		c.ClientAuth = tls.RequireAndVerifyClientCert
	}
	c.VerifyConnection = func(s tls.ConnectionState) error {
		if len(s.PeerCertificates) == 0 || Fingerprint(s.PeerCertificates[0]) != peer {
			return fmt.Errorf("unpaired certificate")
		}
		return nil
	}
	return c, nil
}
