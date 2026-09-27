# Troubleshooting / Risoluzione problemi

## Italiano
### Stack Docker non sano
Eseguire `/opt/netlog-manager/scripts/healthcheck.sh`, quindi controllare `docker compose ps` e i log del servizio interessato.

### MariaDB non pronta al primo avvio
L'inizializzazione del volume può richiedere tempo. Attendere lo stato `healthy`; non eseguire migration durante il temporary server iniziale.

### CreateFutureTables: access denied
Verificare l'esistenza di `netlog_maintenance`, il privilegio CREATE su `syslogdb.*` e EXECUTE sulla procedura. Le migration applicano il grant prima dell'invocazione iniziale.

### Applicazione: directory static mancante
Le immagini correnti creano sempre `/opt/netlog-manager/static`. Ricostruire l'immagine se si usa una revisione precedente.

### Archivio
Un file archivio esistente non causa la cancellazione della tabella sorgente. Il DROP avviene soltanto dopo dump, test zstd, verifica contenuto e checksum. Non usare `/archive/mikrotik/test` come archivio di produzione.

### Storage locale OFFLINE / Permission denied
Su Docker verificare prima lo stato dalla pagina **Impostazioni** e poi i permessi del bind mount. Il runtime usa UID 10001/GID 999 e l'archive root deve essere scrivibile dal monitor. Gli updater correnti riparano la sola root con mode 0750 senza eseguire chown ricorsivi sui dati storici.

### Notifiche automatiche
Controllare **Ultima consegna automatica** e la tabella `notification_delivery_log`: ogni tentativo automatico viene registrato come SENT o FAILED con l'errore disponibile. Verificare anche che il canale sia sottoscritto allo specifico event type.

### Restore
Il restore Docker accetta soltanto backup nel percorso gestito, verifica `SHA256SUMS` e crea un backup di sicurezza prima del ripristino. Il restore applicativo non cancella i log online esistenti in `syslogdb`.

## English
Use `/opt/netlog-manager/scripts/healthcheck.sh`, `docker compose ps`, and service logs for unhealthy stacks. Wait for MariaDB to become `healthy` before migrations. Maintenance errors require the maintenance account, CREATE on `syslogdb.*`, and EXECUTE on the procedure. Archive source tables are dropped only after validation and checksum verification. Docker restore verifies checksums, creates a safety backup, and preserves existing online log data.
