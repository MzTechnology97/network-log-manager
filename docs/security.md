# Security / Sicurezza

## Italiano
Il progetto separa gli account MariaDB per funzione: applicazione CRUD su `netlog_manager`, reader SELECT su `syslogdb`, ingest INSERT su `syslogdb`, maintenance per creazione tabelle e routine. L'interfaccia supporta ruoli Administrator, Operator e Auditor, sessioni server-side, Argon2id, TOTP MFA, CSRF, lockout e cookie Secure/HttpOnly/SameSite.

I secret non devono essere inseriti nel repository. I file runtime sotto `/etc/netlog-manager` e lo stato Docker devono rimanere protetti. TLS self-signed è disponibile al bootstrap; in produzione è preferibile un certificato della PKI aziendale o pubblicamente attendibile. HSTS non è abilitato per default.

## English
MariaDB duties are split across application, read-only log, ingest and maintenance accounts. The UI implements Administrator, Operator and Auditor roles, server-side sessions, Argon2id, TOTP MFA, CSRF protection, lockout and secure cookie attributes.

Never commit runtime secrets. Protect `/etc/netlog-manager` and Docker state. Bootstrap can generate a self-signed TLS certificate; production deployments should preferably use an organizational or publicly trusted certificate. HSTS is intentionally not enabled by default.
