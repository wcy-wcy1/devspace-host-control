package host

import (
	"crypto/subtle"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"strconv"
	"time"
)

const controlHTML = `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="referrer" content="no-referrer"><title>HostControl 本机控制</title><style>body{font:18px system-ui;max-width:680px;margin:60px auto;padding:24px;background:#121820;color:#e3eaf1}button,input{font:inherit;margin:8px;padding:12px}pre{white-space:pre-wrap}button{cursor:pointer}</style><h1>Windows 本机控制</h1><p>这里控制的是真实桌面。授权命令拥有当前用户权限。</p><pre id="status">读取状态…</pre><input id="minutes" type="number" min="1" value="30" aria-label="授权分钟"><button id="enable">开启限时会话</button><button id="pause">暂停并取消任务</button><button id="revoke">撤销设备配对</button><pre id="error"></pre><script>
const token=location.hash.slice(1);history.replaceState(null,'',location.pathname);const headers={'Authorization':'Bearer '+token};async function request(path){const r=await fetch(path,{method:'POST',headers,cache:'no-store'});if(!r.ok)throw Error(await r.text());return r.json()}async function refresh(){try{document.querySelector('#status').textContent=JSON.stringify(await request('/status'),null,2)}catch(e){document.querySelector('#error').textContent=e.message}}for(const action of ['enable','pause','revoke'])document.querySelector('#'+action).onclick=async()=>{try{await request('/'+action+(action==='enable'?'?minutes='+encodeURIComponent(document.querySelector('#minutes').value):''));await refresh()}catch(e){document.querySelector('#error').textContent=e.message}};refresh();setInterval(refresh,2000);
</script></html>`

func (s *Service) StartControl() (string, error) {
	l, e := net.Listen("tcp4", "127.0.0.1:0")
	if e != nil {
		return "", e
	}
	token := randomID() + randomID()
	origin := "http://" + l.Addr().String()
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
		if r.Host != l.Addr().String() || r.Header.Get("Origin") != "" && r.Header.Get("Origin") != origin {
			http.Error(w, "forbidden", 403)
			return
		}
		if r.URL.Path == "/" && r.Method == "GET" {
			w.Header().Set("Content-Type", "text/html; charset=utf-8")
			fmt.Fprint(w, controlHTML)
			return
		}
		if r.Method != "POST" || subtle.ConstantTimeCompare([]byte(r.Header.Get("Authorization")), []byte("Bearer "+token)) != 1 {
			http.Error(w, "forbidden", 403)
			return
		}
		switch r.URL.Path {
		case "/enable":
			m, e := strconv.Atoi(r.URL.Query().Get("minutes"))
			if e != nil || s.Enable(m) != nil {
				http.Error(w, "invalid duration or revoked pairing", 400)
				return
			}
		case "/pause":
			s.Pause(false)
		case "/revoke":
			if e := s.Pause(true); e != nil {
				http.Error(w, e.Error(), 500)
				return
			}
		case "/status":
		default:
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(s.Status())
	})
	server := &http.Server{Handler: handler, ReadHeaderTimeout: 3 * time.Second, ReadTimeout: 5 * time.Second, WriteTimeout: 10 * time.Second, IdleTimeout: 15 * time.Second}
	go server.Serve(l)
	return origin + "/#" + token, nil
}
