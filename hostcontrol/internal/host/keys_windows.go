//go:build windows

package host

import (
	"bytes"
	"fmt"
	"os"
	"syscall"
	"unsafe"
)

var crypt32 = syscall.NewLazyDLL("crypt32.dll")
var cryptProtect = crypt32.NewProc("CryptProtectData")
var cryptUnprotect = crypt32.NewProc("CryptUnprotectData")
var localFree = syscall.NewLazyDLL("kernel32.dll").NewProc("LocalFree")

type dataBlob struct {
	Size uint32
	Data *byte
}

var keyHeader = []byte("HOSTCONTROL-DPAPI\n")

func keyCrypt(b []byte, encrypt bool) ([]byte, error) {
	if len(b) == 0 {
		return nil, fmt.Errorf("empty key")
	}
	in := dataBlob{Size: uint32(len(b)), Data: &b[0]}
	var out dataBlob
	proc := cryptUnprotect
	if encrypt {
		proc = cryptProtect
	}
	ok, _, _ := proc.Call(uintptr(unsafe.Pointer(&in)), 0, 0, 0, 0, 1, uintptr(unsafe.Pointer(&out)))
	if ok == 0 {
		return nil, fmt.Errorf("Windows DPAPI key operation failed for the current user")
	}
	defer localFree.Call(uintptr(unsafe.Pointer(out.Data)))
	return append([]byte(nil), unsafe.Slice(out.Data, int(out.Size))...), nil
}
func protectKey(b []byte) ([]byte, error) {
	encrypted, e := keyCrypt(b, true)
	if e != nil {
		return nil, e
	}
	return append(append([]byte(nil), keyHeader...), encrypted...), nil
}
func loadPrivateKey(path string) ([]byte, error) {
	b, e := os.ReadFile(path)
	if e != nil {
		return nil, e
	}
	if bytes.HasPrefix(b, keyHeader) {
		return keyCrypt(b[len(keyHeader):], false)
	}
	return b, nil
}
