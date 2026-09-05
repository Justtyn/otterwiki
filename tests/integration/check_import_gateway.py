#!/usr/bin/env python
# vim: set et ts=8 sts=4 sw=4 ai:
"""对隔离测试容器上传 ZIP，经 Nginx→uWSGI 验证 65 秒后台任务。"""
import io
import json
import re
import sys
import time
import uuid
import zipfile
from http.cookiejar import CookieJar
from urllib.parse import urlencode
from urllib.request import (
    build_opener,
    HTTPCookieProcessor,
    ProxyHandler,
    Request,
)

base = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:18089'
client = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))


def request(path, data=None, headers=None):
    return client.open(
        Request(base + path, data=data, headers=headers or {}), timeout=10
    )


def token(page):
    return re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page).group(1)


page = request('/-/login').read().decode()
request(
    '/-/login',
    urlencode(
        dict(
            email='gateway@example.org',
            password='gateway-test-password',
            csrf_token=token(page),
        )
    ).encode(),
).close()
page = request('/-/admin/document_import').read().decode()
archive = io.BytesIO()
with zipfile.ZipFile(archive, 'w') as z:
    z.writestr('APStack/docs/index.md', '# 网关慢任务验收\n')
    z.writestr('APStack/.portal.yml', 'mappings: []\n')
    z.writestr('APStack/.idp/.model.yaml', '[]\n')
boundary = uuid.uuid4().hex
parts = []
for name, value in dict(
    csrf_token=token(page),
    confirmation='RESET APSTACK',
    request_key=uuid.uuid4().hex,
).items():
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
    )
parts.append(
    f'--{boundary}\r\nContent-Disposition: form-data; name="archive"; filename="source.zip"\r\nContent-Type: application/zip\r\n\r\n'.encode()
    + archive.getvalue()
    + f'\r\n--{boundary}--\r\n'.encode()
)
start = time.monotonic()
r = request(
    '/-/admin/document_import',
    b''.join(parts),
    {'Content-Type': f'multipart/form-data; boundary={boundary}'},
)
assert r.status == 202, r.status
accepted = time.monotonic() - start
assert accepted < 5, accepted
state = json.load(r)
polls = []
while state['status'] in ('queued', 'running'):
    assert time.monotonic() - start < 100, state
    time.sleep(2)
    before = time.monotonic()
    r = request(state['status_url'])
    assert r.status == 200 and r.headers['Cache-Control'] == 'no-store'
    state = json.load(r)
    polls.append(time.monotonic() - before)
    print(
        json.dumps(
            {
                'elapsed': round(time.monotonic() - start, 1),
                'status': state['status'],
                'phase': state['phase'],
                'query_seconds': round(polls[-1], 3),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
assert state['status'] == 'succeeded', state
assert state['elapsed_seconds'] >= 65, state
assert len(polls) >= 30 and max(polls) < 5
assert state['result']['pages'] == 1, state
assert request(state['document_url']).status == 200
print(
    json.dumps(
        {
            'accepted_seconds': accepted,
            'queries': len(polls),
            'max_query_seconds': max(polls),
            'result': state,
        },
        ensure_ascii=False,
    ),
    flush=True,
)
