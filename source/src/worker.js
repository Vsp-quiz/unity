/* Worker: chay Pyodide + UnityPy + kt_core. Khong cham vao giao dien. */
'use strict';
let pyodide = null, kt = null;
const post = (m, t) => self.postMessage(m, t || []);
const status = (text, pct) => post({ type: 'status', text, pct });

function b64ToU8(b64) {
  const bin = atob(b64), u = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i);
  return u;
}

async function init(msg) {
  let lastErr = null; const errs = [];
  for (const base of msg.bases) {
    try {
      status('Đang tải trình chạy Python (WebAssembly)…', 5);
      const mod = await import(base + 'pyodide.mjs');
      pyodide = await mod.loadPyodide({ indexURL: base });
      lastErr = null; post({ type: 'base', base });
      break;
    } catch (e) { lastErr = e; errs.push(base + ' → ' + (e && e.message)); pyodide = null; post({ type: 'status', text: 'Thử nguồn khác… (' + base + ': ' + (e && e.message) + ')', pct: 5 }); }
  }
  if (!pyodide) throw new Error('Không tải được Pyodide (kiểm tra kết nối mạng): ' + errs.join(' ; '));
  status('Đang tải thư viện (numpy, Pillow, lz4, texture2ddecoder…)', 30);
  await pyodide.loadPackage(msg.packages);
  status('Đang nạp bộ giải mã UnityPy…', 80);
  pyodide.unpackArchive(b64ToU8(msg.pkg), 'zip', { extractDir: '/kt' });
  pyodide.runPython('import sys; sys.path.insert(0, "/kt")');
  kt = pyodide.pyimport('kt_core');
  pyodide.FS.mkdirTree('/in');
  status('Sẵn sàng', 100);
  post({ type: 'ready', defaults: JSON.parse(kt.default_keys()) });
}

function toJs(p) { const r = p.toJs ? p.toJs() : p; if (p.destroy) p.destroy(); return r; }

async function addFiles(msg) {
  kt.set_keys(JSON.stringify(msg.keys || []));
  const results = [];
  for (let i = 0; i < msg.files.length; i++) {
    const f = msg.files[i];
    status(`Đang giải mã ${f.name} (${i + 1}/${msg.files.length})…`, Math.round(100 * i / msg.files.length));
    await new Promise(r => setTimeout(r, 0));
    const path = '/in/f' + i;
    pyodide.FS.writeFile(path, new Uint8Array(f.buf));
    let r;
    try { r = JSON.parse(kt.add_file(f.name, path)); }
    catch (e) { r = { ok: false, error: String(e.message || e).split('\n').slice(-2).join(' ') }; }
    try { pyodide.FS.unlink(path); } catch (e) {}
    r.name = f.name; results.push(r);
  }
  const summary = JSON.parse(kt.summary());
  post({ type: 'loaded', results, summary });
}

function parseKeys(msg) {
  pyodide.FS.writeFile('/in/k', new Uint8Array(msg.buf));
  const r = JSON.parse(kt.parse_keys('/in/k'));
  try { pyodide.FS.unlink('/in/k'); } catch (e) {}
  post({ type: 'parsedkeys', req: msg.req, r, name: msg.name });
}

function preview(msg) {
  const r = toJs(kt.preview(msg.id));
  const meta = JSON.parse(r[0]); const data = r[1];
  post({ type: 'preview', id: msg.id, req: msg.req, meta, data }, data && data.buffer ? [data.buffer] : []);
}

function exportOne(msg) {
  const r = toJs(kt.export_files(msg.id, !!msg.raw));
  const meta = JSON.parse(r[0]); const files = r[1];
  const out = meta.map((m, i) => ({ rel: m[0], kind: m[1], data: files[i] }));
  post({ type: 'exported', req: msg.req, files: out }, out.map(o => o.data.buffer));
}

function makeZip(msg) {
  const cb = (i, n, name) => status(`Đang trích xuất ${i}/${n}: ${name}`, Math.round(100 * i / n));
  const r = toJs(kt.make_zip(msg.ids ? JSON.stringify(msg.ids) : '', !!msg.raw, !!msg.dec, cb));
  const meta = JSON.parse(r[0]); const data = r[1];
  post({ type: 'zip', req: msg.req, meta, data }, [data.buffer]);
}

function decrypted(msg) {
  const r = toJs(kt.decrypted_bundle(msg.src));
  post({ type: 'decrypted', req: msg.req, name: r[0], data: r[1] }, [r[1].buffer]);
}

self.onmessage = async (ev) => {
  const m = ev.data;
  try {
    if (m.type === 'init') await init(m);
    else if (m.type === 'add') await addFiles(m);
    else if (m.type === 'preview') preview(m);
    else if (m.type === 'parsekeys') parseKeys(m);
    else if (m.type === 'export') exportOne(m);
    else if (m.type === 'zip') makeZip(m);
    else if (m.type === 'decrypted') decrypted(m);
    else if (m.type === 'reset') { kt.reset(); post({ type: 'reset' }); }
  } catch (e) {
    post({ type: 'error', req: m.req, op: m.type, message: String((e && e.message) || e).split('\n').slice(-3).join(' | ') });
  }
};
