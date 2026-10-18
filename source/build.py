#!/usr/bin/env python3
"""Dong goi: py/ -> zip -> base64 nhung vao src/template.html -> dist/index.html (1 file duy nhat)."""
import base64, io, os, zipfile
ROOT = os.path.dirname(os.path.abspath(__file__))
buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for r, ds, fs in os.walk(os.path.join(ROOT, 'py')):
        ds[:] = [d for d in ds if d != '__pycache__']
        for f in sorted(fs):
            if f.endswith(('.pyc', '.pyi')): continue
            p = os.path.join(r, f); z.write(p, os.path.relpath(p, os.path.join(ROOT, 'py')))
b64 = base64.b64encode(buf.getvalue()).decode()
html = open(os.path.join(ROOT, 'src/template.html'), encoding='utf-8').read()
worker = open(os.path.join(ROOT, 'src/worker.js'), encoding='utf-8').read()
assert '</script' not in worker
html = html.replace('/*__WORKER__*/', worker).replace('__PKG_B64__', b64)
os.makedirs(os.path.join(ROOT, 'dist'), exist_ok=True)
out = os.path.join(ROOT, 'dist/index.html')
open(out, 'w', encoding='utf-8').write(html)
print('dist/index.html', len(html) // 1024, 'KB (python bundle', len(buf.getvalue()) // 1024, 'KB)')
