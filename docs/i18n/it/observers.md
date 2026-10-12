# Bot osservatori

<!-- i18n-source: docs/observers.md @ 6fc084f -->

> Questa pagina è tradotta dalla pagina in inglese; se le due versioni non coincidono, vale quella in inglese. ([English](../../observers.md))

Bot di Telegram di sola lettura che ricevono una copia degli avvisi del bot principale. Ricevono riepiloghi, avvisi ed errori, ma non possono controllare nessuna sessione.

## <a id="setup"></a>Configurazione

1. Crea un bot con [@BotFather](https://t.me/BotFather)
2. Apri una chat con quel bot e invia `/start`
3. Trova l'ID della tua chat (invia un messaggio, poi apri `https://api.telegram.org/bot<TOKEN>/getUpdates`)
4. Imposta `OBSERVER_BOTS` nell'ambiente di aipager. Se aipager gira in primo piano, esportalo prima di `aipager start`; per il servizio systemd:

```sh
systemctl --user edit aipager    # aggiungi sotto [Service]:
# Environment=OBSERVER_BOTS=<bot_token>:<chat_id>
```

Più osservatori (separati da virgole):

```
OBSERVER_BOTS=111:AAA_first:12345,222:BBB_second:67890
```

Il formato è `token:chat_id`: si divide sull'**ultimo** due punti (il token di un bot contiene già un due punti).

5. Riavvia aipager

## <a id="what-observers-receive"></a>Cosa ricevono gli osservatori

Gli osservatori ricevono questi avvisi da **ogni chat servita dal bot**, cioè ogni chat privata e ogni gruppo, senza nessun filtro: configura un osservatore solo per chat di cui ti fidi per tutto questo. Ogni avviso usa la lingua della chat da cui arriva.

| Evento | Esempio |
|-------|---------|
| Un'attività finisce | `💬 x1 · Finito (2:05, +12 -3)`: solo la riga del risultato, senza la risposta, più il log completo come file `.md` quando la risposta o la scheda sono state tagliate |
| Un lavoro in background finisce | `✅ x1 · Finito (...)`, oppure `⚠️ x1 · Finito (...)` quando un agente è sparito o la fine di un comando in background non si è mai vista |
| Errore dell'API | `⚠️ x1 · I server di Anthropic sono sovraccarichi. Riprova tra un momento.` (senza pulsante per riprovare) |
| Avviso sul contesto | `⚠️ x1 · Contesto usato: 82% - presto verrà compattato in automatico` |
| Compattazione | `🔄 x1 · Compattazione della conversazione` |
| Compattata | `📦 x1 · Conversazione compattata: 82% → 4%` |
| Una richiesta ancora in attesa | `⬆️ x1 · aspetta ancora la tua risposta più in alto`, con la richiesta di permesso o la domanda che aspetta |
| Una sessione al lavoro ma silenziosa | `⏳ x1 · è ancora al lavoro (nessuna novità da 10 min)` |
| Un messaggio che Claude Code non ha preso | `⚠️ x1 · Claude Code non ha ricevuto il messaggio` |
| Fine della sessione | `🔴 x1 · Sessione terminata` |

## <a id="what-observers-dont-receive"></a>Cosa non ricevono gli osservatori

- L'animazione della scheda di attività
- Gli aggiornamenti delle chiamate agli strumenti
- Le richieste di permesso e i loro pulsanti (solo l'avviso «ancora in attesa» qui sopra dice cosa chiede una richiesta)
- I dialoghi AskUserQuestion
- Qualsiasi tastiera o pulsante

Gli osservatori sono completamente senza stato: il messaggio parte e non si aspetta niente. Un osservatore che non funziona non influisce mai sul bot principale.
