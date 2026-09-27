import argparse, hashlib
from pathlib import Path
from .storage_registry import upload_to_replicas

def main():
    p=argparse.ArgumentParser(); p.add_argument("--file",required=True); p.add_argument("--relative",required=True); p.add_argument("--sha256")
    a=p.parse_args(); src=Path(a.file)
    if a.sha256:
        got=hashlib.sha256(src.read_bytes()).hexdigest()
        if got.lower()!=a.sha256.lower(): raise SystemExit("Local archive SHA-256 mismatch before replication")
    results=upload_to_replicas(src,a.relative,a.sha256)
    failed=[r for r in results if r["status"]!="SYNCED"]
    for r in results: print("REPLICA",r["name"],r["status"])
    # Archive source must not be dropped while a configured replica failed.
    if failed: raise SystemExit("Remote archive replication failed: "+", ".join(r["name"] for r in failed))

if __name__=="__main__": main()
