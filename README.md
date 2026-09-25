# 📚 Verificatore di Bibliografia e Affermazioni

Dashboard web locale che controlla se un articolo è sostenuto dalla sua bibliografia:

- **verifica i link** delle fonti (raggiungibili, 404, DNS inesistente);
- **valuta l'articolo con [Jev](https://typesafe.ai)** (TypeSafe *System One*): pertinenza delle fonti, aderenza delle affermazioni e un voto da 1 a 5 stelle, con probabilità calibrate;
- **scrive una giustificazione in italiano** con un LLM economico via [OpenRouter](https://openrouter.ai);
- **mostra tempi e costi** di ogni chiamata, con il totale in centesimi.

Una verifica completa costa circa **0,03 ¢** (circa $0,0003) e richiede circa **3-5 secondi**.

## Avvio rapido

Serve [uv](https://docs.astral.sh/uv/). Le dipendenze (`typesafe-sdk`, `python-dotenv`) sono dichiarate in testa a `main.py` e uv le installa da solo.

```bash
uv run main.py                      # apre http://127.0.0.1:8000/
uv run main.py --port 9000 --no-browser
```

Incolla il testo dell'articolo e la bibliografia (con gli URL), poi premi **Avvia analisi Jev e controllo link**.

## Chiavi API

Il programma legge le chiavi dall'ambiente oppure da `./.env` o `../.env`:

```dotenv
TYPESAFE_API_KEY=...      # obbligatoria: valutazione Jev
OPENROUTER_API_KEY=...    # facoltativa: giustificazione scritta
```

Senza `OPENROUTER_API_KEY` la dashboard funziona lo stesso e mostra solo il riepilogo generato dai giudizi di Jev.

## Scala di valutazione

| Stelle | Significato | Colore |
|---|---|---|
| ★☆☆☆☆ | **Molto falso**: affermazioni false o inventate, fonti inesistenti o contrarie | rosso |
| ★★☆☆☆ | **Vero ma fuorviante**: fatti veri presentati o forzati in modo ingannevole | giallo |
| ★★★☆☆ | **Buono**: fonti accettabili che sostengono le affermazioni principali | verde |
| ★★★★☆ | **Molto buono**: fonti affidabili, solo lacune minori | blu |
| ★★★★★ | **Ottimo**: fonti autorevoli che sostengono pienamente ogni affermazione | oro |

Da 3 stelle in su il risultato è considerato positivo (`rating_good: true`).

## Come funziona

1. **Controllo dei link**: estrae fino a 15 URL univoci dalla bibliografia e li interroga (timeout 5 s). Nella pulizia degli URL la `)` finale viene tolta solo se non chiude una `(` interna, così un link come `…/Python_(linguaggio)` resta intero.
2. **Jev**: una sola chiamata `system_one` con tre domande a scelta (pertinenza, aderenza, stelle) e quattro domande sì/no (fonti autorevoli, affermazioni non supportate, esagerazioni, fonti fuori tema). Jev restituisce giudizi e probabilità, non testo.
3. **Giustificazione**: il verdetto di Jev, l'articolo e la bibliografia vengono passati a un LLM su OpenRouter, che scrive 4-6 frasi in italiano senza cambiare i voti. Sotto il testo compare il riepilogo di Jev con le confidenze.

### Modelli OpenRouter

OpenRouter prova i modelli in quest'ordine, con il ragionamento nascosto disattivato:

1. `openai/gpt-6-luna` (oppure il modello indicato in `OPENROUTER_MODEL`)
2. il Gemini Flash Lite più recente con versione ≥ 3.5 (oggi `google/gemini-3.5-flash-lite`)
3. `openrouter/free`

La tabella *Tempi e costi* indica quale modello ha risposto davvero. Se OpenRouter fallisce, la riga mostra un badge rosso "errore" e la giustificazione si riduce al riepilogo di Jev.

Confronto su un articolo di prova (costi effettivi addebitati da OpenRouter, settembre 2026):

| Modello | Tempo | Costo | Note |
|---|---|---|---|
| `openai/gpt-6-luna` | ~2,6-3,2 s | ~0,019 ¢ | scelto: italiano migliore |
| `google/gemini-3.5-flash-lite` | ~1,7 s | ~0,065 ¢ | più veloce, 3,5× più caro |
| `deepseek/deepseek-v4-flash` | ~3,9 s | ~0,020 ¢ | buono, ripete i numeri del classificatore |
| `google/gemma-4-31b-it` | ~6,9 s | ~0,012 ¢ | il più economico, più lento |

### Costi

- **Jev**: $42 per miliardo di token di input (fonte: typesafe.ai); l'output è gratuito. Il prezzo si cambia con `JEV_PRICE_PER_M_INPUT` (USD per milione di token).
- **OpenRouter**: si usa il costo reale restituito da OpenRouter (`usage.cost`); se manca, viene calcolato dal listino del modello.

## Variabili d'ambiente

| Variabile | Predefinito | Descrizione |
|---|---|---|
| `TYPESAFE_API_KEY` | — | chiave TypeSafe (obbligatoria) |
| `OPENROUTER_API_KEY` | — | chiave OpenRouter (facoltativa) |
| `OPENROUTER_MODEL` | `openai/gpt-6-luna` | modello principale per la giustificazione |
| `JEV_PRICE_PER_M_INPUT` | `0.042` | prezzo Jev in USD per milione di token di input |

## API

Il server espone anche un endpoint JSON:

```bash
POST /api/verify
Content-Type: application/json

{"article": "testo dell'articolo…", "bibliography": "[1] … https://…"}
```

Risposta (abbreviata):

```json
{
  "links": [{"url": "https://…", "status": "200", "active": true}],
  "relation": "Eccellente",
  "adherence": "Grave allucinazione",
  "rating": "★★☆☆☆ Vero ma fuorviante",
  "rating_stars": 2,
  "rating_good": false,
  "explanation": "…",
  "metrics": {
    "jev": {"model": "jev-1.13.0", "seconds": 0.84, "input_tokens": 1671, "output_tokens": 233, "cost_cents": 0.0067},
    "openrouter": {"model": "openai/gpt-6-luna", "seconds": 3.19, "input_tokens": 812, "output_tokens": 213, "cost_cents": 0.0188},
    "total_seconds": 4.03,
    "total_cents": 0.0255
  }
}
```

In caso di errore la risposta è `{"error": "…"}`.

## Esempi

La cartella `samples/` contiene un articolo sul telescopio James Webb in tre versioni:

| File | Bibliografia | Risultato atteso |
|---|---|---|
| `jwst_article.txt` | `jwst_bibliography.txt` | ★ Molto falso (Big Bang "smentito", fonte inventata e irraggiungibile) |
| `jwst_article_misleading.txt` | `jwst_bibliography_clean.txt` | ★★ Vero ma fuorviante (CO₂ presentata come "segno di vita") |
| `jwst_article_clean.txt` | `jwst_bibliography_clean.txt` | ★★★★ Molto buono |

## File

- `main.py`: server locale, logica di verifica e pagina HTML (Bootstrap + Bootstrap Icons da CDN).
- `colab_main.py`: versione originale per Google Colab, che legge la chiave dai *Colab Secrets*.
- `samples/`: articoli e bibliografie di prova.

## Limiti

- Le stelle e la pertinenza a volte hanno una confidenza bassa (30-60%). Il giudizio sull'aderenza è di solito il più netto.
- Il controllo dei link verifica solo che la pagina risponda, non che contenga davvero ciò che l'articolo le attribuisce.
- La pagina carica Bootstrap da CDN, quindi serve una connessione a internet.
