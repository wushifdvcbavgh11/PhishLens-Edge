"""Offline portable-build validation; --models also runs real visual inference."""
import argparse
import base64
import importlib
import json
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--models', action='store_true')
    parser.add_argument('--integrity', action='store_true')
    args = parser.parse_args()
    assert (ROOT / 'portable.json').is_file(), 'Run this from the portable package'
    assert Path(sys.executable).resolve().is_relative_to(ROOT / 'runtime')
    assert sys.flags.isolated, 'Runtime must ignore external Python environments'
    for entry in sys.path:
        assert Path(entry).resolve().is_relative_to(ROOT), 'External import path: ' + entry
    for name in ('fastapi', 'uvicorn', 'numpy', 'PIL', 'torch', 'torchvision', 'cv2', 'detectron2._C'):
        module = importlib.import_module(name)
        assert Path(module.__file__).resolve().is_relative_to(ROOT), name
        print('[OK] ' + name, flush=True)
    if args.integrity:
        import hashlib
        manifest = json.loads((ROOT / 'integrity.json').read_text(encoding='utf-8'))
        for relative, expected in manifest['files'].items():
            path = (ROOT / relative).resolve()
            assert path.is_relative_to(ROOT), 'Invalid manifest path'
            h = hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
                    h.update(chunk)
            assert h.hexdigest() == expected, 'Changed/missing file: ' + relative
        print('[OK] file integrity', flush=True)
    if args.models:
        # Any attempted network access fails the check, including DNS and downloads.
        attempts = []
        def deny(*a, **k):
            attempts.append('network attempted')
            raise RuntimeError('Network is disabled during offline validation')
        socket.socket.connect = deny
        socket.create_connection = deny
        socket.getaddrinfo = deny
        import app
        app._record_event = lambda *a: None
        image = ROOT / 'testset/fake/fake_alipay.png'
        request = app.AnalyzeRequest(url='http://127.0.0.1:8001/fake_alipay.html',
            screenshot_base64=base64.b64encode(image.read_bytes()).decode(),
            dom_signals={'page_domain': '127.0.0.1', 'has_password_input': True})
        result = app.analyze(request)
        assert result['analysis_status'] == 'completed', result.get('visual_error')
        assert not attempts, 'Inference attempted network access'
        assert app.MODELS_READY
        print('[OK] offline real-model inference: ' + json.dumps({k: result[k] for k in
            ('decision', 'brand', 'total_time_s', 'policy_version')}, ensure_ascii=True), flush=True)
    print('Portable environment checks passed. A separate computer still needs acceptance testing.')


if __name__ == '__main__':
    main()
