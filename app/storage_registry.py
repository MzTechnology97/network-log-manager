import os, shutil, time
from pathlib import Path
from .database import app_db
from .remote_storage import decrypt_secret, integrity_test, browse, upload_file, download_file

def _cfg(row):
    return {"type":row["storage_type"],"host":row.get("host") or "","port":row.get("port") or "",
            "share":row.get("share_name") or "","path":row.get("base_path") or "",
            "folder":row.get("folder") or "","domain":row.get("domain_name") or "",
            "endpoint":row.get("endpoint_url") or "","bucket":row.get("bucket_name") or "",
            "region":row.get("region_name") or ""}

def _secret(row):
    try: return decrypt_secret(row.get("secret_encrypted") or "")
    except Exception: return {}

def list_targets(enabled_only=False):
    conn=app_db()
    try:
        with conn.cursor() as cur:
            sql="SELECT * FROM storage_targets"
            if enabled_only: sql+=" WHERE enabled=1"
            sql+=" ORDER BY role='PRIMARY' DESC,id"
            cur.execute(sql); return cur.fetchall()
    finally: conn.close()

def get_target(storage_id):
    conn=app_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM storage_targets WHERE id=%s",(storage_id,)); return cur.fetchone()
    finally: conn.close()

def primary_target():
    rows=[r for r in list_targets(True) if r["role"]=="PRIMARY"]
    return rows[0] if rows else None

def read_order():
    rows=list_targets(True); primary=[r for r in rows if r["role"]=="PRIMARY"]
    fallback=[r for r in rows if r["role"]!="PRIMARY" and r.get("read_fallback")]
    return primary+fallback

def health_check(row, deep=False):
    started=time.monotonic(); cfg=_cfg(row); secret=_secret(row)
    try:
        if deep:
            integrity_test(cfg,secret,row.get("folder") or "")
        else:
            browse(cfg,secret,row.get("folder") or "")
        latency=round((time.monotonic()-started)*1000,2)
        free=total=None
        if row["storage_type"]=="LOCAL":
            usage=shutil.disk_usage(Path(row["base_path"])); free=usage.free; total=usage.total
        elif row["storage_type"]=="SFTP":
            from .remote_storage import _sftp_transport
            t=_sftp_transport(cfg,secret)
            try:
                s=__import__("paramiko").SFTPClient.from_transport(t)
                st=s.statvfs(cfg.get("path") or "/"); free=st.f_bavail*st.f_frsize; total=st.f_blocks*st.f_frsize
            finally: t.close()
        status="HEALTHY"; error=None; failures=0
    except Exception as exc:
        latency=round((time.monotonic()-started)*1000,2); free=total=None
        failures=int(row.get("health_failures") or 0)+1
        status="OFFLINE" if failures>=3 else "DEGRADED"; error=str(exc)[:1000]
    conn=app_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""UPDATE storage_targets SET health_status=%s,last_health_at=NOW(3),
              last_success_at=IF(%s='HEALTHY',NOW(3),last_success_at),last_error=%s,latency_ms=%s,
              free_bytes=%s,total_bytes=%s,health_failures=%s WHERE id=%s""",
              (status,status,error,latency,free,total,failures,row["id"])); conn.commit()
    finally: conn.close()
    return {"id":row["id"],"name":row["name"],"status":status,"latency_ms":latency,
            "error":error,"free_bytes":free,"total_bytes":total}

def health_check_all(deep=False):
    return [health_check(r,deep=deep) for r in list_targets(True)]

def upload_to_replicas(local_path,relative_path,sha256=None):
    results=[]
    for row in list_targets(True):
        if row["storage_type"]=="LOCAL": continue
        conn=app_db()
        try:
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO storage_replication_state(storage_id,relative_path,sha256,status)
                  VALUES(%s,%s,%s,'PENDING') ON DUPLICATE KEY UPDATE sha256=VALUES(sha256),status='PENDING'""",
                  (row["id"],relative_path,sha256)); conn.commit()
        finally: conn.close()
        try:
            upload_file(_cfg(row),_secret(row),local_path,relative_path)
            status="SYNCED"; error=None
        except Exception as exc:
            status="FAILED"; error=str(exc)[:1000]
        conn=app_db()
        try:
            with conn.cursor() as cur:
                cur.execute("""UPDATE storage_replication_state SET status=%s,attempts=attempts+1,
                  last_attempt_at=NOW(3),synced_at=IF(%s='SYNCED',NOW(3),synced_at),last_error=%s
                  WHERE storage_id=%s AND relative_path=%s""",(status,status,error,row["id"],relative_path)); conn.commit()
        finally: conn.close()
        results.append({"storage_id":row["id"],"name":row["name"],"status":status,"error":error})
    return results


def materialize_from_fallback(relative_path, local_path, expected_sha256=None):
    import hashlib
    errors=[]
    for row in read_order():
        try:
            if row["storage_type"]=="LOCAL":
                candidate=Path(row["base_path"])/relative_path
                if candidate.is_file():
                    if Path(local_path).resolve()!=candidate.resolve():
                        Path(local_path).parent.mkdir(parents=True,exist_ok=True)
                        import shutil; shutil.copy2(candidate,local_path)
                    return {"storage_id":row["id"],"name":row["name"]}
                continue
            download_file(_cfg(row),_secret(row),relative_path,local_path)
            if expected_sha256:
                h=hashlib.sha256()
                with Path(local_path).open("rb") as fh:
                    for chunk in iter(lambda:fh.read(8*1024*1024),b""): h.update(chunk)
                if h.hexdigest().lower()!=expected_sha256.lower():
                    Path(local_path).unlink(missing_ok=True); raise RuntimeError("SHA-256 mismatch")
            return {"storage_id":row["id"],"name":row["name"]}
        except Exception as exc:
            errors.append(row["name"]+": "+str(exc))
    raise FileNotFoundError("Archive unavailable on all fallback storages: "+"; ".join(errors))


def resolve_archive_source(relative_path, local_catalog_path, expected_sha256=None):
    """Resolve cache input in PRIMARY -> fallback order, verifying SHA-256."""
    import hashlib
    local_catalog_path=Path(local_catalog_path)
    errors=[]
    for row in read_order():
        try:
            if row["storage_type"]=="LOCAL":
                candidate=Path(row["base_path"])/relative_path
                if not candidate.is_file(): raise FileNotFoundError(str(candidate))
            else:
                candidate=Path("/var/cache/netlog-manager/sources")/str(row["id"])/relative_path
                candidate.parent.mkdir(parents=True,exist_ok=True)
                download_file(_cfg(row),_secret(row),relative_path,candidate)
            if expected_sha256:
                h=hashlib.sha256()
                with candidate.open("rb") as fh:
                    for chunk in iter(lambda:fh.read(8*1024*1024),b""): h.update(chunk)
                if h.hexdigest().lower()!=expected_sha256.lower():
                    if row["storage_type"]!="LOCAL": candidate.unlink(missing_ok=True)
                    raise RuntimeError("SHA-256 mismatch")
            return candidate,row
        except Exception as exc:
            errors.append(row["name"]+": "+str(exc))
    # Backward-compatible local catalog path is a final safety fallback.
    if local_catalog_path.is_file(): return local_catalog_path,{"id":None,"name":"catalog-local"}
    raise FileNotFoundError("Archive unavailable on primary and fallbacks: "+"; ".join(errors))
