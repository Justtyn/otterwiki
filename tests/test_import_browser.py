#!/usr/bin/env python
# vim: set et ts=8 sts=4 sw=4 ai:

"""真实浏览器执行上传进度与轮询脚本；网络响应使用可控替身。"""

import html
import json
import os
from pathlib import Path
import re
import subprocess
import pytest


def test_import_browser_upload_polling_and_refresh(tmp_path):
    candidates = [os.environ.get('CHROME_BIN')] + list(
        (Path.home() / 'Library/Caches/ms-playwright').glob(
            'chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell'
        )
    )
    chrome = next(
        (str(p) for p in candidates if p and Path(p).is_file()), None
    )
    if not chrome:
        pytest.skip('需要 Chromium 验证导入交互')
    script = Path('otterwiki/static/js/document-import.js').read_text()
    fixture = '''<form id="document-import-form" action="http://example.test/-/admin/document_import"><button id="document-import-submit">提交</button></form>
    <section id="document-import-progress" hidden><span id="document-import-phase"></span><span id="document-import-message"></span><span id="document-import-counts"></span><span id="document-import-elapsed"></span><progress id="document-import-bar" max="100"></progress><pre id="document-import-result"></pre><a id="document-import-open" hidden></a></section><script type="application/json" id="document-import-task">null</script>'''
    checks = r'''
let timers=[], replies=[], xhr, lastDelay;
window.setTimeout=(f,d)=>{timers=[f];lastDelay=d;return 1;};
window.clearTimeout=()=>{timers=[];};
window.confirm=()=>true;
window.fetch=async()=>{let r=replies.shift();if(r instanceof Error)throw r;return r;};
window.XMLHttpRequest=class {
 constructor(){xhr=this;this.upload={};}
 open(){} setRequestHeader(){} send(data){this.data=data;}
};
const task={id:'task-123',status:'running',phase:'converting',phase_label:'转换文档与附件',completed:1,total:3,elapsed_seconds:3,status_url:'http://example.test/task',maintenance:true};
const el=id=>document.getElementById('document-import-'+id);
function ok(value,label){if(!value)throw new Error(label);}
function response(body,status=200){return {status,ok:status===200,redirected:false,headers:{get:()=> 'application/json'},json:async()=>body};}
async function tick(){let f=timers.shift();ok(f,'应安排轮询');f();for(let i=0;i<8;i++)await Promise.resolve();}
function setup(initial){document.getElementById('fixture').innerHTML=FIXTURE;el('task').textContent=JSON.stringify(initial);(0,eval)(SOURCE);}
(async()=>{
 setup(null);
 el('form').dispatchEvent(new Event('submit',{cancelable:true}));
 ok(xhr.data.get('request_key').length===32,'请求幂等标识');
 xhr.upload.onprogress({lengthComputable:true,loaded:5,total:10});
 ok(el('bar').value===50,'真实上传进度');
 xhr.upload.onprogress({lengthComputable:true,loaded:10,total:10});
 ok(el('phase').textContent.includes('等待服务器接收确认'),'上传100%不等于导入成功');
 xhr.status=202;xhr.responseText=JSON.stringify(task);xhr.onload();
 ok(lastDelay===2000 && el('submit').disabled,'后台轮询开始');
 for(let delay of [4000,8000,10000]){replies.push(new Error('断网'));await tick();ok(lastDelay===delay,'退避时间');}
 ok(el('message').textContent.includes('不代表导入失败'),'网络错误提示');
 replies.push(response({...task,status:'succeeded',maintenance:false,result:{pages:3,assets:2,commit:'abc',warnings:['提示']},document_url:'/apstack6'}));await tick();
 ok(!timers.length && !el('submit').disabled && !el('open').hidden,'成功结束');
 ok(el('result').textContent.includes('提示'),'最终统计和警告');
 setup(task);ok(el('phase').textContent==='转换文档与附件' && timers.length,'刷新恢复任务');
 replies.push(response(null,401));await tick();
 ok(!timers.length && el('message').textContent.includes('登录已失效'),'认证失败停止轮询');
 document.getElementById('checks').textContent='passed';
})().catch(e=>document.getElementById('checks').textContent=e.stack);
'''.replace(
        'FIXTURE', json.dumps(fixture).replace('<', r'\u003c')
    ).replace(
        'SOURCE', json.dumps(script).replace('<', r'\u003c')
    )
    page = tmp_path / 'import-ui.html'
    page.write_text(
        '<!doctype html><meta charset="utf-8"><div id="fixture"></div><pre id="checks"></pre><script>'
        + checks
        + '</script>'
    )
    result = subprocess.run(
        [
            chrome,
            '--headless',
            '--disable-gpu',
            '--no-first-run',
            '--disable-background-networking',
            f'--user-data-dir={tmp_path / "profile"}',
            '--dump-dom',
            page.as_uri(),
        ],
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stderr
    found = re.search(r'<pre id="checks">(.*?)</pre>', result.stdout, re.S)
    assert found and html.unescape(found[1]) == 'passed', result.stdout


def test_repository_import_works_without_random_uuid(tmp_path):
    candidates = [os.environ.get('CHROME_BIN')] + list(
        (Path.home() / 'Library/Caches/ms-playwright').glob(
            'chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell'
        )
    )
    chrome = next(
        (str(p) for p in candidates if p and Path(p).is_file()), None
    )
    if not chrome:
        pytest.skip('需要 Chromium 验证仓库导入交互')
    script = Path('otterwiki/static/js/repository-sync.js').read_text()
    checks = r'''
Object.defineProperty(window.crypto, 'randomUUID', {value: undefined});
window.prompt=()=> 'IMPORT main';
let posted;
const task={id:'task-123',operation:'import',phase:'done',phase_label:'完成',status:'failed',error:'测试结束',status_url:'/status'};
window.fetch=async(url,options={})=>{
  if(options.method==='POST')posted=options.body;
  return {ok:true,json:async()=>task};
};
function ok(value,label){if(!value)throw new Error(label);}
(async()=>{
  (0,eval)(SOURCE);
  document.querySelector('.repository-task').click();
  await new Promise(resolve=>setTimeout(resolve,0));
  ok(posted,'HTTP 环境仍应提交首次导入请求');
  ok(posted.get('operation')==='import','应提交导入操作');
  ok(posted.get('confirmation')==='IMPORT main','应提交确认文本');
  ok(!posted.has('request_key'),'不支持 randomUUID 时由服务端生成标识');
  document.getElementById('checks').textContent='passed';
})().catch(e=>document.getElementById('checks').textContent=e.stack);
'''.replace(
        'SOURCE', json.dumps(script).replace('<', r'\u003c')
    )
    page = tmp_path / 'repository-import-ui.html'
    page.write_text(
        '<!doctype html><meta charset="utf-8">'
        '<meta name="csrf-token" content="token">'
        '<div class="repository-card" data-state="uninitialized" '
        'data-task-url="/tasks">'
        '<button class="repository-task" data-operation="import" '
        'data-space-slug="main">首次导入</button>'
        '<div class="repository-task-result"></div></div>'
        '<pre id="checks"></pre><script>' + checks + '</script>'
    )
    result = subprocess.run(
        [
            chrome,
            '--headless',
            '--disable-gpu',
            '--no-first-run',
            '--disable-background-networking',
            f'--user-data-dir={tmp_path / "profile"}',
            '--dump-dom',
            page.as_uri(),
        ],
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stderr
    found = re.search(r'<pre id="checks">(.*?)</pre>', result.stdout, re.S)
    assert found and html.unescape(found[1]) == 'passed', result.stdout
