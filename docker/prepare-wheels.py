# vim: set et ts=8 sts=4 sw=4 ai:
"""仅在联网准备容器内运行，下载并校验完整离线依赖集合。"""

from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib


def main():
    if sys.version_info[:2] != (3, 11):
        raise SystemExit('所选运行镜像不是 Python 3.11，请更换完整运行镜像。')
    with open('/src/pyproject.toml', 'rb') as source:
        config = tomllib.load(source)
    output = Path('/wheelhouse')
    output.mkdir(parents=True, exist_ok=True)
    requirements = output / 'requirements-offline.txt'
    packages = [
        'pip',
        *config['build-system']['requires'],
        *config['project']['dependencies'],
        *config['project']['optional-dependencies']['dev'],
    ]
    requirements.write_text('\n'.join(dict.fromkeys(packages)) + '\n')
    subprocess.run(
        [
            sys.executable,
            '-m',
            'pip',
            'wheel',
            '--wheel-dir',
            str(output),
            '-r',
            str(requirements),
        ],
        check=True,
    )
    # 再从下载目录离线解析一次，确保传递依赖和可安装的 wheel 都已齐全。
    with tempfile.TemporaryDirectory() as check_dir:
        subprocess.run(
            [
                sys.executable,
                '-m',
                'pip',
                'download',
                '--no-index',
                '--find-links',
                str(output),
                '--only-binary=:all:',
                '--dest',
                check_dir,
                '-r',
                str(requirements),
            ],
            check=True,
        )
    # 在干净虚拟环境里实际安装，避免运行镜像已有依赖掩盖缺包或冲突。
    with tempfile.TemporaryDirectory() as verify_dir:
        # 运行镜像不一定带 ensurepip；使用现有 pip 向新环境安装即可。
        subprocess.run(
            [sys.executable, '-m', 'venv', '--without-pip', verify_dir],
            check=True,
        )
        python = str(Path(verify_dir) / 'bin/python')
        subprocess.run(
            [
                sys.executable,
                '-m',
                'pip',
                '--python',
                python,
                'install',
                '--no-index',
                '--find-links',
                str(output),
                '-r',
                str(requirements),
            ],
            check=True,
        )
        subprocess.run([python, '-m', 'pip', 'check'], check=True)
        subprocess.run(
            [
                python,
                '-c',
                'from PIL import _imaging; from lxml import etree; '
                'import regex, yaml, sqlalchemy',
            ],
            check=True,
        )
    print('项目依赖、传递依赖、构建工具和测试工具已准备完成。')


if __name__ == '__main__':
    main()
