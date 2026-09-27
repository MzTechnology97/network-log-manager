import json, os, shutil, smtplib, ssl, urllib.request
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
import socket
from .database import app_db, syslog_db
from .remote_storage import browse as storage_browse, decrypt_secret
from .storage_registry import health_check_all

ALERT_KEYS=('storage_capacity','storage_unavailable','storage_health','storage_replication','ingestion_stale','database_unavailable','syslog_listener_down')

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
    cfg=json.loads(channel['configuration_json'] or '{}'); cfg.update(json.loads(channel.get('secret_json') or '{}')); kind=channel['channel_type']
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

def _record(key,severity,title,message,repeat_minutes):
    conn=app_db()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT id,notification_sent_at FROM system_alerts WHERE alert_key=%s AND resolved_at IS NULL ORDER BY id DESC LIMIT 1',(key,)); row=cur.fetchone()
            if row:
                alert_id=row['id']; cur.execute('UPDATE system_alerts SET last_seen_at=NOW(3),occurrence_count=occurrence_count+1,message=%s,severity=%s,title=%s WHERE id=%s',(message,severity,title,alert_id))
            else:
                cur.execute('INSERT INTO system_alerts(alert_key,severity,title,message) VALUES(%s,%s,%s,%s)',(key,severity,title,message)); alert_id=cur.lastrowid
            should_notify=(not row or not row.get('notification_sent_at') or (datetime.now()-row['notification_sent_at']).total_seconds()>=repeat_minutes*60)
            cur.execute('SELECT * FROM notification_channels WHERE enabled=1'); channels=cur.fetchall() if should_notify else []; conn.commit()
        delivered=False
        for channel in channels:
            try:
                selected=json.loads(channel.get('event_types_json') or '[]')
                # An explicit subscription list is authoritative. Legacy channels
                # with NULL/empty subscriptions keep receiving all events.
                if selected and key not in selected: continue
                send_channel(channel,title,message)
                delivered=True
                with conn.cursor() as cur:
                    cur.execute("""INSERT INTO notification_delivery_log(alert_id,channel_id,event_type,status,attempted_at,error_message)
                                   VALUES(%s,%s,%s,'SENT',NOW(3),NULL)""",(alert_id,channel['id'],key)); conn.commit()
            except Exception as exc:
                try:
                    with conn.cursor() as cur:
                        cur.execute("""INSERT INTO notification_delivery_log(alert_id,channel_id,event_type,status,attempted_at,error_message)
                                       VALUES(%s,%s,%s,'FAILED',NOW(3),%s)""",(alert_id,channel['id'],key,str(exc)[:1000])); conn.commit()
                except Exception:
                    pass
        if delivered:
            with conn.cursor() as cur:
                cur.execute('UPDATE system_alerts SET notification_sent_at=NOW(3) WHERE id=%s',(alert_id,)); conn.commit()
    finally: conn.close()

def _resolve_inactive(active):
    inactive=[key for key in ALERT_KEYS if key not in active]
    if not inactive: return
    conn=app_db()
    try:
        with conn.cursor() as cur:
            placeholders=','.join(['%s']*len(inactive))
            cur.execute('UPDATE system_alerts SET resolved_at=NOW(3) WHERE resolved_at IS NULL AND alert_key IN ('+placeholders+')',tuple(inactive)); conn.commit()
    finally: conn.close()

def _latest_log_timestamp(cur):
    # Future daily tables are pre-created, therefore ORDER BY table_name DESC is
    # not a valid ingestion freshness signal. Inspect only today/yesterday.
    for day in (datetime.now().date(), (datetime.now()-timedelta(days=1)).date()):
        name="mikrotik_logs_"+day.strftime("%Y_%m_%d")
        cur.execute("""SELECT COUNT(*) AS c FROM information_schema.tables
                       WHERE table_schema='syslogdb' AND table_name=%s""",(name,))
        if int(cur.fetchone()["c"] or 0):
            cur.execute("SELECT MAX(timestamp) AS ts FROM `"+name+"`")
            ts=cur.fetchone()["ts"]
            if ts is not None: return ts
    return None

def run_checks():
    s=settings(); active=[]; repeat=int(s.get('alert_repeat_minutes','60'))
    # Multi-storage health. A quick read/list check runs each monitor cycle.
    try:
        health=health_check_all(deep=True)
        failed=[h for h in health if h["status"]!="HEALTHY"]
        if failed:
            active.append('storage_health')
            detail="; ".join(h["name"]+": "+(h.get("error") or h["status"]) for h in failed)
            severity='CRITICAL' if any(h["status"]=='OFFLINE' for h in failed) else 'WARNING'
            _record('storage_health',severity,'Storage health check failed',detail,repeat)
    except Exception as exc:
        active.append('storage_health'); _record('storage_health','CRITICAL','Storage health check failed',str(exc),repeat)
    # Capacity remains a local filesystem check; remote capacity is reported
    # only when the backend can expose it reliably.
    path=Path(os.environ.get('ARCHIVE_ROOT','/archive/mikrotik'))
    try:
        usage=shutil.disk_usage(path); percent=(usage.used/usage.total)*100 if usage.total else 0
        warning=float(s.get('storage_warning_percent','80')); critical=float(s.get('storage_critical_percent','90'))
        if percent>=critical:
            active.append('storage_capacity'); _record('storage_capacity','CRITICAL','Storage critical',str(path)+': %.1f%% used'%percent,repeat)
        elif percent>=warning:
            active.append('storage_capacity'); _record('storage_capacity','WARNING','Storage almost full',str(path)+': %.1f%% used'%percent,repeat)
    except Exception as exc:
        active.append('storage_unavailable'); _record('storage_unavailable','CRITICAL','Storage unavailable',str(exc),repeat)
    # Replication failures are a separate condition from reachability.
    try:
        conn=app_db()
        try:
            with conn.cursor() as cur:
                cur.execute("""SELECT COUNT(*) AS c FROM storage_replication_state
                               WHERE status='FAILED' AND last_attempt_at >= NOW() - INTERVAL 24 HOUR""")
                failures=int(cur.fetchone()['c'] or 0)
            if failures:
                active.append('storage_replication')
                _record('storage_replication','WARNING','Storage replication incomplete',
                        str(failures)+' archive object(s) failed replication in the last 24 hours.',repeat)
        finally: conn.close()
    except Exception:
        pass
    try:
        conn=syslog_db()
        try:
            with conn.cursor() as cur:
                stale=int(s.get('ingestion_stale_minutes','5')); last=_latest_log_timestamp(cur)
                if last is None or (datetime.now()-last).total_seconds()>stale*60:
                    active.append('ingestion_stale')
                    _record('ingestion_stale','CRITICAL','No recent network logs',
                            'No log received within the last '+str(stale)+' minutes. Last record: '+str(last),repeat)
        finally: conn.close()
    except Exception as exc:
        active.append('database_unavailable'); _record('database_unavailable','CRITICAL','Database check failed',str(exc),repeat)
    try:
        with socket.create_connection((os.environ.get('SYSLOG_LISTENER_HOST','syslog'),
                                       int(os.environ.get('SYSLOG_LISTENER_PORT','5514'))),timeout=2): pass
    except OSError as exc:
        active.append('syslog_listener_down'); _record('syslog_listener_down','CRITICAL','Syslog listener unavailable',str(exc),repeat)
    _resolve_inactive(active)
    return active


if __name__=='__main__':
    run_checks()
