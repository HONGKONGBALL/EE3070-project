"""Static local pairing confirmation page assets."""

from typing import Final

HTML: Final = """<!doctype html>
<html lang="zh-CN">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>导航设备配对</title>
<link rel="stylesheet" href="/app.css">
<main>
<p class="eyebrow">EE3070 · USB 配对</p>
<h1>核对六位数字</h1>
<p id="message" role="status">正在连接本机确认服务</p>
<div id="number" aria-live="polite">··· ···</div>
<p id="time">准备配对</p>
<p class="steps">核对手机数字一致 → 手机点配对 → 本页确认一致</p>
<div class="actions">
<button id="open">开始配对</button>
<button id="confirm" disabled>确认数字一致</button>
<button id="reject" disabled>拒绝本次配对</button>
</div>
<p id="state">等待设备状态</p>
</main>
<script src="/app.js" defer>
</script>
</html>"""

CSS: Final = """body{margin:0;
background:#edf1ed;
color:#17291e;
font:18px system-ui,sans-serif}
main{max-width:720px;
margin:7vh auto;
padding:40px;
background:#fff;
border-radius:24px;
box-shadow:0 12px 50px #243a2420}
.eyebrow{letter-spacing:.14em;
color:#53705b;
font-size:14px}h1{font-size:38px;
margin:12px 0}
#message{min-height:3em;
line-height:1.7}#number{font:700 clamp(48px,12vw,90px) ui-monospace,monospace;
letter-spacing:.12em;
padding:20px 0}
#time{font-weight:600}.steps{color:#4a5f50;
line-height:1.8}.actions{display:flex;
flex-wrap:wrap;
gap:12px;
margin:26px 0}
button{border:1px solid #9aab9e;
border-radius:12px;
padding:16px 20px;
background:#fff;
color:#173121;
font:600 17px system-ui;
cursor:pointer}
#confirm{background:#1e5937;
color:white;
border-color:#1e5937}button:disabled{opacity:.38;
cursor:default}#state{font:14px ui-monospace,monospace;
color:#68776c}
@media(max-width:760px){main{margin:12px;
padding:25px}.actions{flex-direction:column}button{width:100%}}"""

JS: Final = """'use strict';

const token=location.hash.slice(1), el=id=>document.getElementById(id);

let current=null, latest=-1, pending=false, fresh=0, expiry=0;

function disable(){el('confirm').disabled=true;
el('reject').disabled=true;
}
function render(v,sent){if(v.revision<latest)return;
latest=v.revision;
current=v;
fresh=performance.now();
expiry=sent+v.remaining_ms;

 const s=v.status;
el('message').textContent=v.message;

 el('number').textContent=s&&s.state==='COMPARING'?String(s.number).padStart(6,'0'):'··· ···';

 el('state').textContent=s?s.state+' · '+s.reason:'等待设备状态';

 el('time').textContent=v.authorized?'配对已完成':v.remaining_ms>0?
 '当前窗口剩余 '+Math.ceil(v.remaining_ms/1000)+' 秒':'等待当前设备窗口';

 el('open').disabled=pending||!v.can_open;

 el('confirm').disabled=pending||!v.can_decide;
el('reject').disabled=pending||!v.can_decide;

}
async function poll(){const sent=performance.now();
try{const r=await fetch('/api/state',{headers:{'X-Pairing-Token':token},cache:'no-store'});
if(!r.ok)throw Error();
render(await r.json(),sent);
}
 catch{disable();
el('open').disabled=true;
el('message').textContent='请保持确认页连接，等待服务恢复';
}}
async function act(action){if(pending)return;
let body={action};
if(action!=='open'){
 if(!current||!current.can_decide||performance.now()-fresh>1000||performance.now()>=expiry)return;

 const s=current.status;
body={action,window_id:s.window_id,connection_epoch:s.connection_epoch,number:s.number,revision:current.revision};
}
 pending=true;
disable();
el('open').disabled=true;

 try{const r=await fetch('/api/action',{method:'POST',
 headers:{'Content-Type':'application/json','X-Pairing-Token':token},
 body:JSON.stringify(body)});

 if(!r.ok)el('message').textContent='请等待设备更新后重新选择';
}catch{el('message').textContent='请等待本机服务连接恢复';
}
 finally{pending=false;
await poll();
}}
el('open').onclick=()=>act('open');
el('confirm').onclick=()=>act('confirm');
el('reject').onclick=()=>act('reject');

setInterval(()=>{if(performance.now()-fresh>1000||performance.now()>=expiry)disable();
},100);

setInterval(poll,200);
poll();

"""
