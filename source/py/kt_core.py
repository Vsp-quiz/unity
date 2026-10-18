"""kt_core - loi giai ma + trich xuat .unity3d (chay trong trinh duyet qua Pyodide, hoac CPython de kiem thu).

Thuat toan giai ma (KTResourceCrypto): 0x88 byte dau giu nguyen; phan con lai: hoan vi khoi roi XOR khoa 128 byte.
Sau khi giai ma -> UnityFS binh thuong -> UnityPy doc tat ca object.
"""
import base64, contextlib, csv, hashlib, io, json, os, re, struct, sys, traceback, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

HDR = 0x88
MAGIC_META = 0xfab11baf


class KTError(Exception):
    pass


# ------------------------------------------------------------------ khoa
def _default_keys():
    with open(os.path.join(HERE, 'keys.json')) as f:
        return [bytes.fromhex(k) for k in json.load(f)['keys']]


KEYS = _default_keys()


def parse_keys_text(txt):
    """Nhan JSON ({"keys":[...]} hoac [...]) hoac cac chuoi hex 256 ky tu bat ky -> list bytes(128)."""
    out = []
    for m in re.finditer(r'[0-9a-fA-F]{256}', txt):
        b = bytes.fromhex(m.group(0))
        if b not in out:
            out.append(b)
    return out


def keys_from_metadata(b):
    if len(b) < 24 or struct.unpack('<I', b[:4])[0] != MAGIC_META:
        raise KTError('không phải global-metadata.dat')
    off, size = struct.unpack('<ii', b[8:16])
    doff = struct.unpack('<i', b[16:20])[0]
    ks = []
    for i in range(size // 8):
        ln, o = struct.unpack_from('<II', b, off + i * 8)
        if ln == 256:
            t = b[doff + o:doff + o + ln]
            if re.fullmatch(rb'[0-9a-fA-F]{256}', t):
                ks.append(bytes.fromhex(t.decode()))
    if not ks:
        raise KTError('không tìm thấy khóa 256 ký tự hex nào trong metadata')
    return ks


def parse_keys_blob(b):
    """Noi dung file (JSON/TXT hoac global-metadata.dat) -> list hex(256 ky tu)."""
    b = bytes(b)
    ks = keys_from_metadata(b) if b[:4] == struct.pack('<I', MAGIC_META) else parse_keys_text(b.decode('utf-8', 'ignore'))
    return [k.hex() for k in ks]


ACTIVE = [('Khóa %d' % (i + 1), k) for i, k in enumerate(KEYS)]


def set_active_keys(items):
    """items: [(nhan, hex)] cac khoa dang duoc chon. Rong = khong dung khoa (chi doc file khong ma hoa)."""
    ACTIVE[:] = [(str(l), bytes.fromhex(h)) for l, h in items]
    return len(ACTIVE)


def all_keys():
    return list(ACTIVE)


# ------------------------------------------------------------------ thuat toan
def _swap(x, L):
    w = (L + 3) >> 2
    if w >= 2:
        a = x[1:w:2]; b = x[w + 1:2 * w:2]
        x[1:w:2] = b; x[w + 1:2 * w:2] = a
    w10 = L - 2 * w
    if w10 >= 1:
        base = 2 * w; m = w10 - w
        if m > 0:
            a = x[base:base + m:2]; b = x[base + w:base + w + m:2]
            x[base:base + m:2] = b; x[base + w:base + w + m:2] = a


def _xor(x, key):
    L = len(x)
    ks = (key * (L // len(key) + 1))[:L]
    return (int.from_bytes(x, 'little') ^ int.from_bytes(ks, 'little')).to_bytes(L, 'little')


def decrypt(data, key):
    if len(data) <= HDR:
        return bytes(data)
    x = bytearray(data[HDR:])
    _swap(x, len(x))
    return bytes(data[:HDR]) + _xor(bytes(x), key)


def encrypt(data, key):
    if len(data) <= HDR:
        return bytes(data)
    x = bytearray(_xor(bytes(data[HDR:]), key))
    _swap(x, len(x))
    return bytes(data[:HDR]) + bytes(x)


# ------------------------------------------------------------------ UnityPy
_UP = None


def unitypy():
    global _UP
    if _UP is None:
        sh = os.path.join(HERE, 'shims')
        if os.path.isdir(sh) and sh not in sys.path:
            sys.path.append(sh)  # chi dung khi thieu goi that
        import UnityPy
        _UP = UnityPy
    return _UP


def load_env(data):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return unitypy().load(bytes(data))


def is_bundle(data):
    return data[:7] == b'UnityFS' or data[:8] in (b'UnityWeb', b'UnityRaw')


def try_env(data):
    try:
        env = load_env(data)
        objs = list(env.objects)
        return env if objs else None
    except Exception:
        return None


def text_ok(data):
    s = data[:4096]
    if not s:
        return False
    if s.startswith((b'\xef\xbb\xbf', b'<?xml', b'{', b'[', b'PK', b'\x89PNG', b'\x1f\x8b')):
        return True
    printable = sum(1 for c in s if c in (9, 10, 13) or 32 <= c < 127 or c >= 0xc2)
    return printable / len(s) > 0.95


# ------------------------------------------------------------------ phien lam viec
def safe_name(s, fallback='unnamed'):
    s = re.sub(r'[\\:*?"<>|\x00-\x1f]+', '_', str(s or '')).strip(' .')
    return (s or fallback)[:120]


def safe_path(p):
    parts = [safe_name(x) for x in re.split(r'[\\/]+', str(p)) if x not in ('', '.', '..')]
    return '/'.join(parts)


class Source:
    def __init__(self, idx, name, plain, mode, env, note):
        self.idx, self.name, self.plain, self.mode, self.env, self.note = idx, name, plain, mode, env, note
        self.kind = 'bundle' if env is not None else 'file'
        self.version = ''
        self.platform = ''


class Session:
    def __init__(self):
        self.sources = []
        self.entries = []      # list[dict]
        self.by_id = {}
        self.objs = {}         # id -> ObjectReader
        self.log = []

    # ---- nap file
    def add_file(self, name, data):
        data = bytes(data)
        if data[:2] == b'PK' and name.lower().endswith('.zip'):
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    n = 0
                    for zi in z.infolist():
                        if zi.is_dir():
                            continue
                        self.add_file(name + '/' + zi.filename, z.read(zi))
                        n += 1
                    if n:
                        return
            except zipfile.BadZipFile:
                pass
        idx = len(self.sources)
        src = self._open(idx, name, data)
        self.sources.append(src)
        self._index(src)
        return src

    def _open(self, idx, name, data):
        cands = [(None, data)] + [(lb, decrypt(data, k)) for lb, k in all_keys()] if len(data) > HDR else [(None, data)]
        # 1) UnityFS (ma hoa hoac khong)
        if is_bundle(data):
            for label, c in cands:
                env = try_env(c)
                if env is not None:
                    return Source(idx, name, c, label, env, 'không mã hóa' if label is None else 'đã giải mã (%s)' % label)
            raise KTError('%s: giải mã thất bại (sai khóa/mode hoặc không phải bundle hợp lệ)' % name)
        # 2) van ban / nhi phan thuong
        for label, c in cands:
            if is_bundle(c):
                env = try_env(c)
                if env is not None:
                    return Source(idx, name, c, label, env, 'đã giải mã (%s)' % label)
            if label is None and text_ok(c):
                return Source(idx, name, c, None, None, 'văn bản/nhị phân thường')
            if label is not None and text_ok(c[HDR:]):
                return Source(idx, name, c, label, None, 'đã giải mã (%s)' % label)
        # 3) SerializedFile tran (.assets / level...)
        if len(data) > 64:
            for label, c in cands:
                env = try_env(c)
                if env is not None:
                    return Source(idx, name, c, label, env, 'không mã hóa' if label is None else 'đã giải mã (%s)' % label)
        return Source(idx, name, data, None, None, 'không nhận dạng được (giữ nguyên byte gốc)')

    # ---- chi muc
    def _index(self, src):
        if src.env is None:
            e = dict(id='%d:0' % src.idx, src=src.idx, pid=0, type='File', name=src.name.split('/')[-1],
                     size=len(src.plain), path='', ext=None)
            self.entries.append(e); self.by_id[e['id']] = e
            return
        env = src.env
        try:
            for f in env.files.values():
                v = getattr(f, 'unity_version', None) or getattr(f, 'version_engine', None)
                if v and not src.version:
                    src.version = str(v)
                t = getattr(f, 'target_platform', None) or getattr(f, 'build_target', None)
                if t is not None and not src.platform:
                    src.platform = getattr(t, 'name', str(t))
        except Exception:
            pass
        for o in env.objects:
            try:
                nm = o.peek_name() or ''
            except Exception:
                nm = ''
            try:
                path = o.container or ''
            except Exception:
                path = ''
            tname = o.type.name
            e = dict(id='%d:%d' % (src.idx, o.path_id), src=src.idx, pid=o.path_id, type=tname,
                     name=nm, size=o.byte_size, path=path, ext=None)
            if e['id'] in self.by_id:  # path_id trung giua cac serialized file
                e['id'] += '#%d' % len(self.entries)
            self.entries.append(e); self.by_id[e['id']] = e; self.objs[e['id']] = o

    # ---- thong tin
    def summary(self):
        types = {}
        for e in self.entries:
            t = types.setdefault(e['type'], [0, 0]); t[0] += 1; t[1] += e['size']
        return dict(
            sources=[dict(name=s.name, kind=s.kind, note=s.note, size=len(s.plain), version=s.version,
                          platform=s.platform, mode=s.mode) for s in self.sources],
            count=len(self.entries),
            types=[dict(type=k, count=v[0], bytes=v[1]) for k, v in sorted(types.items(), key=lambda kv: -kv[1][0])],
            entries=[[e['id'], e['type'], e['name'], e['size'], e['path'], e['src']] for e in self.entries])

    # ---- xuat
    def export(self, eid, raw_too=False):
        """-> list[(relpath, bytes, kind)]  (kind: text|image|json|audio|model|font|bin). Phan tu dau la ban xem truoc."""
        e = self.by_id[eid]
        src = self.sources[e['src']]
        if e['type'] == 'File':
            fn = safe_name(e['name'])
            return [(fn, src.plain, 'text' if text_ok(src.plain) else 'bin')]
        o = self.objs[eid]
        res = []
        try:
            res = _export_object(o, e)
        except Exception as ex:
            e['err'] = '%s: %s' % (type(ex).__name__, ex)
        if not res:
            res = _export_fallback(o, e)
        elif raw_too:
            try:
                res.append(('%s.bin' % _stem(e), o.get_raw_data(), 'bin'))
            except Exception:
                pass
        return res

    def decrypted_bundles(self):
        return [(s.name, s.plain) for s in self.sources if s.mode is not None]

    # ---- zip
    def build_zip(self, ids=None, raw_too=False, with_decrypted=True, progress=None):
        ids = list(ids) if ids is not None else [e['id'] for e in self.entries]
        buf = io.BytesIO()
        used = set(); errs = []; rows = []
        STORE = {'.png', '.ogg', '.jpg', '.zip', '.m4a', '.mp3'}
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED, compresslevel=3) as z:
            def put(rel, data):
                ext = os.path.splitext(rel)[1].lower()
                z.writestr(rel, data,
                           compress_type=zipfile.ZIP_STORED if ext in STORE else zipfile.ZIP_DEFLATED)
            n = len(ids)
            for i, eid in enumerate(ids):
                e = self.by_id.get(eid)
                if e is None:
                    continue
                if progress and (i % 20 == 0 or i == n - 1):
                    progress(i + 1, n, e['name'])
                try:
                    files = self.export(eid, raw_too)
                except Exception as ex:
                    errs.append('%s %s: %s' % (e['type'], e['name'], ex)); continue
                if e.get('err'):
                    errs.append('%s %s (path_id %s): %s' % (e['type'], e['name'], e['pid'], e['err']))
                for rel, data, kind in files:
                    rel = safe_path(rel)
                    base, ext = os.path.splitext(rel)
                    cand = rel
                    if cand.lower() in used:
                        cand = '%s__%s%s' % (base, e['pid'], ext)
                    k = 2
                    while cand.lower() in used:
                        cand = '%s__%s_%d%s' % (base, e['pid'], k, ext); k += 1
                    used.add(cand.lower())
                    put(cand, data)
                    rows.append([e['type'], e['name'], e['pid'], cand, len(data), self.sources[e['src']].name])
                files = None
            if with_decrypted:
                for name, plain in self.decrypted_bundles():
                    put('_decrypted/' + safe_path(name.split('/')[-1]), plain)
            sio = io.StringIO(); w = csv.writer(sio)
            w.writerow(['Loại', 'Tên', 'PathID', 'Đường dẫn', 'Bytes', 'Nguồn'])
            w.writerows(rows)
            put('_index.csv', ('﻿' + sio.getvalue()).encode('utf-8'))
            if errs:
                put('_errors.txt', '\n'.join(errs).encode('utf-8'))
        return buf.getvalue(), len(rows), len(errs)


# ------------------------------------------------------------------ xuat tung loai
def _stem(e):
    nm = safe_name(e['name'], 'obj') if e['name'] else 'obj'
    return '%s/%s' % (safe_name(e['type']), nm)


def _path_for(e, ext):
    """Uu tien duong dan container (Assets/...), neu khong thi Loai/Ten. Thay phan mo rong cu (.ogg, .bytes...)."""
    if e.get('path'):
        p = safe_path(e['path'])
        base, cur = os.path.splitext(p)
        if cur.lower() == ext.lower():
            return p
        if re.fullmatch(r'\.[A-Za-z0-9]{1,7}', cur):
            return base + ext
        return p + ext
    return '%s%s' % (_stem(e), ext)


def _sniff_ext(raw):
    h = raw[3:24] if raw[:3] == b'\xef\xbb\xbf' else raw[:24]
    s = h.lstrip()
    if s.startswith(b'<?xml') or s.startswith(b'<'):
        return '.xml'
    if s[:1] in (b'{', b'['):
        return '.json'
    if raw[:4] == b'\x89PNG':
        return '.png'
    if raw[:2] == b'PK':
        return '.zip'
    if raw[:4] in (b'\x1bLua', b'\x1bLuaS'):
        return '.luac'
    return '.txt' if _is_texty(raw) else '.bytes'


def _is_texty(raw):
    s = raw[:2048]
    if not s:
        return True
    if b'\x00' in s:
        return False
    try:
        s.decode('utf-8')
        return True
    except UnicodeDecodeError:
        return sum(1 for c in s if c in (9, 10, 13) or 32 <= c < 127) / len(s) > 0.95


def _textasset_raw(o):
    try:
        t = o.read()
        r = t.m_Script
        return r.encode('utf-8', 'surrogateescape') if isinstance(r, str) else bytes(r), t.m_Name
    except Exception:
        # du phong khi bundle khong co typetree: TextAsset = string m_Name + string m_Script
        raw = o.get_raw_data()
        en = '<' if getattr(o.assets_file, 'endian', '<') in ('<', 'little') else '>'
        n = struct.unpack_from(en + 'i', raw, 0)[0]
        name = raw[4:4 + n].decode('utf-8', 'replace')
        p = (4 + n + 3) & ~3
        m = struct.unpack_from(en + 'i', raw, p)[0]
        return bytes(raw[p + 4:p + 4 + m]), name


def _json_default(o):
    if isinstance(o, (bytes, bytearray, memoryview)):
        b = bytes(o)
        return {'$bytes': len(b), 'base64': base64.b64encode(b[:4096]).decode(), 'truncated': len(b) > 4096}
    if hasattr(o, 'tolist'):
        return o.tolist()
    return str(o)


def _typetree_json(o):
    tt = o.read_typetree()
    return json.dumps(tt, ensure_ascii=False, indent=1, default=_json_default).encode('utf-8')


def _png(im):
    b = io.BytesIO(); im.save(b, 'PNG'); return b.getvalue()


def _mesh_obj(mesh):
    import numpy as np
    from UnityPy.helpers.MeshHelper import MeshHandler
    h = MeshHandler(mesh); h.process()
    if not h.m_Vertices or h.m_VertexCount <= 0:
        return None
    v = np.asarray(h.m_Vertices, np.float32).reshape(len(h.m_Vertices), -1)[:, :3]
    tris = [t for sub in h.get_triangles() for t in sub]
    if not tris:
        return None
    f = np.asarray(tris, np.int64).reshape(-1, 3)
    f = f[(f < len(v)).all(1)]
    uv = None
    if h.m_UV0 is not None and len(h.m_UV0) == len(v):
        uv = np.asarray(h.m_UV0, np.float32).reshape(len(v), -1)[:, :2]
    out = io.StringIO()
    out.write('# xuat boi KT Web Decoder (Unity trai->OBJ phai: dao x)\no %s\n' % safe_name(mesh.m_Name))
    out.write(''.join('v %.6g %.6g %.6g\n' % (-x, y, z) for x, y, z in v))
    if uv is not None:
        out.write(''.join('vt %.6g %.6g\n' % (a, b) for a, b in uv))
        out.write(''.join('f %d/%d %d/%d %d/%d\n' % (c + 1, c + 1, b + 1, b + 1, a + 1, a + 1) for a, b, c in f))
    else:
        out.write(''.join('f %d %d %d\n' % (c + 1, b + 1, a + 1) for a, b, c in f))
    return out.getvalue().encode('utf-8')


def _audio(clip, e):
    name = safe_name(clip.m_Name or e['name'] or 'audio')
    try:
        samples = clip.samples
        if samples:
            res = []
            for fn, data in samples.items():
                res.append((_path_for(dict(e, name=os.path.splitext(fn)[0], path=''), os.path.splitext(fn)[1] or '.wav'),
                            bytes(data), 'audio'))
            return res
    except Exception:
        pass
    # du phong: du lieu am thanh goc (FSB...) de nguoi dung tu mo bang cong cu khac
    from UnityPy.helpers.ResourceReader import get_resource_data
    if clip.m_AudioData:
        d = bytes(clip.m_AudioData)
    elif clip.m_Resource:
        r = clip.m_Resource
        d = get_resource_data(r.m_Source, clip.object_reader.assets_file, r.m_Offset, r.m_Size)
    else:
        return []
    ext = {b'OggS': '.ogg', b'RIFF': '.wav', b'FSB5': '.fsb'}.get(d[:4], '.bin')
    if d[4:8] == b'ftyp':
        ext = '.m4a'
    kind = 'audio' if ext in ('.ogg', '.wav', '.m4a') else 'bin'
    return [(_path_for(dict(e, name=name), ext), d, kind)]


def _export_object(o, e):
    t = e['type']
    if t == 'TextAsset':
        raw, nm = _textasset_raw(o)
        if not e['name']:
            e['name'] = nm
        ext = _sniff_ext(raw)
        return [(_path_for(e, ext), raw, 'text' if _is_texty(raw) else 'bin')]
    if t in ('Texture2D', 'Sprite', 'Cubemap'):
        d = o.read()
        if not e['name']:
            e['name'] = d.m_Name
        im = d.image
        if im is None:
            raise ValueError('không giải mã được ảnh')
        return [(_path_for(e, '.png'), _png(im), 'image')]
    if t == 'AudioClip':
        d = o.read()
        if not e['name']:
            e['name'] = d.m_Name
        return _audio(d, e)
    if t == 'Mesh':
        d = o.read()
        if not e['name']:
            e['name'] = d.m_Name
        raw = _mesh_obj(d)
        return [(_path_for(e, '.obj'), raw, 'model')] if raw else []
    if t == 'Font':
        d = o.read()
        data = bytes(d.m_FontData or b'')
        if data:
            return [(_path_for(e, '.otf' if data[:4] == b'OTTO' else '.ttf'), data, 'font')]
        return []
    if t == 'Shader':
        d = o.read()
        txt = None
        try:
            txt = d.export()
        except Exception:
            pass
        if txt:
            return [(_path_for(e, '.shader'), txt.encode('utf-8') if isinstance(txt, str) else bytes(txt), 'text')]
        return []
    if t == 'AssetBundle' or True:
        return [(_path_for(e, '.json'), _typetree_json(o), 'json')]


def _export_fallback(o, e):
    """Neu khong xuat duoc theo loai: thu typetree JSON, cuoi cung la byte goc -> khong bao gio mat du lieu."""
    try:
        return [(_path_for(e, '.json'), _typetree_json(o), 'json')]
    except Exception as ex:
        e.setdefault('err', '%s: %s' % (type(ex).__name__, ex))
    try:
        return [(_path_for(e, '.bin'), bytes(o.get_raw_data()), 'bin')]
    except Exception as ex:
        e['err'] = '%s: %s' % (type(ex).__name__, ex)
        return []


# ------------------------------------------------------------------ API cho worker
SESSION = None


def reset():
    global SESSION
    SESSION = Session()


reset()


def add_file(name, path):
    """path: duong dan trong he thong file ao cua Pyodide. -> {ok, note|error}"""
    try:
        with open(path, 'rb') as f:
            data = f.read()
        n0 = len(SESSION.sources)
        SESSION.add_file(name, data)
        return json.dumps(dict(ok=True, added=len(SESSION.sources) - n0))
    except KTError as ex:
        return json.dumps(dict(ok=False, error=str(ex)))
    except Exception as ex:
        return json.dumps(dict(ok=False, error='%s: %s' % (type(ex).__name__, ex), tb=traceback.format_exc()))


def default_keys():
    return json.dumps([k.hex() for k in KEYS])


def parse_keys(path):
    try:
        with open(path, 'rb') as f:
            return json.dumps(dict(ok=True, keys=parse_keys_blob(f.read())))
    except Exception as ex:
        return json.dumps(dict(ok=False, error=str(ex)))


def set_keys(items_json):
    return set_active_keys(json.loads(items_json))


def summary():
    return json.dumps(SESSION.summary(), ensure_ascii=False)


def preview(eid):
    """-> (meta_json, bytes_preview|None). Chi lay file dau tien."""
    files = SESSION.export(eid)
    e = SESSION.by_id[eid]
    if not files:
        return json.dumps(dict(ok=False, error=e.get('err', 'không có dữ liệu'))), b''
    rel, data, kind = files[0]
    return json.dumps(dict(ok=True, rel=rel, kind=kind, size=len(data), err=e.get('err'))), data


def export_files(eid, raw_too=False):
    files = SESSION.export(eid, raw_too)
    return json.dumps([[r, k, len(d)] for r, d, k in files]), [d for _, d, _ in files]


def make_zip(ids_json, raw_too, with_decrypted, progress_cb):
    ids = json.loads(ids_json) if ids_json else None
    data, n, nerr = SESSION.build_zip(ids, bool(raw_too), bool(with_decrypted), progress_cb)
    return json.dumps(dict(files=n, errors=nerr)), data


def decrypted_bundle(src_idx):
    s = SESSION.sources[src_idx]
    return s.name.split('/')[-1], s.plain
