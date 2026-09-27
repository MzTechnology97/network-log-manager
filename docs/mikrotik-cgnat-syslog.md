# MikroTik CGNAT and syslog configuration / Configurazione MikroTik CGNAT e syslog

This page provides a reference RouterOS configuration for sending NAT attribution logs to Network Log Manager. Adapt addresses, interface lists and ports to the deployment before applying it.

Questa pagina fornisce una configurazione RouterOS di riferimento per inviare a Network Log Manager i log necessari all'attribuzione NAT. Prima di applicarla, adattare indirizzi, interface list e porte all'installazione.

## Italiano

### Obiettivo

Per una corretta attribuzione CGNAT il server deve ricevere, per ogni nuova connessione, almeno:

- protocollo;
- IP e porta privata del cliente;
- IP e porta pubblica assegnata dalla SNAT;
- IP e porta di destinazione;
- timestamp corretto.

Network Log Manager estrae questi campi dai messaggi firewall RouterOS che includono la sezione `NAT (...)`.

### 1. Esempio CGNAT

Esempio semplice con rete clienti `100.64.0.0/10`, lista WAN `WAN` e IP pubblico `203.0.113.10`:

```routeros
/ip firewall nat
add chain=srcnat src-address=100.64.0.0/10 out-interface-list=WAN \
    action=src-nat to-addresses=203.0.113.10 \
    comment="CGNAT - customer Internet access"
```

`203.0.113.10` è un indirizzo di documentazione: sostituirlo con l'IP o pool pubblico reale. Se il router utilizza già regole CGNAT più articolate, non è necessario sostituirle: la parte essenziale per Network Log Manager è il logging delle nuove connessioni.

### 2. Regola firewall per generare il record di attribuzione

Inserire una regola di solo logging per le nuove connessioni provenienti dalla rete CGNAT:

```routeros
/ip firewall filter
add chain=forward src-address=100.64.0.0/10 connection-state=new \
    action=log log-prefix="NETLOG " \
    comment="Network Log Manager - log new CGNAT connections"
```

`action=log` non accetta e non blocca il traffico: dopo aver scritto il log RouterOS continua con le regole successive. La regola deve quindi essere collocata in una posizione in cui le nuove connessioni Internet dei clienti la attraversino.

Il parser è progettato per messaggi RouterOS contenenti dati equivalenti a:

```text
connection-state:new,snat ... proto TCP ... 100.64.1.20:54321->198.51.100.25:443, NAT (100.64.1.20:54321->203.0.113.10:62001)->198.51.100.25:443
```

Non usare il testo di esempio come formato da generare manualmente: è RouterOS a produrre i dettagli NAT.

### 3. Invio dei log al server

Esempio per un server Network Log Manager all'indirizzo `172.31.0.26`, porta syslog `5514`:

```routeros
/system logging action
add name=netlog-remote target=remote remote=172.31.0.26 remote-port=5514 \
    bsd-syslog=yes syslog-facility=local7 syslog-severity=info

/system logging
add topics=firewall action=netlog-remote
```

Se esiste già un'azione remota con lo stesso nome, modificarla invece di crearne una seconda.

La porta deve corrispondere a quella configurata nell'installazione di Network Log Manager. La configurazione di riferimento usa UDP syslog; proteggere il percorso di management e non esporre la porta syslog direttamente a Internet.

### 4. Ora del router

L'attribuzione legale/operativa dipende dal timestamp. Configurare NTP e il fuso orario corretti su tutti i router:

```routeros
/system clock
set time-zone-name=Europe/Rome

/system ntp client
set enabled=yes
```

Configurare inoltre i server NTP secondo lo standard della propria rete.

### 5. Verifica

Generare una nuova connessione da un cliente CGNAT e verificare sul router:

```routeros
/log print where topics~"firewall"
```

Sul server verificare che `/var/log/network.log` mostri protocollo, sorgente, NAT pubblico e destinazione. Per una verifica completa controllare poi che il record sia presente nella tabella giornaliera MariaDB.

### Note operative

Il logging di ogni nuova connessione può generare un volume elevato. Dimensionare CPU, storage, retention e banda di management in base al numero di sessioni. Evitare regole duplicate che registrino due volte la stessa connessione.

Con FastTrack, assicurarsi che la regola di logging intercetti il primo pacchetto `connection-state=new`; le connessioni già established non devono essere registrate nuovamente per l'attribuzione iniziale.

---

## English

### Goal

For reliable CGNAT attribution, the server should receive at least the protocol, customer private IP/port, translated public IP/port, destination IP/port and an accurate timestamp for each new connection. Network Log Manager parses RouterOS firewall messages containing the `NAT (...)` section.

### 1. CGNAT example

```routeros
/ip firewall nat
add chain=srcnat src-address=100.64.0.0/10 out-interface-list=WAN \
    action=src-nat to-addresses=203.0.113.10 \
    comment="CGNAT - customer Internet access"
```

Replace the documentation address with the actual public address or adapt the rule to the existing CGNAT pool. Existing production NAT policies do not need to be replaced merely to enable logging.

### 2. Log new customer connections

```routeros
/ip firewall filter
add chain=forward src-address=100.64.0.0/10 connection-state=new \
    action=log log-prefix="NETLOG " \
    comment="Network Log Manager - log new CGNAT connections"
```

The `log` action records the packet and then processing continues. Place the rule where new customer Internet connections traverse it.

Expected RouterOS messages contain information equivalent to:

```text
connection-state:new,snat ... proto TCP ... 100.64.1.20:54321->198.51.100.25:443, NAT (100.64.1.20:54321->203.0.113.10:62001)->198.51.100.25:443
```

### 3. Remote syslog

```routeros
/system logging action
add name=netlog-remote target=remote remote=172.31.0.26 remote-port=5514 \
    bsd-syslog=yes syslog-facility=local7 syslog-severity=info

/system logging
add topics=firewall action=netlog-remote
```

Use the actual Network Log Manager address and configured syslog port. The reference deployment uses UDP syslog; keep it on a protected management path rather than exposing it directly to the Internet.

### 4. Time synchronization

```routeros
/system clock
set time-zone-name=Europe/Rome

/system ntp client
set enabled=yes
```

Configure the appropriate NTP servers for the network.

### 5. Validation

Generate a new connection from a CGNAT customer and check:

```routeros
/log print where topics~"firewall"
```

Then verify `/var/log/network.log` and the current MariaDB daily table. The parsed record should contain source IP/port, translated public IP/port, destination IP/port and protocol.

### Operational notes

Logging every new connection can produce substantial traffic and storage usage. Size CPU, storage, retention and management bandwidth accordingly and avoid duplicate logging rules. With FastTrack, ensure the logging rule sees the initial `connection-state=new` packet.
