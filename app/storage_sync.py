import argparse, hashlib
from pathlib import Path
from .database import app_db
from .remote_storage import decrypt_secret, upload_file

def load_storage():
    conn=app_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT setting_key,setting_value FROM settings WHERE setting_key LIKE 'external_storage_%'")
            s={r["setting_key"]:r["setting_value"] or "" for r in cur.fetchall()}
    finally: conn.close()
    if s.get("external_storage_enabled")!="1" or s.get("external_storage_test_status")!="OK":
        return None,None
    cfg={"type":s.get("external_storage_type","LOCAL"),"host":s.get("external_storage_host",""),"port":s.get("external_storage_port",""),"share":s.get("external_storage_share",""),"path":s.get("external_storage_path",""),"folder":s.get("external_storage_folder",""),"domain":s.get("external_storage_domain","")}
    return cfg,decrypt_secret(s.get("external_storage_secret",""))

def main():
    p=argparse.ArgumentParser(); p.add_argument("--file",required=True); p.add_argument("--relative",required=True); p.add_argument("--sha256")
    a=p.parse_args(); cfg,secret=load_storage()
    if not cfg or cfg["type"].upper()=="LOCAL": return
    src=Path(a.file)
    if a.sha256:
        got=hashlib.sha256(src.read_bytes()).hexdigest()
        if got.lower()!=a.sha256.lower(): raise SystemExit("Local archive SHA-256 mismatch before remote upload")
    upload_file(cfg,secret,src,a.relative)
    print("REMOTE_UPLOAD_OK",a.relative)

if __name__=="__main__": main()
