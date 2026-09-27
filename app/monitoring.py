import json, os, shutil, smtplib, ssl, urllib.request
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from .database import app_db, syslog_db

DEFAULTS={'retention_days':'1825','archive_after_days':'365','storage_warning_percent':'80','storage_critical_percent':'90','ingestion_stale_minutes':'5','alert_repeat_minutes':'60','external_storage_enabled':'0','external_storage_type':'LOCAL','external_storage_path':'/archive/mikrotik'}

def settings():
    values=dict(DEFAULTS); conn=app_db()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT setting_key,setting_value FROM settings')
            values.update({r['setting_key']:r['setting_value'] or '' for r in cur.fetchall()})
    finally: conn.close()
    return values

def _post(url,payload):
    req=urllib.request.Request(url,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=10) as response: return response.status

def send_channel(channel,title,message):
    cfg=json.loads(channel['configuration_json'] or '{}'); kind=channel['channel_type']
    if kind=='WEBHOOK': return _post(cfg['url'],{'title':title,'message':message})
    if kind=='SLACK': return _post(cfg['url'],{'text':'*'+title+'*\n'+message})
    if kind=='DISCORD': return _post(cfg['url'],{'content':'**'+title+'**\n'+message})
    if kind=='TELEGRAM': return _post('https://api.telegram.org/bot'+cfg['bot_token']+'/sendMessage',{'chat_id':cfg['chat_id'],'text':title+'\n'+message})
    if kind=='EMAIL':
        msg=EmailMessage(); msg['Subject']=title; msg['From']=cfg['from']; msg['To']=cfg['to']; msg.set_content(message)
        with smtplib.SMTP(cfg['host'],int(cfg.get('port',587)),timeout=10) as smtp:
            if cfg.get('starttls',True): smtp.starttls(context=ssl.create_default_context())
            if cfg.get('username'): smtp.login(cfg['username'],cfg.get('password',''))
            smtp.send_message(msg)
        return 250
    raise ValueError('Unsupported notification channel')

def _record(key,severity,title,message):
    conn=app_db()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT id,notification_sent_at FROM system_alerts WHERE alert_key=%s AND resolved_at IS NULL ORDER BY id DESC LIMIT 1',(key,)); row=cur.fetchone()
            if row: alert_id=row['id']; cur.execute('UPDATE system_alerts SET last_seen_at=NOW(3),occurrence_count=occurrence_count+1,message=%s WHERE id=%s',(message,alert_id))
            else: cur.execute('INSERT INTO system_alerts(alert_key,severity,title,message) VALUES(%s,%s,%s,%s)',(key,severity,title,message)); alert_id=cur.lastrowid
            repeat=int(settings().get('alert_repeat_minutes','60')); should_notify=(not row or not row.get('notification_sent_at') or (datetime.now()-row['notification_sent_at']).total_seconds()>=repeat*60); cur.execute('SELECT * FROM notification_channels WHERE enabled=1'); channels=cur.fetchall() if should_notify else []; conn.commit()
        for channel in channels:
            try: send_channel(channel,title,message)
            except Exception: pass
        with conn.cursor() as cur: cur.execute('UPDATE system_alerts SET notification_sent_at=NOW(3) WHERE id=%s',(alert_id,)); conn.commit()
    finally: conn.close()

def run_checks():
    s=settings(); active=[]; path=Path(s.get('external_storage_path') or os.environ.get('ARCHIVE_ROOT','/archive/mikrotik'))
    try:
        usage=shutil.disk_usage(path); percent=(usage.used/usage.total)*100 if usage.total else 0; warning=float(s.get('storage_warning_percent','80')); critical=float(s.get('storage_critical_percent','90'))
        if percent>=critical: active.append('storage_capacity'); _record('storage_capacity','CRITICAL','Storage critical',str(path)+': %.1f%% used'%percent)
        elif percent>=warning: active.append('storage_capacity'); _record('storage_capacity','WARNING','Storage almost full',str(path)+': %.1f%% used'%percent)
    except Exception as exc: active.append('storage_unavailable'); _record('storage_unavailable','CRITICAL','Storage unavailable',str(exc))
    try:
        conn=syslog_db()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='syslogdb' AND table_name REGEXP '^mikrotik_logs_[0-9]{4}_[0-9]{2}_[0-9]{2}$' ORDER BY table_name DESC LIMIT 1"); table=cur.fetchone()
                if table:
                    name=table['table_name']; cur.execute('SELECT MAX(timestamp) AS ts FROM '+name); last=cur.fetchone()['ts']; stale=int(s.get('ingestion_stale_minutes','5'))
                    if not last or (datetime.now()-last).total_seconds()>stale*60: active.append('ingestion_stale'); _record('ingestion_stale','CRITICAL','No recent network logs','No log received within the last '+str(stale)+' minutes. Last record: '+str(last))
        finally: conn.close()
    except Exception as exc: active.append('database_unavailable'); _record('database_unavailable','CRITICAL','Database check failed',str(exc))
    return active


if __name__=='__main__':
    run_checks()
