"""Deployment preflight: imports + a real Streamlit session, without a browser.

Run with the same Python and requirements as deployment:
    python check_startup.py
"""
import ast
import importlib
from importlib import metadata
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
REQUIRED_DISTRIBUTIONS = {'streamlit', 'pandas', 'openpyxl', 'xlrd', 'tzdata', 'websockets'}
CRITICAL_IMPORTS = {
    'stock_tax.cgt': ('CalculationError', 'Transaction', 'Transfer', 'calculate_ledger', 'summarize'),
    'stock_tax.importers': ('BrokerImporter', 'load_rates', 'load_accounts', 'load_transfer_reviews',
                            'CONFIRMED', 'REVIEW', 'BLOCKED', 'INVALID'),
}


def check_imports(root=ROOT):
    """Validate the tracked internal API, dependency manifest and import graph."""
    manifest = root / 'requirements.txt'
    entries = {re.split(r'[<>=!~\[;\s]', line.strip(), maxsplit=1)[0].lower()
               for line in manifest.read_text(encoding='utf-8-sig').splitlines()
               if line.strip() and not line.lstrip().startswith('#')}
    missing = REQUIRED_DISTRIBUTIONS - entries
    if missing:
        raise RuntimeError(f'requirements.txt is missing dependencies: {sorted(missing)}')
    versions = {name: metadata.version(name) for name in sorted(REQUIRED_DISTRIBUTIONS)}
    for name in sorted(REQUIRED_DISTRIBUTIONS):
        importlib.import_module(name)
    graph = {}
    for path in (root / 'stock_tax').glob('*.py'):
        name = 'stock_tax' if path.stem == '__init__' else f'stock_tax.{path.stem}'
        graph[name] = []
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))):
            if isinstance(node, ast.ImportFrom):
                dependency = f'stock_tax.{node.module}' if node.level else node.module
                if dependency and dependency.startswith('stock_tax.'):
                    graph[name].append(dependency)
    visited = set()

    def visit(name, chain):
        if name in chain:
            raise RuntimeError(f'Circular internal import: {" -> ".join(chain + [name])}')
        if name not in graph:
            raise RuntimeError(f'Missing internal module: {name}')
        if name not in visited:
            for dependency in graph[name]:
                visit(dependency, chain + [name])
            visited.add(name)

    for name in graph:
        visit(name, [])
    imports = {name: set(symbols) for name, symbols in CRITICAL_IMPORTS.items()}
    for node in ast.walk(ast.parse((root / 'app.py').read_text(encoding='utf-8-sig'))):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module in ('cgt', 'importers'):
                raise RuntimeError(f'app.py uses a generic internal module: {node.module}')
            if node.module.startswith('stock_tax.'):
                imports.setdefault(node.module, set()).update(alias.name for alias in node.names)
    for name, symbols in imports.items():
        module = importlib.import_module(name)
        expected = root / (name.replace('.', '/') + '.py')
        if Path(module.__file__).resolve() != expected.resolve():
            raise RuntimeError(f'Wrong module resolved: {name}: {module.__file__}')
        for symbol in symbols:
            if not hasattr(module, symbol):
                raise RuntimeError(f'Missing internal symbol: {name}.{symbol}')
    return versions


def check_server(root=ROOT, timeout=45):
    """Execute app.py over the real server protocol, not just its health URL.

    A healthy HTTP process alone does not mean the app script imports or runs.
    We request a session and inspect all rendered messages until script finish.
    """
    from streamlit.proto.BackMsg_pb2 import BackMsg
    from streamlit.proto.ForwardMsg_pb2 import ForwardMsg
    from websockets.sync.client import connect

    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    command = [sys.executable, '-m', 'streamlit', 'run', str(root / 'app.py'),
               '--server.headless=true', '--browser.gatherUsageStats=false',
               '--server.address=127.0.0.1', f'--server.port={port}']
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
    with tempfile.TemporaryFile(mode='w+b') as log:
        process = subprocess.Popen(command, cwd=root, stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=flags)
        try:
            deadline = time.monotonic() + timeout
            while True:
                if process.poll() is not None:
                    raise RuntimeError('Streamlit exited before becoming healthy')
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/_stcore/health', timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    raise RuntimeError('Streamlit health check timed out')
                time.sleep(0.1)
            elements, headings, labels = [], [], []
            # No Origin/cookie is needed for an anonymous loopback session; no
            # security settings or exception visibility settings are disabled.
            with connect(f'ws://127.0.0.1:{port}/_stcore/stream', subprotocols=['streamlit'],
                         open_timeout=10, max_size=16 * 1024 * 1024) as connection:
                request = BackMsg()
                request.rerun_script.query_string = ''
                connection.send(request.SerializeToString())
                while time.monotonic() < deadline:
                    message = ForwardMsg()
                    message.ParseFromString(connection.recv(timeout=max(0.1, deadline - time.monotonic())))
                    kind = message.WhichOneof('type')
                    if kind == 'delta' and message.delta.HasField('new_element'):
                        element = message.delta.new_element
                        element_type = element.WhichOneof('type')
                        elements.append(element_type)
                        if element_type == 'exception':
                            raise RuntimeError(f'Streamlit script exception: {element.exception}')
                        if element_type == 'heading':
                            headings.append(element.heading.body)
                        if element_type == 'button':
                            labels.append(element.button.label)
                    if kind == 'script_finished':
                        if message.script_finished != ForwardMsg.FINISHED_SUCCESSFULLY:
                            raise RuntimeError(f'Streamlit script did not finish successfully: {message.script_finished}')
                        break
                else:
                    raise RuntimeError('Streamlit session initialization timed out')
            if elements.count('file_uploader') != 5 or elements.count('selectbox') != 2:
                raise RuntimeError(f'Missing sidebar inputs: {elements}')
            if not any('CGT' in heading for heading in headings) or '🚀 合并并计算' not in labels:
                raise RuntimeError('Missing application title or calculation button')
            return {name: elements.count(name) for name in sorted(set(elements))}
        except Exception as exc:
            process.terminate()
            process.wait(timeout=10)
            log.seek(0)
            raise RuntimeError(f'{exc}\nServer log:\n{log.read().decode("utf-8", errors="replace")}') from exc
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == '__main__':
    print(json.dumps({'python': sys.version, 'platform': sys.platform,
                      'dependencies': check_imports(), 'rendered_elements': check_server()},
                     ensure_ascii=False, indent=2))
