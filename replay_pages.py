"""Read-only, unlisted review pages protected by random share URLs."""
import base64,hashlib,html,json,re,secrets,threading,time
from datetime import datetime,timezone,timedelta
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from .replay_core import ReplayError,summarize,report_url,hand_name

def esc(value):return html.escape(str(value),quote=True)
def stamp(value):return datetime.fromtimestamp(value,timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M') if value else '手动提供的牌谱'

STYLE='''*{box-sizing:border-box}body{margin:0;background:#101821;color:#edf2f7;font:16px/1.6 system-ui,"Microsoft YaHei",sans-serif}main{max-width:1100px;margin:auto;padding:32px 20px 60px}header,.panel,.game{background:#1d2b38;border:1px solid #2c4050;border-radius:20px;padding:24px;margin-bottom:20px}small,.muted{color:#a1b3c5}h1{margin:5px 0 12px;font-size:30px}h2{font-size:21px;margin:0 0 12px}h3{font-size:18px;margin:0}a{color:#53d6cb;text-decoration:none}a:hover{text-decoration:underline}.badge{display:inline-block;padding:4px 12px;border-radius:20px;background:#263e4b;color:#53d6cb}.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:20px}.metric{padding:14px 18px;background:#14212c;border-radius:12px}.metric b{display:block;color:#53d6cb;font-size:30px}.chart{width:100%;height:auto}.game-top{display:flex;justify-content:space-between;gap:20px;align-items:start}.rate{font-size:32px;color:#ffcc7a;white-space:nowrap}.actions{display:flex;gap:12px;flex-wrap:wrap;margin-top:15px}.button{display:inline-block;padding:9px 16px;background:#294453;border-radius:10px}.button.primary{background:#53d6cb;color:#101821;font-weight:700}table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;border-bottom:1px solid #344755;padding:9px 7px}details{margin-top:20px}summary{cursor:pointer;color:#53d6cb}.scroll{overflow:auto}.decision{padding:12px 0;border-bottom:1px solid #344755}footer{font-size:13px;color:#a1b3c5}@media(max-width:600px){main{padding:16px 12px 35px}header,.panel,.game{padding:18px;border-radius:15px}.stats{gap:8px}.metric{padding:10px}.metric b{font-size:23px}h1{font-size:25px}.game-top{gap:8px}.rate{font-size:26px}}'''

STYLE+='''button.button{font:inherit;border:0;color:#53d6cb;cursor:pointer}button.button:focus-visible{outline:2px solid #ffcc7a;outline-offset:3px}textarea.copy-value{width:100%;min-height:100px;resize:vertical;background:#14212c;color:#edf2f7;border:1px solid #53d6cb;border-radius:8px;padding:12px;font:inherit;word-break:break-all}#copy-status{position:fixed;bottom:22px;left:50%;transform:translateX(-50%);padding:10px 18px;background:#294453;border-radius:10px;max-width:90%;z-index:10}#copy-status:empty{display:none}[hidden]{display:none!important}'''

COPY_SCRIPT=r'''
(() => {
  const status = document.getElementById('copy-status');
  const fallback = document.getElementById('copy-fallback');
  const value = document.getElementById('copy-value');
  let timer;
  document.getElementById('copy-close').addEventListener('click', () => { fallback.hidden = true; });
  document.addEventListener('click', async event => {
    const button = event.target.closest('button[data-copy-replay]');
    if (!button) return;
    const url = button.dataset.copyReplay;
    let copied = false;
    if (window.isSecureContext && navigator.clipboard && navigator.clipboard.writeText) {
      try { await navigator.clipboard.writeText(url); copied = true; } catch (_) {}
    }
    if (!copied) {
      const helper = document.createElement('textarea');
      helper.value = url;
      helper.style.cssText = 'position:fixed;top:0;left:0;opacity:0';
      document.body.appendChild(helper);
      helper.focus(); helper.select(); helper.setSelectionRange(0, url.length);
      try { copied = document.execCommand('copy'); } catch (_) {}
      helper.remove();
    }
    clearTimeout(timer);
    if (copied) {
      fallback.hidden = true;
      status.textContent = '牌谱链接已复制';
      button.textContent = '已复制';
      timer = setTimeout(() => { status.textContent = ''; button.textContent = '复制牌谱'; }, 2200);
    } else {
      fallback.hidden = false;
      value.value = url; value.focus(); value.select(); value.setSelectionRange(0, url.length);
      fallback.scrollIntoView({block: 'center'});
      status.textContent = '浏览器限制了自动复制，请在文本框中长按或手动复制。';
    }
  });
})();
'''
COPY_SCRIPT_HASH=base64.b64encode(hashlib.sha256(COPY_SCRIPT.encode('utf-8')).digest()).decode('ascii')

def render_page(batch,data,reports):
    games=data['games'];title=data.get('label') or '多牌谱复盘'
    rate=f"{data['accordance']:.2%}" if data['accordance'] is not None else '等待结果'
    refresh='<meta http-equiv="refresh" content="30">' if data['pending'] else ''
    chunks=[f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · 慧慧复盘</title>{refresh}<style>{STYLE}</style></head><body><main>']
    chunks.append(f'<header><span class="badge">Mortal 4.1b</span><h1>{esc(title)}</h1><small>批次 {esc(batch["id"])} · 四麻东南战 · '+('按对局时间从早到晚' if data.get('ordered_by')=='time' else '按链接提交顺序')+f'</small><div class="stats"><div class="metric">汇总一致率<b>{rate}</b></div><div class="metric">分析完成<b>{data["done"]}/{len(games)}</b></div><div class="metric">评估决策<b>{data["total"]}</b></div></div><p class="muted">一致 {data["matches"]} / 评估 {data["total"]}；等待 {data["pending"]}，未完成 {data["failed"]}。汇总按评估决策数加权。</p></header>')
    svg=['<svg class="chart" viewBox="0 0 1000 285" role="img" aria-label="逐场一致率趋势">'];previous=None
    for n in (0,25,50,75,100):
        y=230-n*2;svg.append(f'<line x1="55" y1="{y}" x2="970" y2="{y}" stroke="#344755"/><text x="8" y="{y+5}" fill="#a1b3c5" font-size="16">{n}%</text>')
    for i,g in enumerate(games):
        x=70+880*i/max(1,len(games)-1);svg.append(f'<text x="{x}" y="270" text-anchor="middle" fill="#a1b3c5">{i+1}</text>')
        if 'accordance' not in g:previous=None;continue
        y=230-g['accordance']*200
        if previous:svg.append(f'<line x1="{previous[0]}" y1="{previous[1]}" x2="{x}" y2="{y}" stroke="#53d6cb" stroke-width="4"/>')
        svg.append(f'<circle cx="{x}" cy="{y}" r="6" fill="#ffcc7a"/><text x="{x}" y="{y-13}" text-anchor="middle" fill="#edf2f7" font-size="16">{g["accordance"]:.1%}</text>');previous=x,y
    chunks.append('<section class="panel"><h2>逐场一致率趋势</h2>'+''.join(svg)+'</svg><small>横轴序号与下方对局一致；各场完整详情可展开查看。</small></section>')
    statuses={'queued':'排队中','running':'正在分析','failed':'未完成','cancelled':'已取消','manual':'等待验证'}
    for g,job in zip(games,batch['items']):
        number=g['position'];rating=f'{g["accordance"]:.2%}' if 'accordance' in g else statuses.get(g['status'],g['status'])
        detail=(f'{stamp(g.get("start_time"))} · 第{g["rank"]}位 · {g["score"]:,}点' if g.get('start_time') else stamp(None))
        seat='东南西北'[g['seat']]+'家' if type(g.get('seat')) is int and 0<=g['seat']<4 else '报告席位'
        chunks.append(f'<article class="game" id="game{number}"><div class="game-top"><div><h2>第 {number} 场 · {esc(detail)}</h2><small>开局{seat} · 单场编号 {esc(g["job_id"])}</small></div><b class="rate">{esc(rating)}</b></div>')
        if 'accordance' in g:chunks.append(f'<p>一致决策 {g["matches"]} / 评估决策 {g["total"]}</p>')
        chunks.append('<div class="actions">')
        link=report_url(g)
        if link and g['status']=='done':chunks.append(f'<a class="button primary" href="{esc(link)}" target="_blank" rel="noopener noreferrer">打开本场逐巡复盘 ↗</a>')
        chunks.append(f'<button type="button" class="button" data-copy-replay="{esc(g["url"])}">复制牌谱</button><a class="button" href="#game{number}">第 {number} 场定位</a></div>')
        chunks.append(f'<noscript><details><summary>手动复制牌谱</summary><textarea class="copy-value" readonly aria-label="完整牌谱链接">{esc(g["url"])}</textarea></details></noscript>')
        report=reports.get(g['job_id'])
        if report:
            chunks.append('<details><summary>展开分局数据与重点决策</summary><div class="scroll"><table><thead><tr><th>局数</th><th>一致 / 评估</th><th>一致率</th></tr></thead><tbody>')
            for h in report['hands']:
                value=f"{h['matches']/h['total']:.1%}" if h['total'] else '—'
                chunks.append(f'<tr><td>{esc(hand_name(h["kyoku"],h["honba"]))}</td><td>{h["matches"]} / {h["total"]}</td><td>{value}</td></tr>')
            chunks.append('</tbody></table></div><h3>值得回看的决策</h3>')
            for d in report['differences'][:8]:
                url=report_url(job,d['hand_index'],d['ply'])
                chunks.append(f'<div class="decision"><a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(hand_name(d["kyoku"],d["honba"]))} · 第{d["turn"]}巡 ↗</a><br>实际：{esc(d["actual"])}<br>Mortal：{esc(d["expected"])}</div>')
            chunks.append('</details>')
        elif g.get('error'):chunks.append(f'<p class="muted">{esc(g["error"])}</p>')
        chunks.append('</article>')
    chunks.append('<section class="panel" id="copy-fallback" hidden><h2>手动复制牌谱</h2><p>长按或全选下方完整链接进行复制：</p><textarea class="copy-value" id="copy-value" readonly aria-label="完整牌谱链接"></textarea><button type="button" class="button" id="copy-close">关闭</button></section><div id="copy-status" role="status" aria-live="polite"></div>')
    chunks.append('<footer>一致率只表示与 Mortal 4.1b 的决策吻合程度，不等于实战胜率；分歧不等于失误。<br>本页面保存于机器人服务器，外部 Mortal 报告可能过期；请保管好这条分享链接。</footer></main>')
    chunks.append('<script id="replay-copy">'+COPY_SCRIPT+'</script></body></html>')
    return ''.join(chunks)

class ReplayPages:
    def __init__(self,store,batches,public_url,port=6192,host='0.0.0.0'):
        parsed=urlsplit(public_url)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment:raise ReplayError('复盘网页地址须为完整的 http(s) 根地址。')
        self.store=store;self.batches=batches;self.public_url=public_url.rstrip('/');self.server=None;self.lock=threading.Lock();self.cache={}
        with store.db() as db:db.execute('CREATE TABLE IF NOT EXISTS replay_pages(token TEXT PRIMARY KEY,owner TEXT UNIQUE,scope TEXT,actor TEXT,created REAL)')
        parent=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                self.connection.settimeout(10)
                match=re.fullmatch(r'/r/([0-9a-f]{40})/?',urlsplit(self.path).path)
                if not match:self.reply(404,b'Not found','text/plain');return
                try:
                    content=parent.page(match[1]);self.reply(200,content.encode('utf-8'),'text/html; charset=utf-8')
                except KeyError:self.reply(404,b'Not found','text/plain')
                except Exception:self.reply(503,'报告暂时无法加载，请稍后刷新。'.encode('utf-8'),'text/plain; charset=utf-8')
            def reply(self,status,content,kind):
                self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(content)));self.send_header('Cache-Control','no-store');self.send_header('Referrer-Policy','no-referrer');self.send_header('X-Content-Type-Options','nosniff');self.send_header('X-Frame-Options','DENY');self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'sha256-"+COPY_SCRIPT_HASH+"'; style-src 'unsafe-inline'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'");self.end_headers()
                try:self.wfile.write(content)
                except OSError:pass
        self.server=ThreadingHTTPServer((host,int(port)),Handler);self.server.daemon_threads=True
        self.thread=threading.Thread(target=self.server.serve_forever,name='mahjong-review-pages',daemon=True)
    def start(self):self.thread.start()
    def close(self):self.server.shutdown();self.server.server_close();self.thread.join(timeout=3)
    def link(self,batch):
        with self.store.db() as db:
            row=db.execute('SELECT token FROM replay_pages WHERE owner=?',(batch['id'],)).fetchone()
            token=row[0] if row else secrets.token_hex(20)
            if not row:db.execute('INSERT INTO replay_pages VALUES(?,?,?,?,?)',(token,batch['id'],batch['scope'],batch['actor'],time.time()))
        return self.public_url+'/r/'+token
    def page(self,token):
        with self.store.db() as db:row=db.execute('SELECT * FROM replay_pages WHERE token=?',(token,)).fetchone()
        if not row:raise KeyError(token)
        with self.lock:
            cached=self.cache.get(token)
            if cached and time.monotonic()-cached[0]<10:return cached[1]
            batch=self.batches.get(row['scope'],row['actor'],row['owner']);data=self.batches.summary(batch);reports={}
            for job in batch['items']:
                if job['status']=='done':reports[job['id']]=summarize(json.loads(Path(job['report_path']).read_text(encoding='utf-8')),job['seat'])
            content=render_page(batch,data,reports)
            if len(self.cache)>=64:self.cache.clear()
            self.cache[token]=(time.monotonic(),content);return content
