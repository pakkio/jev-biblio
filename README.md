# 📚 Verificatore di Bibliografia e Affermazioni

Dashboard web locale che controlla se un articolo è davvero sostenuto dalle sue fonti:

- **verifica i link** della bibliografia e **scarica il testo delle fonti**;
- **estrae le affermazioni** dell'articolo con un LLM, separando fatti, opinioni ed esperienze personali;
- **verifica ogni fatto sul testo delle fonti con [Jev](https://typesafe.ai)** (TypeSafe *System One*): sostenuto, parzialmente sostenuto, esagerato, contraddetto o non trovato;
- **passa a un LLM che ragiona solo i casi in cui Jev è incerto**;
- **assegna un voto da 1 a 5 stelle** con Jev e **scrive una giustificazione in italiano**;
- **mostra tempi e costi** di ogni passaggio, con il totale in centesimi.

Una verifica completa costa in media circa **0,2 ¢** e richiede **10-40 secondi**, a seconda della lunghezza dell'articolo.

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
| 2. Estrazione | LLM (`gpt-6-luna`) | Divide l'articolo in affermazioni atomiche: **fatto**, **opinione** o **esperienza** in prima persona. Per ognuna indica le fonti e le parole chiave in italiano e inglese. |
| 3. Estratti | Python | Per ogni fatto sceglie i paragrafi delle fonti che condividono parole chiave e numeri, confrontati per valore: "4.700" corrisponde a "4.7k". |
| 4. Verifica | Jev, una chiamata per fatto, in parallelo | Decide se gli estratti sostengono l'affermazione e con quale confidenza. |
| 5. Casi incerti | LLM che ragiona (`gpt-6-luna-pro`) | Solo se la confidenza di Jev è sotto l'80%: rilegge affermazione ed estratti e decide, con una frase di motivazione. |
| 6. Voto | Jev | Valuta pertinenza, aderenza e stelle (domanda `Score`, che restituisce un valore atteso, es. 2,3/5), tenendo conto dei verdetti per affermazione. |
| 7. Giustificazione | LLM (`gpt-6-luna`) | Scrive 4-6 frasi in italiano senza cambiare i voti. |

Opinioni ed esperienze personali non vengono verificate e non abbassano il voto. Un'interpretazione presentata come conseguenza delle fonti ("questo dimostra che…") viene invece trattata come fatto da verificare: è lì che si nascondono gli articoli fuorvianti.

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

`eval/` contiene 21 articoli brevi su 7 argomenti (Apollo 11, Marie Curie, Torre di Pisa, Grande muraglia, fotosintesi, Python, telescopio Webb) con fonti reali. Ogni argomento ha una versione corretta (4★ attese), una fuorviante (2★: fatti veri, conclusioni distorte) e una falsa (1★).

```bash
uv run --no-project python eval/build_dataset.py   # rigenera eval/dataset.jsonl
uv run eval/run_eval.py --workers 4                 # verifica tutto e stampa le metriche
uv run eval/run_eval.py --only apollo11,curie       # solo alcuni argomenti
```

Risultati di settembre 2026, confrontati con la versione precedente (solo voto globale di Jev, senza leggere le fonti):

| | Solo voto globale | Verifica per affermazione |
|---|---|---|
| Stelle esatte | 62% | **71%** |
| Entro una stella | 100% | 100% |
| Buono / non buono corretto | 100% | 100% |
| Errore medio | 0,38 stelle | **0,29 stelle** |
| Tempo medio | **5 s** | 16,5 s |
| Costo medio | **0,019 ¢** | 0,196 ¢ |

**Come leggerli:** le etichette sono state assegnate da chi ha scritto gli articoli e i falsi sono per lo più noti (Aldrin primo sulla Luna, muraglia visibile dalla Luna), quindi Jev li riconosce anche senza leggere le fonti. Il vantaggio della verifica sulle fonti dovrebbe crescere su fatti poco noti, citazioni attribuite alla fonte sbagliata e numeri leggermente alterati, che questo set non contiene ancora. L'errore più frequente è un articolo fuorviante giudicato "molto falso" (3 casi su 7). Il passo successivo è aggiungere articoli reali etichettati da persone.

## Variabili d'ambiente

| Variabile | Predefinito | Descrizione |
|---|---|---|
| `TYPESAFE_API_KEY` | — | chiave TypeSafe (obbligatoria) |
| `OPENROUTER_API_KEY` | — | chiave OpenRouter |
| `OPENROUTER_MODEL` | `openai/gpt-6-luna` | modello per estrazione e giustificazione |
| `ESCALATION_MODEL` | `openai/gpt-6-luna-pro` | modello che ragiona sui casi incerti |
| `ESCALATION_THRESHOLD` | `0.8` | sotto questa confidenza di Jev il caso passa al LLM |
| `MAX_ESCALATIONS` | `8` | massimo di casi incerti passati al LLM per articolo |
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
     "verdict": "Sostenuta", "confidence": 1.0, "decided_by": "Jev", "reason": "", "excerpts": {"2": "…"}}
  ],
  "claim_counts": {"Sostenuta": 9, "Esagerata": 3, "Non trovata": 4},
  "relation": "Eccellente",
  "adherence": "Discrepanza parziale",
  "rating": "★★☆☆☆ Vero ma fuorviante",
  "rating_stars": 2,
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
- `eval/`: dataset etichettato e script di valutazione.
- `colab_main.py`: versione originale per Google Colab, che legge la chiave dai *Colab Secrets*.
- `samples/`: articoli e bibliografie di prova.

## Limiti

- **Pagine dinamiche:** si legge solo l'HTML statico. Le pagine costruite via JavaScript danno pochi paragrafi, e le affermazioni risultano "Non trovata" anche quando la fonte è corretta (succede con alcune pagine NASA).
- **Estratti:** la scelta dei paragrafi si basa su parole chiave, non sul significato, quindi a volte il passaggio giusto resta fuori.
- **Confidenza:** stelle e pertinenza hanno spesso una confidenza del 50-80%, mentre i verdetti sui singoli fatti sono di solito più netti.
- **Costo e tempo:** la verifica per affermazione costa circa 10 volte il solo voto globale ed è circa 3 volte più lenta.
- **Connessione:** la pagina carica Bootstrap da CDN, quindi serve internet.
