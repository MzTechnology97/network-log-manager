import base64, hashlib, json, os, posixpath, shlex, subprocess, tempfile
from pathlib import Path
from cryptography.fernet import Fernet
import paramiko
from .config import ENV

def _fernet():
    raw=(ENV.get("SECRET_KEY") or "").encode()
    key=base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
    return Fernet(key)

def encrypt_secret(data):
    return _fernet().encrypt(json.dumps(data).encode()).decode()

def decrypt_secret(value):
    if not value: return {}
    return json.loads(_fernet().decrypt(value.encode()).decode())

def _safe_folder(folder):
    folder=(folder or "").strip().replace("\\","/")
    if ".." in folder.split("/") or any(ch in folder for ch in ['"',";","\\n","\\r"]): raise ValueError("Invalid folder")
    return folder.strip("/")

def _smb_cmd(cfg, secret, command):
    host=cfg["host"].strip(); share=cfg["share"].strip().strip("/\\\\")
    user=secret.get("username",""); password=secret.get("password","")
    domain=cfg.get("domain","").strip()
    with tempfile.NamedTemporaryFile("w",delete=False) as auth:
        auth.write("username = "+user+chr(10)+"password = "+password+chr(10))
        if domain: auth.write("domain = "+domain+chr(10))
        auth_path=auth.name
    os.chmod(auth_path,0o600)
    try:
        args=["smbclient",f"//{host}/{share}","-A",auth_path,"-c",command]
        p=subprocess.run(args,capture_output=True,text=True,timeout=20)
    finally:
        Path(auth_path).unlink(missing_ok=True)
    if p.returncode: raise RuntimeError((p.stderr or p.stdout).strip()[-500:])
    return p.stdout

def browse(cfg, secret, folder=""):
    kind=cfg["type"].upper(); folder=_safe_folder(folder)
    if kind=="LOCAL":
        root=Path(cfg["path"]).resolve(); target=(root/folder).resolve()
        if root not in target.parents and target!=root: raise ValueError("Invalid folder")
        return sorted([p.name for p in target.iterdir() if p.is_dir()])
    if kind=="SMB":
        cmd=(f'cd "{folder}"; ' if folder else "")+"ls"
        out=_smb_cmd(cfg,secret,cmd); result=[]
        for line in out.splitlines():
            parts=line.split()
            if len(parts)>=2 and "D" in parts[1] and parts[0] not in (".",".."): result.append(parts[0])
        return sorted(set(result))
    if kind=="SFTP":
        t=paramiko.Transport((cfg["host"],int(cfg.get("port") or 22)))
        try:
            t.connect(username=secret["username"],password=secret["password"])
            s=paramiko.SFTPClient.from_transport(t); root=cfg.get("path") or "/"; target=posixpath.join(root,folder)
            return sorted([a.filename for a in s.listdir_attr(target) if (a.st_mode & 0o170000)==0o040000])
        finally: t.close()
    raise ValueError("Browsing is supported for LOCAL, SMB and SFTP")

def integrity_test(cfg, secret, folder=""):
    folder=_safe_folder(folder); payload=os.urandom(4096); digest=hashlib.sha256(payload).hexdigest()
    name=".netlog-storage-test-"+os.urandom(6).hex()
    kind=cfg["type"].upper()
    if kind=="LOCAL":
        root=Path(cfg["path"]).resolve(); target=(root/folder).resolve(); target.mkdir(parents=True,exist_ok=True)
        p=target/name; p.write_bytes(payload); read=p.read_bytes(); p.unlink()
    elif kind=="SMB":
        with tempfile.NamedTemporaryFile() as tmp:
            tmp.write(payload); tmp.flush()
            remote=posixpath.join(folder,name) if folder else name
            _smb_cmd(cfg,secret,f'put "{tmp.name}" "{remote}"; get "{remote}" "{tmp.name}.read"; del "{remote}"')
            read=Path(tmp.name+".read").read_bytes(); Path(tmp.name+".read").unlink(missing_ok=True)
    elif kind=="SFTP":
        t=paramiko.Transport((cfg["host"],int(cfg.get("port") or 22)))
        try:
            t.connect(username=secret["username"],password=secret["password"]); s=paramiko.SFTPClient.from_transport(t)
            remote=posixpath.join(cfg.get("path") or "/",folder,name)
            with s.open(remote,"wb") as f: f.write(payload)
            with s.open(remote,"rb") as f: read=f.read()
            s.remove(remote)
        finally: t.close()
    else: raise ValueError("Integrity test is supported for LOCAL, SMB and SFTP")
    got=hashlib.sha256(read).hexdigest()
    if got!=digest: raise RuntimeError("Storage integrity verification failed")
    return {"ok":True,"sha256":digest,"bytes":len(payload)}


def upload_file(cfg, secret, local_path, relative_path):
    src=Path(local_path); rel=_safe_folder(relative_path)
    if not src.is_file(): raise FileNotFoundError(str(src))
    kind=cfg["type"].upper()
    if kind=="LOCAL":
        root=Path(cfg["path"]).resolve(); dst=(root/rel).resolve()
        if src.resolve()!=dst:
            dst.parent.mkdir(parents=True,exist_ok=True)
            import shutil; shutil.copy2(src,dst)
        return
    if kind=="SMB":
        parent=posixpath.dirname(posixpath.join(_safe_folder(cfg.get("folder","")),rel))
        current=""
        for part in [p for p in parent.split("/") if p]:
            current=posixpath.join(current,part); 
            try: _smb_cmd(cfg,secret,f'mkdir "{current}"')
            except RuntimeError: pass
        remote=posixpath.join(_safe_folder(cfg.get("folder","")),rel)
        _smb_cmd(cfg,secret,f'put "{src}" "{remote}"')
        return
    if kind=="SFTP":
        t=paramiko.Transport((cfg["host"],int(cfg.get("port") or 22)))
        try:
            t.connect(username=secret["username"],password=secret["password"]); s=paramiko.SFTPClient.from_transport(t)
            remote=posixpath.join(cfg.get("path") or "/",_safe_folder(cfg.get("folder","")),rel)
            parent=posixpath.dirname(remote); current=""
            for part in parent.split("/"):
                if not part: continue
                current+="/"+part
                try: s.mkdir(current)
                except OSError: pass
            s.put(str(src),remote)
        finally: t.close()
        return
    raise ValueError("Unsupported remote storage type")
