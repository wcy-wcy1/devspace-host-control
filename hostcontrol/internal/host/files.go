package host

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"unicode/utf8"
)

func str(a map[string]any, k string) string { s, _ := a[k].(string); return s }
func number(a map[string]any, k string, d int) int {
	v, ok := a[k].(float64)
	if !ok {
		return d
	}
	return int(v)
}
func hash(b []byte) string { h := sha256.Sum256(b); return hex.EncodeToString(h[:]) }
func relative(s string) (string, error) {
	if s == "" {
		s = "."
	}
	if strings.ContainsAny(s, "\\:\x00") || strings.HasPrefix(s, "/") {
		return "", fmt.Errorf("use relative slash-separated workspace paths")
	}
	for _, p := range strings.Split(s, "/") {
		if p == ".." {
			return "", fmt.Errorf("parent traversal denied")
		}
		if strings.HasSuffix(p, ".") && p != "." || strings.HasSuffix(p, " ") {
			return "", fmt.Errorf("ambiguous Windows path denied")
		}
	}
	p := filepath.FromSlash(s)
	if !filepath.IsLocal(p) {
		return "", fmt.Errorf("path is not workspace-relative")
	}
	return p, nil
}

func (s *Service) workspace(a map[string]any) (*os.Root, string, error) {
	root, ok := s.cfg.Roots[str(a, "workspace")]
	if !ok {
		return nil, "", fmt.Errorf("unknown workspace")
	}
	p, e := relative(str(a, "path"))
	if e != nil {
		return nil, "", e
	}
	r, e := os.OpenRoot(root)
	if e != nil {
		return nil, "", fmt.Errorf("workspace unavailable")
	}
	return r, p, nil
}

func readLimited(r *os.Root, p string) ([]byte, error) {
	f, e := r.Open(p)
	if e != nil {
		return nil, fmt.Errorf("cannot open workspace file")
	}
	defer f.Close()
	st, e := f.Stat()
	if e != nil || !st.Mode().IsRegular() {
		return nil, fmt.Errorf("regular file required")
	}
	b, e := io.ReadAll(io.LimitReader(f, (1<<20)+1))
	if e != nil {
		return nil, fmt.Errorf("read failed")
	}
	if len(b) > 1<<20 {
		return nil, fmt.Errorf("file exceeds 1 MiB")
	}
	return b, nil
}

func (s *Service) fileCall(name string, a map[string]any) (any, error) {
	r, p, e := s.workspace(a)
	if e != nil {
		return nil, e
	}
	defer r.Close()
	switch name {
	case "fs_list":
		f, e := r.Open(p)
		if e != nil {
			return nil, fmt.Errorf("directory unavailable")
		}
		defer f.Close()
		entries, e := f.ReadDir(1001)
		if e != nil && e != io.EOF {
			return nil, fmt.Errorf("directory read failed")
		}
		if len(entries) > 1000 {
			return nil, fmt.Errorf("directory exceeds 1000 entries; select a subdirectory")
		}
		result := []map[string]any{}
		for _, v := range entries {
			result = append(result, map[string]any{"name": v.Name(), "directory": v.IsDir(), "symlink": v.Type()&os.ModeSymlink != 0})
		}
		return result, nil
	case "fs_read":
		b, e := readLimited(r, p)
		if e != nil {
			return nil, e
		}
		if !utf8.Valid(b) {
			return nil, fmt.Errorf("UTF-8 text files only")
		}
		return map[string]any{"text": string(b), "sha256": hash(b)}, nil
	case "fs_write":
		text, ok := a["text"].(string)
		if !ok || len(text) > 1<<20 {
			return nil, fmt.Errorf("text is required, maximum 1 MiB")
		}
		old, e := readLimited(r, p)
		exists := e == nil
		if !exists {
			if _, se := r.Lstat(p); se == nil || !os.IsNotExist(se) {
				return nil, fmt.Errorf("target exists or is inaccessible")
			}
		}
		if exists && str(a, "expected_sha256") != hash(old) {
			return nil, fmt.Errorf("write conflict: fs_read then supply expected_sha256")
		}
		if !exists && str(a, "expected_sha256") != "" {
			return nil, fmt.Errorf("write conflict: target missing")
		}
		temp := filepath.Join(filepath.Dir(p), ".hostcontrol-"+randomID())
		f, e := r.OpenFile(temp, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
		if e != nil {
			return nil, fmt.Errorf("cannot create file; parent must exist")
		}
		defer r.Remove(temp)
		_, e = f.WriteString(text)
		if e == nil {
			e = f.Sync()
		}
		ce := f.Close()
		if e != nil || ce != nil {
			return nil, fmt.Errorf("write failed")
		}
		backup := ""
		if exists {
			backup = p + ".hostcontrol-backup-" + randomID()
			bf, be := r.OpenFile(backup, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
			if be != nil {
				return nil, fmt.Errorf("backup failed")
			}
			_, be = bf.Write(old)
			bce := bf.Close()
			if be != nil || bce != nil {
				return nil, fmt.Errorf("backup failed")
			}
		}
		// Recheck before commit. External concurrent writers can still race this check.
		if exists {
			latest, le := readLimited(r, p)
			if le != nil || hash(latest) != hash(old) {
				return nil, fmt.Errorf("write conflict before commit")
			}
		} else {
			if _, le := r.Lstat(p); !os.IsNotExist(le) {
				return nil, fmt.Errorf("target appeared before commit")
			}
		}
		if e = r.Rename(temp, p); e != nil {
			return nil, fmt.Errorf("atomic replacement failed")
		}
		return map[string]any{"sha256": hash([]byte(text)), "backup": filepath.ToSlash(backup)}, nil
	case "fs_move":
		to, e := relative(str(a, "to"))
		if e != nil || to == "." {
			return nil, fmt.Errorf("invalid destination")
		}
		st, e := r.Lstat(p)
		if e != nil || !st.Mode().IsRegular() {
			return nil, fmt.Errorf("source must be a regular file")
		}
		if _, e = r.Lstat(to); !os.IsNotExist(e) {
			return nil, fmt.Errorf("destination exists or is inaccessible")
		}
		if e = r.Rename(p, to); e != nil {
			return nil, fmt.Errorf("move failed")
		}
		return map[string]any{"moved": true}, nil
	}
	return nil, fmt.Errorf("unknown file tool")
}

func (s *Service) execCWD(a map[string]any) (string, error) {
	root, ok := s.cfg.Roots[str(a, "workspace")]
	if !ok {
		return "", fmt.Errorf("unknown workspace")
	}
	p, e := relative(str(a, "path"))
	if e != nil {
		return "", e
	}
	// This is a working-directory validation, not a shell sandbox.
	target, e := filepath.EvalSymlinks(filepath.Join(root, p))
	if e != nil {
		return "", fmt.Errorf("working directory unavailable")
	}
	resolvedRoot, e := filepath.EvalSymlinks(root)
	if e != nil {
		return "", e
	}
	rel, e := filepath.Rel(resolvedRoot, target)
	if e != nil || !filepath.IsLocal(rel) {
		return "", fmt.Errorf("working directory outside workspace")
	}
	st, e := os.Stat(target)
	if e != nil || !st.IsDir() {
		return "", fmt.Errorf("working directory must be a directory")
	}
	return target, nil
}
