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

const controlHTML = `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="referrer" content="no-referrer"><title>HostControl 本机控制</title><style>body{font:17px system-ui;max-width:760px;margin:40px auto;padding:24px;background:#121820;color:#e3eaf1}button,input,select{font:inherit;margin:6px;padding:10px}pre{white-space:pre-wrap;overflow-wrap:anywhere}button{cursor:pointer}.workspace{border:1px solid #405268;border-radius:10px;padding:16px;margin:12px 0}small{display:block;color:#b4c2d3}#error{color:#ffb4a8}</style><h1>Windows 本机控制</h1><p id="capabilities"></p><pre id="status">读取状态…</pre><div id="workspaces"></div><p>目录权限只能在本机修改。保存后暂停会话，请重新授权。</p><p><strong>剪贴板是独立通道</strong>：不受目录白名单限制；默认开启文本传递，实际开关以远程桌面配置为准。本页不读取剪贴板。</p><input id="minutes" type="number" min="1" value="30" aria-label="授权分钟"><button id="enable">开启限时会话</button><button id="pause">暂停并取消任务</button><button id="revoke">撤销设备配对</button><pre id="error" role="alert"></pre><script>
const token=location.hash.slice(1);history.replaceState(null,'',location.pathname);const headers={'Authorization':'Bearer '+token};let rendered='',refreshing=false;const el=id=>document.getElementById(id);
async function request(path){const r=await fetch(path,{method:'POST',headers,cache:'no-store'});if(!r.ok)throw Error(await r.text());return r.json()}
async function refresh(){if(refreshing)return;refreshing=true;try{const s=await request('/status');const left=s.active?Math.max(0,Math.ceil((Date.parse(s.expires_at)-Date.now())/1000)):0;el('status').textContent=s.revoked?'设备配对已撤销':s.active?'会话已开启 · 剩余 '+Math.floor(left/60)+' 分 '+left%60+' 秒':'会话已暂停';el('capabilities').textContent='允许能力：'+s.capabilities.join('、')+(s.capabilities.includes('exec')?'（命令使用当前 Windows 用户权限）':'');el('minutes').max=s.max_session_minutes;if(Number(el('minutes').value)>s.max_session_minutes)el('minutes').value=s.max_session_minutes;el('enable').disabled=s.revoked;
const signature=JSON.stringify([s.local_workspaces,s.permissions_editable]);if(signature!==rendered){rendered=signature;el('workspaces').replaceChildren();for(const w of s.local_workspaces){const row=document.createElement('div');row.className='workspace';const title=document.createElement('strong');title.textContent=w.name;const path=document.createElement('pre');path.textContent=w.path;const select=document.createElement('select');select.setAttribute('aria-label',w.name+' 目录权限');for(const [value,label] of [['read-only','只读'],['read-write','读写']]){const option=document.createElement('option');option.value=value;option.textContent=label;select.append(option)}select.value=w.access;select.disabled=!s.permissions_editable;const save=document.createElement('button');save.textContent='保存权限';save.disabled=!s.permissions_editable;save.onclick=async()=>{save.disabled=true;try{await request('/workspace-access?workspace='+encodeURIComponent(w.name)+'&access='+encodeURIComponent(select.value));el('error').textContent='权限已保存，会话已暂停；请重新开启限时会话。';rendered='';await refresh()}catch(e){el('error').textContent=e.message}finally{save.disabled=!s.permissions_editable}};row.append(title,path,select,save);el('workspaces').append(row)}}}catch(e){el('error').textContent=e.message;el('status').textContent='无法确认授权状态';el('enable').disabled=true}finally{refreshing=false}}
for(const action of ['enable','pause','revoke'])el(action).onclick=async()=>{try{await request('/'+action+(action==='enable'?'?minutes='+encodeURIComponent(el('minutes').value):''));el('error').textContent='';await refresh()}catch(e){el('error').textContent=e.message}};refresh();setInterval(refresh,1000);
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
		case "/workspace-access":
			if e := s.setWorkspaceAccess(r.URL.Query().Get("workspace"), r.URL.Query().Get("access")); e != nil {
				http.Error(w, e.Error(), http.StatusConflict)
				return
			}
		default:
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(s.localStatus())
	})
	server := &http.Server{Handler: handler, ReadHeaderTimeout: 3 * time.Second, ReadTimeout: 5 * time.Second, WriteTimeout: 10 * time.Second, IdleTimeout: 15 * time.Second}
	go server.Serve(l)
	return origin + "/#" + token, nil
}
