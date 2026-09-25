# 📚 Verificatore di Bibliografia e Affermazioni

Dashboard web locale che controlla se un articolo è davvero sostenuto dalle sue fonti:

- **verifica i link** della bibliografia e **scarica il testo delle fonti**;
- **estrae le affermazioni** dell'articolo con un LLM, separando fatti, conclusioni dell'autore, opinioni ed esperienze personali;
- **verifica ogni affermazione sul testo delle fonti con [Jev](https://typesafe.ai)** (TypeSafe *System One*) e ne stima la plausibilità secondo la conoscenza generale;
- **passa a un LLM che ragiona solo i casi in cui Jev è incerto**;
- **assegna da 1 a 5 stelle con regole esplicite** sui verdetti, mostrando il motivo, e **scrive una giustificazione in italiano**;
- **mostra tempi e costi** di ogni passaggio, con il totale in centesimi.

Una verifica completa costa in media **0,1-0,2 ¢** e richiede **10-40 secondi**, a seconda della lunghezza dell'articolo. Sul set di test il voto è corretto in 18 articoli su 18 (vedi [Valutazione](#valutazione)).

## Avvio rapido

Serve [uv](https://docs.astral.sh/uv/). Le dipendenze (`typesafe-sdk`, `python-dotenv`) sono dichiarate in testa a `main.py` e uv le installa da solo.

```bash
uv run main.py                      # apre http://127.0.0.1:8000/
uv run main.py --port 9000 --no-browser
```

Incolla il testo dell'articolo e la bibliografia con gli URL, poi premi **Avvia analisi Jev e controllo link**. Nella bibliografia i riferimenti possono essere numerati (`[1] Titolo https://…`) e citati nel testo con `[1]`. Se l'articolo non usa i numeri, è il LLM ad associare ogni affermazione alle fonti pertinenti.

## Chiavi API

Il programma legge le chiavi dall'ambiente oppure da `./.env` o `../.env`:

```dotenv
TYPESAFE_API_KEY=...      # obbligatoria: verifiche e voto di Jev
OPENROUTER_API_KEY=...    # consigliata: estrazione delle affermazioni, casi incerti, giustificazione
```

Senza `OPENROUTER_API_KEY` la dashboard funziona in modalità ridotta: divide l'articolo per frasi invece di estrarre le affermazioni, non passa a un LLM i casi incerti e mostra solo il riepilogo di Jev.

## Come funziona

| Passaggio | Chi lo fa | Cosa succede |
|---|---|---|
| 1. Link e fonti | Python | Scarica in parallelo fino a 15 URL, con un tetto complessivo di 8 s, ed estrae i paragrafi di testo. |
| 2. Estrazione | LLM (`gpt-6-luna`), a blocchi in parallelo se l'articolo supera ~2.200 caratteri | Divide il testo in affermazioni atomiche: **fatto**, **conclusione** dell'autore ("quindi", "dimostra che"), **opinione** o **esperienza** in prima persona. Per ognuna indica le fonti e le parole chiave in italiano e inglese. Temperatura 0, per essere il più possibile ripetibile. |
| 3. Estratti | Python + Jev | Per ogni affermazione trova fino a 12 paragrafi candidati per fonte, per parole chiave e numeri confrontati per valore ("4.700" corrisponde a "4.7k"). Poi Jev, con una domanda sì/no per paragrafo in un'unica chiamata, sceglie quelli che servono davvero a confermare o smentire. |
| 4. Verifica | Jev, una chiamata per affermazione, in parallelo | Decide se gli estratti sostengono l'affermazione, con quale confidenza, quanto è plausibile secondo la conoscenza generale e — solo se l'affermazione contiene numeri (date, quantità, misure) — se quei numeri corrispondono a quelli delle fonti. |
| 5. Casi incerti | LLM che ragiona (`gpt-6-luna-pro`) | Solo se la confidenza di Jev è sotto l'80%: rilegge affermazione ed estratti e decide, con una frase di motivazione. |
| 6. Voto | regole + Jev | Le stelle si calcolano con le regole qui sotto. Jev dà anche pertinenza, aderenza e un voto globale (`Score`), mostrati come informazione e usati come riserva se l'articolo non contiene fatti verificabili. |
| 7. Giustificazione | LLM (`gpt-6-luna`) | Scrive 4-6 frasi in italiano senza cambiare i voti. |

Opinioni ed esperienze personali non vengono verificate e non abbassano il voto. Le conclusioni dell'autore ("questo dimostra che…") invece vengono verificate: è lì che si nascondono gli articoli fuorvianti.

### Controllo mirato sui numeri

Quando un'affermazione contiene un numero abbastanza specifico da poter essere alterato (non un piccolo intero come "5 strati"), Jev riceve una domanda in più nella stessa chiamata: "gli estratti contengono lo stesso numero, o uno diverso?". Nei casi che ho provato a mano (data del lancio spostata di un giorno, altezza cambiata, annegati in un paragrafo lungo e per il resto corretto) la domanda principale di Jev intercettava già l'alterazione da sola con "Contraddetta"; il controllo sui numeri non ha ancora cambiato un verdetto nei test automatici. Resta comunque nella dashboard come segnale in più, a costo quasi nullo, e come rete di sicurezza per i casi in cui Jev giudicasse "Parzialmente sostenuta" un fatto con un numero sbagliato — un errore che le regole di voto (sotto) tratterebbero come falso.

### Regole di voto

Le stelle non sono un giudizio complessivo "a sensazione": si calcolano dai verdetti sulle singole affermazioni, in quest'ordine, e la dashboard mostra la regola che ha deciso.

1. **1★ Molto falso**: un fatto di base è contraddetto dalle fonti e implausibile, oppure è chiaramente falso (plausibilità < 15%), oppure un'affermazione implausibile è citata solo da fonti irraggiungibili (citazione inventata).
2. **2★ Vero ma fuorviante**: i fatti reggono, ma una conclusione dell'autore è esagerata, contraddetta o implausibile, oppure un fatto implausibile distorce la fonte.
3. **5★ Ottimo**: almeno 3 affermazioni, tutte pienamente sostenute, e fonti non solo enciclopediche.
4. **4★ Molto buono**: almeno il 60% delle affermazioni confermato dalle fonti. Un articolo basato solo su Wikipedia arriva al massimo a 4★.
5. **3★ Buono**: negli altri casi.

La separazione tra premesse e conclusioni è ciò che distingue 1★ da 2★: il voto globale di Jev, da solo, dava 1★ a quasi tutti gli articoli fuorvianti.

### Verdetti per affermazione

| Verdetto | Significato |
|---|---|
| Sostenuta | gli estratti dicono la stessa cosa |
| Parzialmente sostenuta | il nucleo è confermato, alcuni dettagli non compaiono negli estratti |
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
- **Test** (`test_dataset.jsonl`, 18 articoli): Everest, penicillina, Galileo, Titanic, Colosseo, DNA. Scritto dopo aver fissato le regole e usato una sola volta. Metà degli articoli non ha marcatori `[n]`.

```bash
uv run --no-project python eval/build_dataset.py                   # rigenera i due set
uv run eval/run_eval.py --workers 4                                 # set di sviluppo
uv run eval/run_eval.py --dataset test_dataset.jsonl --workers 4    # set di test
uv run eval/run_eval.py --only apollo11,curie                       # solo alcuni argomenti
```

Risultati di settembre 2026:

| | Voto globale Jev (v1) | Per affermazione, voto Jev (v2) | Per affermazione + regole (v3) | v3 + estratti Jev, estrazione a blocchi, controllo numeri (v4) |
|---|---|---|---|---|
| Stelle esatte, sviluppo | 62% | 71% | 95% | 90-95%* |
| Stelle esatte, **test** | — | — | 100% | 100% |
| Tempo medio, sviluppo | 5 s | 16,5 s | 14,4 s | 17,1 s |
| Tempo medio, **articolo lungo** (~9.000 caratteri) | — | — | 20,8 s (estrazione) | **11,6 s** (estrazione, -44%) |
| Costo medio, sviluppo | 0,019 ¢ | 0,196 ¢ | 0,202 ¢ | 0,225 ¢ |

*\*Ho visto oscillare lo stesso articolo tra 3★ e 4★ da un'esecuzione all'altra (Grande muraglia, versione corretta): la selezione degli estratti fatta da Jev non è deterministica, quindi a volte sceglie un paragrafo diverso e il verdetto cambia. Non è una regressione di questa versione — l'ho verificato rilanciando lo stesso articolo tre volte.*

**Come leggerli:**
- **Variabilità:** oltre al caso sopra, gli articoli fuorvianti al confine tra 1★ e 2★ possono cambiare risultato da un'esecuzione all'altra (es. Galileo fuorviante: 2★, 1★, 1★ su tre run). L'estrazione del LLM a volte marca come "fatto" una parte di una conclusione.
- **Chi ha scritto le etichette:** entrambi i set sono stati scritti ed etichettati da chi ha sviluppato le regole, con tre categorie nette. Il test su articoli mai visti riduce il rischio di aver adattato le regole ai dati, ma non lo elimina.
- **Margine statistico:** con 18 articoli, un 100% è compatibile con una precisione reale intorno all'85-90%.
- **Articoli reali:** quelli veri sono più sfumati (errori isolati in articoli buoni, opinioni mescolate ai fatti) e il confine tra 3★, 4★ e 5★ è meno netto di quello tra falso e fuorviante.
- **Prossimo passo:** raccogliere articoli reali etichettati da persone.

## Variabili d'ambiente

| Variabile | Predefinito | Descrizione |
|---|---|---|
| `TYPESAFE_API_KEY` | — | chiave TypeSafe (obbligatoria) |
| `OPENROUTER_API_KEY` | — | chiave OpenRouter |
| `OPENROUTER_MODEL` | `openai/gpt-6-luna` | modello per estrazione e giustificazione |
| `ESCALATION_MODEL` | `openai/gpt-6-luna-pro` | modello che ragiona sui casi incerti |
| `ESCALATION_THRESHOLD` | `0.8` | sotto questa confidenza di Jev il caso passa al LLM |

| `MAX_ESCALATIONS` | `8` | massimo di casi incerti passati al LLM per articolo |
| `EXCERPT_SELECTION` | `jev` | `jev`: Jev sceglie i paragrafi pertinenti; `keywords`: solo parole chiave, più economico |
| `JEV_PRICE_PER_M_INPUT` | `0.042` | prezzo Jev in USD per milione di token di input |

Se il modello scelto su OpenRouter non risponde, si passa al Gemini Flash Lite più recente con versione ≥ 3.5 e poi a `openrouter/free`. La tabella *Tempi e costi* mostra quale modello ha risposto davvero.

### Costi

- **Jev**: $42 per miliardo di token di input (fonte: typesafe.ai); l'output è gratuito.
- **OpenRouter**: si usa il costo reale restituito da OpenRouter (`usage.cost`); se manca, viene calcolato dal listino del modello.

Nella verifica per affermazione la voce più costosa sono i casi incerti passati al LLM che ragiona. Alzare o abbassare `ESCALATION_THRESHOLD` sposta il compromesso tra costo e precisione.

## API

```bash
POST /api/verify
Content-Type: application/json

{"article": "testo dell'articolo…", "bibliography": "[1] Titolo https://…"}
```

Risposta (abbreviata):

```json
{
  "links": [{"url": "https://…", "status": "200", "active": true}],
  "claims": [
    {"text": "Lo specchio primario di Webb ha un diametro di 6,5 metri.", "kind": "fatto", "refs": ["2"],
     "verdict": "Sostenuta", "confidence": 1.0, "world": 0.98, "decided_by": "Jev", "reason": "", "excerpts": {"2": "…"}}
  ],
  "claim_counts": {"Sostenuta": 9, "Esagerata": 3, "Non trovata": 4},
  "relation": "Eccellente",
  "adherence": "Discrepanza parziale",
  "rating": "★★☆☆☆ Vero ma fuorviante",
  "rating_stars": 2,
  "rating_reason": "conclusione non giustificata dalle fonti: «Il rilevamento di anidride carbonica … significa che Webb ha trovato un forte indizio di vita»",
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
- `llm.py`: chiamate a OpenRouter con catena di riserva, tempi e costi.
- `eval/`: set di sviluppo e di test etichettati, script di valutazione.
- `colab_main.py`: versione originale per Google Colab, che legge la chiave dai *Colab Secrets*.
- `samples/`: articoli e bibliografie di prova.

## Limiti

- **Pagine dinamiche:** si legge solo l'HTML statico. Le pagine costruite via JavaScript danno pochi paragrafi, e le affermazioni risultano "Non trovata" anche quando la fonte è corretta (succede con alcune pagine NASA).
- **Estratti non deterministici:** la selezione con Jev sceglie candidati diversi da un'esecuzione all'altra, e questo può far oscillare di una stella il voto di uno stesso articolo (vedi Valutazione).
- **Regole semplici:** una sola affermazione giudicata falsa porta a 1★. Un errore di Jev su un fatto isolato può quindi abbassare molto il voto, e il motivo mostrato permette di accorgersene.
- **Estrazione a blocchi:** su articoli lunghi ogni blocco è estratto con poco contesto delle altre parti; un pronome o riferimento che punta a un blocco diverso può non essere risolto.
- **Costo e tempo:** la verifica per affermazione costa circa 10 volte il solo voto globale ed è circa 3 volte più lenta.
- **Connessione:** la pagina carica Bootstrap da CDN, quindi serve internet.
