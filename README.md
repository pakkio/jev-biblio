# 📚 Skepsis

> Harness Jev per verificare se le affermazioni di un testo sono sostenute dalle sue fonti.

Dashboard web locale che controlla se un articolo è davvero sostenuto dalle sue fonti:

- **verifica i link** della bibliografia e **scarica il testo delle fonti**;
- **classifica l'autorevolezza di ogni fonte** con Jev in 4 fasce — 🟢 autorevole, 🟡 semi-autorevole, 🟠 opinione, 🔴 inaffidabile (o inesistente) — indipendentemente dal voto dell'articolo;
- **estrae le affermazioni** dell'articolo con un LLM, separando fatti, conclusioni dell'autore, opinioni ed esperienze personali;
- **verifica ogni affermazione sul testo delle fonti con [Jev](https://typesafe.ai)** (TypeSafe *System One*) e ne stima la plausibilità secondo la conoscenza generale;
- **passa a un LLM che ragiona solo i casi in cui Jev è incerto**;
- **assegna da 1 a 5 stelle con regole esplicite** sui verdetti, mostrando il motivo, e **scrive una giustificazione in italiano**;
- **mostra tempi e costi** di ogni passaggio, con il totale in centesimi.

Una verifica completa costa in media **0,1-0,2 ¢** e richiede **10-40 secondi**, a seconda della lunghezza dell'articolo. Ultima esecuzione (settembre 2026): 100% sul set di test, 95% sul set di sviluppo (vedi [Valutazione](#valutazione) per i numeri completi, per la regressione del 27 settembre e per cosa significa ormai "test" — non è più un set indipendente).

## Avvio rapido

Serve [uv](https://docs.astral.sh/uv/). Le dipendenze (`typesafe-sdk`, `python-dotenv`, `pypdf`, `curl-cffi`) sono dichiarate in testa a `main.py` e uv le installa da solo.

```bash
uv run main.py                      # apre http://127.0.0.1:8000/
uv run main.py --port 9000 --no-browser
```

Skepsis ascolta solo su localhost. Per esporla intenzionalmente a una rete bisogna aggiungere `--host … --allow-remote` e metterla dietro un proxy con autenticazione e rate limiting.

Incolla il testo dell'articolo e la bibliografia con gli URL, poi premi **Avvia analisi Jev e controllo link**. Nella bibliografia i riferimenti possono essere numerati (`[1] Titolo https://…`) e citati nel testo con `[1]`. Se l'articolo non usa i numeri, è il LLM ad associare ogni affermazione alle fonti pertinenti.

Se hai copiato una pagina intera (articolo e bibliografia già insieme, come capita incollando una pagina web), puoi lasciare vuoto il campo della bibliografia: Jev prova a separare le due parti da solo (vedi il passaggio 1 in [Come funziona](#come-funziona)). Se non trova un elenco di fonti separato, il testo resta tutto nell'articolo e la dashboard chiede comunque una bibliografia.

## Chiavi API

Il programma legge le chiavi dall'ambiente oppure da `./.env` o `../.env`:

```dotenv
TYPESAFE_API_KEY=...      # obbligatoria: verifiche e voto di Jev
OPENROUTER_API_KEY=...    # consigliata: estrazione delle affermazioni, casi incerti, giustificazione
TAVILY_API_KEY=...        # facoltativa: ricerca esterna di fonti autorevoli
```

Senza `OPENROUTER_API_KEY` la dashboard funziona in modalità ridotta: divide l'articolo per frasi invece di estrarre le affermazioni, non passa a un LLM i casi incerti e mostra solo il riepilogo di Jev.

## Come funziona

| Passaggio | Chi lo fa | Cosa succede |
|---|---|---|
| 1. Separazione (solo se manca la bibliografia) | Jev, una chiamata con una domanda sì/no per paragrafo (riserva: euristica sui titoli di sezione) | Se l'utente incolla una pagina intera (articolo e bibliografia insieme) senza compilare il campo bibliografia, Jev classifica ogni paragrafo come "articolo" o "elenco di fonti" e taglia al primo paragrafo di bibliografia confermato da una maggioranza dei successivi. Senza questo, o se Jev non decide nulla, il testo resta tutto nell'articolo. |
| 2. Link e fonti | Python | Scarica in parallelo fino a 15 URL pubblici, con un tetto complessivo di 8 s; legge HTML, testo e PDF entro limiti di dimensione. Usa `curl_cffi` per replicare l'handshake TLS di Chrome (alcuni siti dietro Cloudflare bloccano il fingerprint TLS di `urllib`/`requests` anche con header credibili); se non è installato, torna a `urllib`. |
| 3. Autorevolezza delle fonti | Jev, una chiamata per fonte, in parallelo | Classifica ogni fonte in **Autorevole** 🟢, **Semi-autorevole** 🟡, **Opinione** 🟠 o **Inaffidabile** 🔴, in base a titolo, dominio e un estratto della pagina. Le fonti probabilmente inesistenti (DNS, 404, 410) sono già "Inaffidabile" senza bisogno di chiedere a Jev. **Non entra nel voto delle stelle**: è un'informazione separata su quanto vale citare quella fonte, non su quanto l'affermazione è sostenuta. |
| 4. Estrazione | LLM (`gpt-6-luna`), a blocchi in parallelo se l'articolo supera ~2.200 caratteri | Divide il testo in affermazioni atomiche: **fatto**, **conclusione** dell'autore ("quindi", "dimostra che"), **opinione** o **esperienza** in prima persona. Per ognuna indica le fonti e le parole chiave in italiano e inglese. Temperatura 0, per essere il più possibile ripetibile. |
| 5. Estratti | Python + Jev | Per ogni affermazione trova fino a 12 paragrafi candidati per fonte, per parole chiave e numeri confrontati per valore ("4.700" corrisponde a "4.7k"). Poi Jev, con una domanda sì/no per paragrafo in un'unica chiamata, sceglie quelli che servono davvero a confermare o smentire. |
| 6. Verifica | Jev, una chiamata per affermazione, in parallelo | Decide se gli estratti sostengono l'affermazione, con quale confidenza, quanto è plausibile secondo la conoscenza generale e — solo se l'affermazione contiene numeri (date, quantità, misure) — se quei numeri corrispondono a quelli delle fonti. |
| 7. Casi incerti | LLM che ragiona (`gpt-6-luna-pro`) | Solo se la confidenza di Jev è sotto l'80%: rilegge affermazione ed estratti e decide, con una frase di motivazione. |
| 8. Voto | regole + Jev | Le stelle si calcolano con le regole qui sotto. Jev dà anche pertinenza, aderenza e un voto globale (`Score`), mostrati come informazione e usati come riserva se l'articolo non contiene fatti verificabili (o se il campione è troppo piccolo, o nessuna affermazione risulta confermata). |
| 9. Giustificazione | LLM (`gpt-6-luna`) | Scrive 4-6 frasi in italiano senza cambiare i voti. |
| 10. Riscontro esterno (facoltativo) | Tavily + Python + Jev | Se l'utente seleziona l'opzione, cerca fino a 2 fonti per ciascuno dei primi 5 fatti o conclusioni non coperti. Accetta solo domini con una policy dichiarata (enti pubblici, università e una piccola lista di editori/istituzioni), scarica il testo in sicurezza e mostra un verdetto separato. **Non modifica mai le stelle** della bibliografia originale. |

Opinioni ed esperienze personali non vengono verificate e non abbassano il voto. Le conclusioni dell'autore ("questo dimostra che…") invece vengono verificate: è lì che si nascondono gli articoli fuorvianti.

### Controllo mirato sui numeri

Quando un'affermazione contiene un numero abbastanza specifico da poter essere alterato (non un piccolo intero come "5 strati"), Jev riceve una domanda in più nella stessa chiamata, a tre vie: gli estratti riportano, per lo stesso soggetto specifico dell'affermazione, lo stesso numero (**Uguale**), un numero diverso (**Diverso**), o non ne parlano affatto (**Assente**, anche se contengono un numero diverso ma per un soggetto diverso o più specifico). Solo **Diverso** conta come alterazione nelle regole di voto (sotto); prima del 27 settembre 2026 era una domanda sì/no che confondeva "Diverso" e "Assente" in un solo "no", ed è per questo che un fatto vero su Webb (una temperatura generale degli strumenti, quando l'estratto scelto citava solo un componente specifico) veniva occasionalmente segnalato come "numero alterato" pur non essendolo.

### Regole di voto

Le stelle non sono un giudizio complessivo "a sensazione": si calcolano dai verdetti sulle singole affermazioni, in quest'ordine, e la dashboard mostra la regola che ha deciso.

1. **1★ Molto falso**: un fatto è contraddetto dalle fonti **e** implausibile secondo Jev (le due cose insieme bastano da sole), oppure ci sono almeno due fatti gravemente problematici (contraddetti, con numeri alterati, o citati solo da una fonte che probabilmente non esiste — DNS inesistente, 404, 410, non semplicemente bloccata da un sito anti-bot).
2. **2★ Vero ma fuorviante**: un solo fatto problematico isolato, oppure i fatti reggono ma una conclusione dell'autore è esagerata, contraddetta o implausibile, oppure un fatto implausibile distorce la fonte.
3. **5★ Ottimo**: almeno 3 affermazioni, tutte pienamente sostenute, e fonti non solo enciclopediche.
4. **4★ Molto buono**: almeno il 60% delle affermazioni confermato dalle fonti. Un articolo basato solo su Wikipedia arriva al massimo a 4★.
5. **3★ Buono**: negli altri casi, purché almeno una affermazione su tre sia confermata dalle fonti.

Con meno di 3 affermazioni verificabili estratte, o con **zero** affermazioni confermate dalle fonti, le regole non decidono e il voto è affidato al voto globale di Jev (`rating_source: "Jev"` invece di `"regole"`): un campione troppo piccolo, o nessuna prova a favore, non giustificano un "Buono" con le regole sopra.

La sola implausibilità di Jev secondo la conoscenza generale **non basta mai da sola** a far scattare una regola: per fatti recenti, tecnici o di nicchia è spesso solo ignoranza del modello, non falsità. Conta solo insieme a un'evidenza reale (una fonte che la contraddice, un numero diverso in un estratto trovato, o una fonte citata che risulta proprio inesistente).

La separazione tra premesse e conclusioni è ciò che distingue 1★ da 2★: il voto globale di Jev, da solo, dava 1★ a quasi tutti gli articoli fuorvianti.

Il motivo (`rating_reason`) elenca **tutte** le affermazioni responsabili del voto, non solo la prima trovata: se un articolo ha più fatti gravemente problematici, o più conclusioni non giustificate, compaiono tutte, ciascuna con i numeri delle fonti citate (es. «affermazione» [1][3]).

### Verdetti per affermazione

L'API espone il verdetto canonico in `verdict` (usato dalle regole di voto) e la versione mostrata in dashboard in `verdict_label`, identica tranne per "Parzialmente sostenuta".

| Verdetto | Significato |
|---|---|
| Sostenuta | gli estratti dicono la stessa cosa |
| Parzialmente sostenuta | il nucleo è confermato, alcuni dettagli non compaiono negli estratti. Mostrata come **Quasi sostenuta** se Jev era abbastanza sicuro da non richiedere l'intervento del LLM (confidenza ≥ soglia di escalation, 80% di default); altrimenti resta **Parzialmente sostenuta** — la confidenza misura quanto il giudice è sicuro del verdetto, non quanta parte dell'affermazione è coperta, quindi non si presta a una scala fine di gradazione |
| Esagerata | l'affermazione trae conclusioni o certezze che le fonti non danno |
| Contraddetta | le fonti dicono il contrario |
| Non trovata | gli estratti non trattano l'argomento |
| Fonte irraggiungibile | la fonte citata non si scarica |
| Senza fonte | nessuna fonte citata per un fatto |
| Opinione / Esperienza personale | non verificata |

### Scala delle stelle

| Stelle | Significato | Colore |
|---|---|---|
| ★☆☆☆☆ | **Molto falso**: affermazioni false o inventate, fonti inesistenti o contrarie | rosso |
| ★★☆☆☆ | **Vero ma fuorviante**: fatti veri presentati o forzati in modo ingannevole | giallo |
| ★★★☆☆ | **Buono**: fonti accettabili che sostengono le affermazioni principali | verde |
| ★★★★☆ | **Molto buono**: fonti affidabili, solo lacune minori | blu |
| ★★★★★ | **Ottimo**: fonti autorevoli che sostengono pienamente ogni affermazione | oro |

Da 3 stelle in su il risultato è considerato positivo (`rating_good: true`).

## Valutazione

`eval/` contiene due set di articoli brevi con fonti reali (Wikipedia italiana e inglese, NASA, ESA). Ogni argomento ha una versione corretta (4★ attese), una fuorviante (2★: fatti veri, conclusioni distorte) e una falsa (1★).

- **Sviluppo** (`dataset.jsonl`, 21 articoli): Apollo 11, Marie Curie, Torre di Pisa, Grande muraglia, fotosintesi, Python, telescopio Webb. Usato per mettere a punto le regole.
- **Test** (`test_dataset.jsonl`, 18 articoli): Everest, penicillina, Galileo, Titanic, Colosseo, DNA. Scritto dopo aver fissato le regole. Metà degli articoli non ha marcatori `[n]`.

**Il set di test non è più "usato una sola volta".** È servito a scoprire il bug di settembre 2026 (sotto) ed è stato rilanciato più volte per verificarne la correzione: da qui in avanti conta come un secondo set di sviluppo, non come test indipendente. Il prossimo cambiamento alle regole di voto ha bisogno di articoli nuovi, mai usati per tarare nulla.

```bash
uv run --no-project python eval/build_dataset.py                   # rigenera i due set
uv run eval/run_eval.py --workers 4                                 # set di sviluppo
uv run eval/run_eval.py --dataset test_dataset.jsonl --workers 4    # set di test
uv run eval/run_eval.py --only apollo11,curie                       # solo alcuni argomenti
```

Ogni esecuzione salva i dettagli in `eval/results/` con un timestamp, quindi un run parziale non sovrascrive più il benchmark precedente. Usa `--output percorso.json` solo quando vuoi scegliere esplicitamente il file da aggiornare.

Risultati di settembre 2026:

| | Voto globale Jev (v1) | Per affermazione, voto Jev (v2) | Per affermazione + regole (v3) | v3 + estratti Jev, estrazione a blocchi, controllo numeri (v4) | bug del 27/9 pomeriggio (v4.5, prima della correzione sotto) | dopo la correzione del 27/9 (v5) | controllo numeri per soggetto + link rot (v5.1) | numeri a 3 vie: Uguale/Diverso/Assente (v5.2) |
|---|---|---|---|---|---|---|---|---|
| Stelle esatte, sviluppo | 62% | 71% | 95% | 90-95%* | 81% (17/21) | 86% (18/21) | 90% (19/21) | **95% (20/21)** |
| Entro una stella, sviluppo | — | — | — | — | 90% | 95% | 95% | 100% |
| Stelle esatte, **test** | — | — | 100% | 100% | **89% (16/18)** | 100% (18/18) | 100% (18/18) | 100% (18/18) |
| Tempo medio, sviluppo | 5 s | 16,5 s | 14,4 s | 17,1 s | — | 12,4 s | 12,4 s | 12,3 s |
| Tempo medio, **articolo lungo** (~9.000 caratteri) | — | — | 20,8 s (estrazione) | **11,6 s** (estrazione, -44%) | — | — | — | — |
| Costo medio, sviluppo | 0,019 ¢ | 0,196 ¢ | 0,202 ¢ | 0,225 ¢ | — | 0,222 ¢ | 0,234 ¢ | 0,231 ¢ |

*\*Ho visto oscillare lo stesso articolo tra 3★ e 4★ da un'esecuzione all'altra (Grande muraglia, versione corretta): la selezione degli estratti fatta da Jev non è deterministica, quindi a volte sceglie un paragrafo diverso e il verdetto cambia. Non è una regressione di questa versione — l'ho verificato rilanciando lo stesso articolo tre volte.*

**La regressione misurata del 27 settembre 2026 è stata sul set di test, non sullo sviluppo, ed è la colonna v4.5 sopra.** Il set di sviluppo non era mai stato al 100%: la sua traiettoria è 62% → 71% → 95% → 90-95% → 81% → 86%, con oscillazioni note (nota\* sopra). Il test invece era stabile al 100% dalle versioni v3-v4, ed è sceso a **89% (16/18)** — `penicillina-false` votato 2★ invece di 1★, `titanic-misleading` votato 4★ invece di 2★ — a causa di una correzione fatta poche ore prima per togliere un falso positivo (una fonte vera ma bloccata, tipo un 401 su un repository gated, faceva scattare "1★ molto falso" solo perché Jev non riconosceva un fatto tecnico recente): quella correzione aveva tolto l'unico segnale che intercettava un fatto contraddetto ma isolato, o un campione troppo piccolo per un voto affidabile. È il compromesso classico tra falsi positivi e falsi negativi. I dettagli di quella run (16/18) restano in `eval/results/test_dataset_20260927T154511Z.json`, non cancellati. La correzione seguente (stessa giornata, v5) ha richiesto **due segnali indipendenti d'accordo** — un'evidenza reale (fonte che contraddice, o citazione a una fonte che non esiste) più l'implausibilità di Jev — invece di uno solo, e ha anche fermato le regole dal dichiarare un voto "dalle regole" quando il campione è troppo piccolo (meno di 3 affermazioni verificabili) o non c'è alcuna prova a favore (0% confermato): in questi casi il voto è affidato al voto globale di Jev invece che a una regola che non ha materiale per decidere. Questo ha riportato il test al 100% (18/18) e portato lo sviluppo da 81% a 86%.

**Il 100% sul test, a questo punto, non è più un risultato su dati indipendenti.** Due dei tre casi che l'hanno riportato al 100% (`penicillina-false`, `titanic-misleading`) sono stati usati per scoprire e correggere il bug appena descritto: contano come accuratezza su dati già usati per tarare le regole, non come una valutazione alla cieca (vedi anche la nota sul set di test, sopra).

**v5.1** ha corretto due falsi allarmi nuovi scoperti su `jwst-misleading` (era finito a 1★ invece di 2★): il controllo sui numeri chiedeva a Jev di confrontare il numero solo con lo stesso soggetto (una temperatura riferita "agli strumenti" in generale non è contraddetta da una temperatura diversa di un singolo componente citato a parte negli estratti); e un 404/410, o un DNS che non risolve più, viene ora ritentato su Wayback Machine prima di essere trattato come "citazione inventata" o fonte "Inaffidabile" — un link rotto (fonte vera, spostata o rimossa) non è la stessa cosa di una fonte mai esistita.

`jwst-clean` restava però un miss: non era rumore ma **un valore esattamente sul bordo della soglia**. Il JSON di valutazione (salvato per i mismatch da v5.1 in poi) mostrava `number_match` a una domanda sì/no e `world` a 0,64 contro una soglia di 0,65 — bastava un centesimo. La causa era la stessa domanda sì/no del controllo numeri: rispondeva "no" sia quando la fonte riportava un numero diverso, sia quando semplicemente non ne parlava (l'estratto scelto per "le operazioni sono iniziate a luglio 2022" diceva "dopo sei mesi", senza la data). **v5.2** trasforma quella domanda in una scelta a tre vie — Uguale / Diverso / Assente — e conta come alterazione solo "Diverso": lo stesso fatto ora risulta "Assente" in ogni run, e non tocca più la soglia. `jwst-clean` è passato a 4★ in 5 esecuzioni su 5.

**Come leggerli:**
- **Variabilità:** oltre al caso sopra, gli articoli fuorvianti al confine tra 1★ e 2★ possono cambiare risultato da un'esecuzione all'altra (es. Galileo fuorviante: 2★, 1★, 1★ su tre run; il telescopio Webb, versione corretta, ha oscillato tra 4★ e 2★ per lo stesso motivo — un singolo controllo sui numeri vicino alla soglia). L'estrazione del LLM a volte marca come "fatto" una parte di una conclusione.
- **Chi ha scritto le etichette:** entrambi i set sono stati scritti ed etichettati da chi ha sviluppato le regole, con tre categorie nette. Non sono più test indipendenti (vedi sopra).
- **Margine statistico:** con 18-21 articoli, queste percentuali hanno un margine di diversi punti in entrambe le direzioni.
- **Articoli reali:** quelli veri sono più sfumati (errori isolati in articoli buoni, opinioni mescolate ai fatti) e il confine tra 3★, 4★ e 5★ è meno netto di quello tra falso e fuorviante.
- **Prossimo passo:** raccogliere articoli reali etichettati da persone, e scrivere articoli di valutazione nuovi (mai usati per tarare le regole) prima del prossimo cambiamento.

## Variabili d'ambiente

| Variabile | Predefinito | Descrizione |
|---|---|---|
| `TYPESAFE_API_KEY` | — | chiave TypeSafe (obbligatoria) |
| `OPENROUTER_API_KEY` | — | chiave OpenRouter |
| `TAVILY_API_KEY` | — | abilita la ricerca facoltativa di fonti esterne; senza chiave la verifica normale resta disponibile |
| `OPENROUTER_MODEL` | `openai/gpt-6-luna` | modello per estrazione e giustificazione |
| `ESCALATION_MODEL` | `openai/gpt-6-luna-pro` | modello che ragiona sui casi incerti |
| `ESCALATION_THRESHOLD` | `0.8` | sotto questa confidenza di Jev il caso passa al LLM |

| `MAX_ESCALATIONS` | `8` | massimo di casi incerti passati al LLM per articolo |
| `EXCERPT_SELECTION` | `jev` | `jev`: Jev sceglie i paragrafi pertinenti; `keywords`: solo parole chiave, più economico |
| `JEV_PRICE_PER_M_INPUT` | `0.042` | prezzo Jev in USD per milione di token di input |

Limiti applicati: articolo fino a 100.000 caratteri, bibliografia fino a 30.000 caratteri, singola fonte fino a 2 MB e fino a 50 pagine PDF. Gli URL con indirizzi locali, privati o riservati sono rifiutati. La ricerca esterna considera al massimo 5 claim e 2 risultati filtrati per claim.

Se il modello scelto su OpenRouter non risponde, si passa al Gemini Flash Lite più recente con versione ≥ 3.5 e poi a `openrouter/free`. La tabella *Tempi e costi* mostra quale modello ha risposto davvero.

### Costi

- **Jev**: $42 per miliardo di token di input (fonte: typesafe.ai); l'output è gratuito.
- **OpenRouter**: si usa il costo reale restituito da OpenRouter (`usage.cost`); se manca, viene calcolato dal listino del modello.

Nella verifica per affermazione la voce più costosa sono i casi incerti passati al LLM che ragiona. Alzare o abbassare `ESCALATION_THRESHOLD` sposta il compromesso tra costo e precisione.

## API

```bash
POST /api/verify
Content-Type: application/json

{"article": "testo dell'articolo…", "bibliography": "[1] Titolo https://…", "find_authoritative_sources": true}
```

Risposta (abbreviata):

```json
{
  "links": [{"url": "https://…", "status": "200", "active": true}],
  "external_check": {"requested": true, "message": "Riscontro esterno: non modifica il voto della bibliografia originale.", "sources": [], "checks": []},
  "claims": [
    {"text": "Lo specchio primario di Webb ha un diametro di 6,5 metri.", "kind": "fatto", "refs": ["2"],
     "verdict": "Sostenuta", "verdict_label": "Sostenuta", "confidence": 1.0, "world": 0.98, "decided_by": "Jev", "reason": "", "excerpts": {"2": "…"}}
  ],
  "claim_counts": {"Sostenuta": 9, "Esagerata": 3, "Non trovata": 4},
  "relation": "Eccellente",
  "adherence": "Discrepanza parziale",
  "rating": "★★☆☆☆ Vero ma fuorviante",
  "rating_stars": 2,
  "rating_reason": "conclusione non giustificata dalle fonti:\n⚠️ «Il rilevamento di anidride carbonica … significa che Webb ha trovato un forte indizio di vita» [2]",
  "rating_source": "regole",
  "rating_value": 2.31,
  "rating_confidence": 0.73,
  "rating_good": false,
  "explanation": "…",
  "metrics": {
    "steps": [
      {"step": "Estrazione affermazioni", "model": "openai/gpt-6-luna", "seconds": 10.3,
       "input_tokens": 908, "output_tokens": 1360, "cost_cents": 0.077, "error": null}
    ],
    "total_seconds": 23.4,
    "total_cents": 0.36
  }
}
```

In caso di errore la risposta è `{"error": "…"}`.

## Esempi

| File | Bibliografia | Contenuto |
|---|---|---|
| `samples/jwst_article.txt` | `jwst_bibliography.txt` | Webb, con "Big Bang smentito" citato da un blog inesistente |
| `samples/jwst_article_misleading.txt` | `jwst_bibliography_clean.txt` | Webb, con la CO₂ presentata come "segno di vita" |
| `samples/jwst_article_clean.txt` | `jwst_bibliography_clean.txt` | Webb, versione corretta |
| `samples/harness_article.txt` | `harness_bibliography.txt` | saggio in prima persona su agenti e intelaiature: soprattutto esperienze e opinioni, pochi fatti verificabili (Chatbot Arena, SWE-bench) |

## File

- `main.py`: server locale, orchestrazione dei passaggi e pagina HTML (Bootstrap + Bootstrap Icons da CDN).
- `claims.py`: download delle fonti, estrazione delle affermazioni, scelta degli estratti, verifica con Jev e passaggio al LLM.
- `discovery.py`: ricerca facoltativa e policy trasparente per fonti esterne autorevoli.
- `llm.py`: chiamate a OpenRouter con catena di riserva, tempi e costi.
- `eval/`: set di sviluppo e di test etichettati, script di valutazione.
- `colab_main.py`: demo originale per Google Colab, che legge la chiave dai *Colab Secrets*; non include ancora la pipeline completa di Skepsis (estratti, PDF e protezioni URL).
- `samples/`: articoli e bibliografie di prova.

## Limiti

- **Pagine dinamiche:** Skepsis legge l'HTML ricevuto dal server e non esegue JavaScript. Le pagine costruite via JavaScript possono dare pochi paragrafi, e le affermazioni risultano "Non trovata" anche quando la fonte è corretta (succede con alcune pagine NASA).
- **PDF e formati:** legge PDF testuali fino a 2 MB e 50 pagine; PDF scansiti, cifrati o con estrazione testuale scadente restano non leggibili. Non gestisce ancora EPUB, DOCX o OCR.
- **Reti e sorgenti:** per sicurezza accetta solo URL HTTP(S) pubblici; intranet, `localhost` e indirizzi privati sono esclusi. Una fonte raggiungibile ma non leggibile viene mostrata come tale nella dashboard.
- **Ricerca esterna:** è opt-in, richiede `TAVILY_API_KEY` e dipende dai risultati del motore di ricerca. La policy di dominio è deliberatamente conservativa, ma non dimostra da sola che una fonte sia pertinente o vera: il riscontro resta separato dal voto e richiede giudizio umano.
- **Estratti non deterministici:** la selezione con Jev sceglie candidati diversi da un'esecuzione all'altra, e questo può far oscillare di una stella il voto di uno stesso articolo (vedi Valutazione).
- **Regole semplici:** una sola affermazione giudicata falsa porta a 1★. Un errore di Jev su un fatto isolato può quindi abbassare molto il voto, e il motivo mostrato permette di accorgersene.
- **Estrazione a blocchi:** su articoli lunghi ogni blocco è estratto con poco contesto delle altre parti; un pronome o riferimento che punta a un blocco diverso può non essere risolto.
- **Costo e tempo:** la verifica per affermazione costa circa 10 volte il solo voto globale ed è circa 3 volte più lenta.
- **Connessione:** la pagina carica Bootstrap da CDN, quindi serve internet.
