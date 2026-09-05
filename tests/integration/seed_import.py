"""测试容器专用：初始化空数据卷与临时管理员。"""

from datetime import datetime, UTC
from pathlib import Path
from otterwiki.server import app, db
from otterwiki.migrations import run_migrations
from otterwiki.auth import SimpleAuth, generate_password_hash

run_migrations()
with app.app_context():
    db.session.add(
        SimpleAuth.User(
            name='测试管理员',
            email='gateway@example.org',
            password_hash=generate_password_hash('gateway-test-password'),
            is_admin=True,
            is_approved=True,
            email_confirmed=True,
            first_seen=datetime.now(UTC),
            last_seen=datetime.now(UTC),
        )
    )
    db.session.commit()
source = Path('/tmp/gateway-source')
(source / 'docs').mkdir(parents=True)
(source / 'docs/index.md').write_text('# 网关慢任务验收\n', encoding='utf-8')
(source / '.portal.yml').write_text('mappings: []\n')
(source / '.idp').mkdir()
(source / '.idp/.model.yaml').write_text('[]\n')
