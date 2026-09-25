# /// script
# requires-python = ">=3.10"
# dependencies = ["typesafe-sdk", "python-dotenv"]
# ///
"""
📊 Verificatore di Bibliografia e Affermazioni (versione locale).

Uso:
    uv run main.py [--port 8000] [--no-browser]

Legge TYPESAFE_API_KEY e OPENROUTER_API_KEY dall'ambiente, da ../.env o da ./.env.
Variabili opzionali: OPENROUTER_MODEL (modello principale, predefinito openai/gpt-6-luna), JEV_PRICE_PER_M_INPUT (USD per milione di token).
"""
import argparse
import json
import os
import re
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError, HTTPError

from dotenv import load_dotenv
from typesafe_sdk import Choice, Noul, TypeSafeClient

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")
load_dotenv(HERE.parent / ".env")


# 1. Logica principale del verificatore, chiamata dal frontend
def run_bibliography_verifier(article_text, bibliography_raw):
    try:
        api_key = os.getenv('TYPESAFE_API_KEY')
        if not api_key:
            return json.dumps({"error": "TYPESAFE_API_KEY non trovata nell'ambiente o nel file .env."})

        # Estrae gli URL univoci dalla bibliografia incollata
        urls = re.findall(r'https?://[^\s<>"\x7f-\xff]+', bibliography_raw)
        link_statuses = []

        # Controlla fino a 15 URL per verificare che siano raggiungibili (non 404)
        for clean_url in list(dict.fromkeys(_clean_url(u) for u in urls))[:15]:
            status_code = "N/D"
            is_active = False
            try:
                req = urllib.request.Request(
                    clean_url,
                    headers={'User-Agent': 'Mozilla/5.0 (Windows; Intel Mac OS X)'}
                )
                with urllib.request.urlopen(req, timeout=5) as response:
                    status_code = str(response.getcode())
                    is_active = True
            except HTTPError as e:
                status_code = str(e.code)
            except URLError as e:
                status_code = "DNS / Irraggiungibile"
            except Exception as e:
                status_code = "Errore"

            link_statuses.append({
                "url": clean_url,
                "status": status_code,
                "active": is_active
            })

        # Usa Jev per valutare pertinenza delle fonti, aderenza delle affermazioni e qualità complessiva
        client = TypeSafeClient(api_key=api_key)
        state_data = "CONTENUTO DELL'ARTICOLO:\n" + str(article_text) + "\n\nBIBLIOGRAFIA/FONTI:\n" + str(bibliography_raw)

        jev_start = time.perf_counter()
        response = client.system_one(
            state=state_data,
            model="jev-latest",
            questions={
                "relation_rating": Choice(
                    instructions="Le fonti della bibliografia sono pertinenti e coerenti con la tesi principale dell'articolo?",
                    criteria={
                        "Eccellente": "Le fonti sono molto pertinenti, autorevoli e sostengono direttamente il tema centrale.",
                        "Discreta": "Le fonti sono solo marginalmente collegate al tema, poco integrate o superficiali.",
                        "Scarsa": "Le fonti sono irrilevanti, non corrispondenti o non sostengono il contesto dell'articolo."
                    }
                ),
                "claim_adherence": Choice(
                    instructions="Con quanta accuratezza l'articolo rispetta e rappresenta ciò che affermano le fonti citate?",
                    criteria={
                        "Aderenza forte": "L'articolo riporta correttamente le fonti, senza esagerazioni né invenzioni.",
                        "Discrepanza parziale": "Alcune citazioni sono forzate, esagerate o leggermente disallineate rispetto alle fonti.",
                        "Grave allucinazione": "L'articolo travisa fatti essenziali o cita fonti che non sostengono le affermazioni corrispondenti."
                    }
                ),
                "overall_score": Choice(
                    instructions="Valuta la qualità e l'affidabilità di questa bibliografia da 1 a 5 stelle.",
                    criteria={
                        "5 stelle": "Ottimo: fonti autorevoli e verificate che sostengono pienamente ogni affermazione, con pertinenza eccezionale.",
                        "4 stelle": "Molto buono: fonti affidabili che sostengono le affermazioni, con solo lacune o scollamenti minori.",
                        "3 stelle": "Buono: fonti accettabili che sostengono le affermazioni principali, anche se alcune citazioni sono poco approfondite o solide.",
                        "2 stelle": "Vero ma fuorviante: le affermazioni sono tecnicamente vere o in parte supportate dalle fonti, ma presentate, selezionate o forzate in modo da ingannare il lettore.",
                        "1 stella": "Molto falso: le affermazioni principali sono false o inventate, oppure le fonti citate le contraddicono, non esistono o non c'entrano."
                    }
                ),
                # Jev restituisce giudizi, non testo: questi segnali sì/no alimentano
                # la giustificazione scritta (OpenRouter) e il riepilogo di riserva.
                **{key: Noul(instructions=text) for key, text in SIGNALS.items()}
            }
        )

        jev_seconds = time.perf_counter() - jev_start
        jev_usage = response.usage
        jev_metrics = {
            "model": response.model,
            "seconds": round(jev_seconds, 3),
            "input_tokens": jev_usage.input_tokens,
            "output_tokens": jev_usage.output_tokens,
            # Jev fattura solo i token di input; l'output è gratuito
            "cost_cents": jev_usage.input_tokens * JEV_PRICE_PER_M_INPUT / 1e6 * 100,
        }

        relation = response.answers["relation_rating"]
        adherence = response.answers["claim_adherence"]
        rating = response.answers["overall_score"]
        verdict = _build_explanation(
            relation, adherence, rating,
            {key: response.answers[key].noul for key in SIGNALS},
            link_statuses,
        )
        try:
            llm = _llm_justification(article_text, bibliography_raw, verdict)
            explanation = f"{llm['text']}\n\n[Jev] {verdict}"
            llm_metrics = {k: v for k, v in llm.items() if k != "text"}
        except Exception as e:
            print(f"[AVVISO] Giustificazione OpenRouter non riuscita, uso il riepilogo di Jev: {e}")
            explanation = verdict
            llm_metrics = {"model": _pick_openrouter_model(), "seconds": None, "input_tokens": 0,
                           "output_tokens": 0, "cost_cents": 0.0, "error": str(e)}
        result = {
            "links": link_statuses,
            "relation": relation.choice,
            "adherence": adherence.choice,
            "rating": _stars_label(rating.choice),
            "rating_stars": _star_count(rating.choice),
            "rating_good": _star_count(rating.choice) >= 3,
            "explanation": explanation,
            "metrics": {
                "jev": jev_metrics,
                "openrouter": llm_metrics,
                "total_seconds": round(jev_metrics["seconds"] + (llm_metrics["seconds"] or 0), 3),
                "total_cents": jev_metrics["cost_cents"] + llm_metrics["cost_cents"],
            }
        }
        return json.dumps(result)

    except Exception as e:
        return json.dumps({"error": str(e)})


# Controlli sì/no le cui probabilità sostengono la spiegazione testuale
SIGNALS = {
    "authoritative": "Le fonti citate sono autorevoli (articoli peer-reviewed, documentazione ufficiale, editori affidabili).",
    "unsupported_claims": "L'articolo contiene affermazioni fattuali che nessuna delle fonti citate sembra sostenere.",
    "exaggeration": "L'articolo esagera o amplifica ciò che dicono le sue fonti.",
    "off_topic_sources": "Alcune fonti citate non c'entrano con l'argomento dell'articolo.",
}

SIGNAL_TEXT = {
    "authoritative": ("Le fonti sembrano autorevoli", "Le fonti non sembrano autorevoli"),
    "unsupported_claims": ("alcune affermazioni non sembrano supportate dalle fonti", "le affermazioni sembrano supportate dalle fonti"),
    "exaggeration": ("l'articolo sembra esagerare le fonti", "nessuna evidente esagerazione delle fonti"),
    "off_topic_sources": ("alcune fonti sembrano fuori tema", "le fonti restano in tema"),
}


# Prezzo Jev: $42 per miliardo di token di input (typesafe.ai), output gratuito
JEV_PRICE_PER_M_INPUT = float(os.getenv("JEV_PRICE_PER_M_INPUT", "0.042"))

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
# Modello principale: economico, veloce e ottimo in italiano (usato con ragionamento disattivato)
OPENROUTER_PRIMARY = "openai/gpt-6-luna"
# Ultima riserva se non risponde nessun altro modello
OPENROUTER_FALLBACK = "openrouter/free"
MIN_FLASH_LITE_VERSION = (3, 5)
_openrouter_chain = None
_openrouter_pricing = {}


def _pick_openrouter_model():
    """Modello principale della catena (quello che dovrebbe rispondere)."""
    return _openrouter_models()[0]


def _openrouter_models():
    """Catena di modelli: principale (OPENROUTER_MODEL o gpt-6-luna),
    poi il Gemini Flash Lite più recente con versione >= 3.5, infine openrouter/free."""
    global _openrouter_chain
    if _openrouter_chain:
        return _openrouter_chain
    primary = os.getenv("OPENROUTER_MODEL", OPENROUTER_PRIMARY)
    try:
        with urllib.request.urlopen(OPENROUTER_MODELS_URL, timeout=15) as response:
            models = json.loads(response.read())["data"]
        for m in models:
            _openrouter_pricing[m["id"]] = m.get("pricing", {})
        candidates = []
        for m in models:
            match = re.fullmatch(r"google/gemini-(\d+)\.(\d+)-flash-lite", m["id"])
            if match and (int(match[1]), int(match[2])) >= MIN_FLASH_LITE_VERSION:
                candidates.append(((int(match[1]), int(match[2])), m["id"]))
        flash_lite = max(candidates)[1] if candidates else None
    except Exception as e:
        print(f"[AVVISO] Impossibile leggere l'elenco modelli di OpenRouter: {e}")
        flash_lite = None
    chain = [primary, flash_lite, OPENROUTER_FALLBACK]
    _openrouter_chain = [m for i, m in enumerate(chain) if m and m not in chain[:i]]
    return _openrouter_chain


def _openrouter_cost_cents(model, usage):
    """Costo in centesimi: usa usage.cost di OpenRouter, altrimenti lo calcola dal listino."""
    if usage.get("cost") is not None:
        return float(usage["cost"]) * 100
    pricing = _openrouter_pricing.get(model, {})
    usd = (usage.get("prompt_tokens", 0) * float(pricing.get("prompt", 0))
           + usage.get("completion_tokens", 0) * float(pricing.get("completion", 0)))
    return usd * 100


def _llm_justification(article_text, bibliography_raw, verdict):
    """Chiede a un modello OpenRouter di scrivere la giustificazione del verdetto di Jev."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY non impostata")
    models = _openrouter_models()
    model = models[0]
    prompt = (
        "Stai revisionando la bibliografia di un articolo. Un classificatore ha già prodotto questo verdetto:\n"
        f"{verdict}\n\n"
        "Scrivi in italiano una giustificazione concisa (4-6 frasi, testo semplice, niente markdown) che spieghi il verdetto. "
        "Cita affermazioni specifiche dell'articolo e fonti specifiche, indica punti di forza e debolezze "
        "e menziona i link irraggiungibili se rilevanti. Non modificare le valutazioni.\n\n"
        f"ARTICOLO:\n{article_text}\n\nBIBLIOGRAFIA:\n{bibliography_raw}"
    )
    req = urllib.request.Request(
        OPENROUTER_URL,
        data=json.dumps({
            "model": model,
            # se il modello principale non risponde, OpenRouter prova i successivi
            "models": models,
            # niente ragionamento nascosto: risposte più rapide ed economiche
            "reasoning": {"enabled": False},
            "usage": {"include": True},
            "messages": [
                {"role": "system", "content": "Rispondi sempre e solo in italiano."},
                {"role": "user", "content": prompt},
            ],
        }).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-Title": "Verificatore Bibliografia",
        },
    )
    start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=90) as response:
        data = json.loads(response.read())
    seconds = time.perf_counter() - start
    text = (data["choices"][0]["message"].get("content") or "").strip()
    if not text:
        raise RuntimeError("risposta vuota dal modello")
    used_model = data.get("model", model)
    usage = data.get("usage") or {}
    return {
        "text": text,
        "model": used_model,
        "seconds": round(seconds, 3),
        "input_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
        "cost_cents": _openrouter_cost_cents(used_model, usage),
    }


# Significato delle stelle: 1-2 non buono (1 molto falso, 2 vero ma fuorviante), 3 buono, 4 molto buono, 5 ottimo
STAR_MEANING = {1: "Molto falso", 2: "Vero ma fuorviante", 3: "Buono", 4: "Molto buono", 5: "Ottimo"}


def _star_count(choice):
    return int(choice.split()[0])


def _stars_label(choice):
    n = _star_count(choice)
    return "★" * n + "☆" * (5 - n) + f" {STAR_MEANING[n]}"


def _clean_url(url):
    """Rimuove la punteggiatura finale, mantenendo una ')' che chiude una '(' interna all'URL."""
    url = url.strip()
    while url and url[-1] in '.,;:!?]}"\'':
        url = url[:-1]
        url = url.strip()
    while url.endswith(')') and url.count(')') > url.count('('):
        url = url[:-1].rstrip('.,;:!?')
    return url


def _build_explanation(relation, adherence, rating, signals, link_statuses):
    """Compone una motivazione leggibile a partire dalle risposte calibrate di Jev."""
    parts = [
        f"Pertinenza: {relation.choice} (confidenza {relation.confidence:.0%}). "
        f"Aderenza alle fonti: {adherence.choice} ({adherence.confidence:.0%}). "
        f"Complessivo: {_stars_label(rating.choice)} ({rating.confidence:.0%})."
    ]
    findings = []
    for key, prob in signals.items():
        yes_text, no_text = SIGNAL_TEXT[key]
        if prob >= 0.6:
            findings.append(f"{yes_text} (p={prob:.2f})")
        elif prob <= 0.4:
            findings.append(f"{no_text} (p={1 - prob:.2f})")
        else:
            findings.append(f"incerto se {yes_text.lower()} (p={prob:.2f})")
    parts.append("Segnali: " + "; ".join(findings) + ".")
    dead = [l["url"] for l in link_statuses if not l["active"]]
    if link_statuses:
        parts.append(f"Link: {len(link_statuses) - len(dead)}/{len(link_statuses)} raggiungibili.")
    if dead:
        parts.append("Irraggiungibili: " + ", ".join(dead) + ".")
    return " ".join(parts)

# 2. Pagina HTML servita su /
html_code = """
<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="UTF-8">
    <title>Verificatore Bibliografia</title>
    <!-- Bootstrap CSS -->
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css" rel="stylesheet">
    <style>
        body {
            background-color: #f4f6f8;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            color: #333;
            padding: 10px;
        }
        .dashboard-container {
            max-width: 1200px;
            margin: 0 auto;
        }
        .card {
            background-color: #ffffff;
            border: none;
            border-radius: 8px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.05);
            margin-bottom: 20px;
        }
        .card-header {
            background-color: #fff;
            border-bottom: 1px solid #f0f0f0;
            font-weight: 600;
            color: #4f46e5;
        }
        .kpi-card {
            text-align: center;
            padding: 20px;
        }
        .kpi-val {
            font-size: 1.8rem;
            font-weight: 700;
            color: #1e1b4b;
        }
        /* Livelli di stelle: 1 rosso, 2 giallo, 3 verde, 4 blu, 5 oro */
        .rating-1 { color: #dc2626; }
        .rating-2 { color: #eab308; }
        .rating-3 { color: #16a34a; }
        .rating-4 { color: #2563eb; }
        .rating-5 { color: #d4a017; text-shadow: 0 0 8px rgba(212, 160, 23, 0.45); }
        .kpi-val .stars { font-size: 1.4rem; letter-spacing: 2px; }
        .kpi-val .stars .bi-star { opacity: 0.35; }
        .kpi-val .meaning { font-size: 1rem; font-weight: 600; }
        .rating-legend { font-size: 0.72rem; line-height: 1.6; }
        .rating-legend span { white-space: nowrap; margin-right: 6px; }
        .metrics-table td, .metrics-table th { font-size: 0.85rem; }
        .metrics-table .total-row td { font-weight: 700; border-top: 2px solid #e5e7eb; }
        .kpi-title {
            font-size: 0.85rem;
            text-transform: uppercase;
            color: #6b7280;
            letter-spacing: 0.05em;
            margin-bottom: 5px;
        }
    </style>
</head>
<body>
    <div class="dashboard-container">
        <h2 class="mb-4" style="color: #1e1b4b; font-weight: 700;">📚 Verificatore di Bibliografia e Affermazioni</h2>

        <!-- Input -->
        <div class="card p-4">
            <div class="row">
                <div class="col-md-6 mb-3">
                    <label class="form-label fw-bold">Testo dell'articolo e affermazioni</label>
                    <textarea id="article-input" class="form-control" rows="5" placeholder="Incolla qui il testo o le affermazioni dell'articolo...">Il calcolo quantistico promette di rivoluzionare la chimica simulando molecole complesse. Uno studio del 2021 di ricercatori IBM ha dimostrato la supremazia quantistica in questo campo, sostenendo che un processore da 50 qubit ha calcolato le energie dello stato fondamentale delle molecole con una fedeltà del 99,9%.</textarea>
                </div>
                <div class="col-md-6 mb-3">
                    <label class="form-label fw-bold">Riferimenti bibliografici</label>
                    <textarea id="bib-input" class="form-control" rows="5" placeholder="Incolla i riferimenti e le fonti della bibliografia con gli URL...">- Watson, H. Simulare molecole su hardware classico. https://httpbin.org/status/200
- Smith, J. Simulazioni quantistiche in chimica (2021). Nature. https://httpbin.org/status/404
- Miller, A. Affermazioni false sul calcolo quantistico. (2022). https://httpbin.org/status/500</textarea>
                </div>
            </div>
            <button class="btn btn-primary mt-2" id="btn-run" style="background-color:#4f46e5; border:none;">Avvia analisi Jev e controllo link</button>
        </div>

        <!-- Indicatore di caricamento -->
        <div id="loader" class="alert alert-info d-none" role="alert">
            🔄 Controllo degli URL della bibliografia e valutazione con Jev in corso... Attendere (circa 15-30 secondi).
        </div>

        <!-- Griglia dei risultati -->
        <div id="dashboard-results" class="d-none">
            <div class="row mb-3">
                <div class="col-md-3">
                    <div class="card kpi-card">
                        <div class="kpi-title">Riferimenti totali</div>
                        <div class="kpi-val" id="kpi-total">0</div>
                    </div>
                </div>
                <div class="col-md-3">
                    <div class="card kpi-card">
                        <div class="kpi-title">Link attivi (non 404)</div>
                        <div class="kpi-val text-success" id="kpi-active">0</div>
                    </div>
                </div>
                <div class="col-md-3">
                    <div class="card kpi-card">
                        <div class="kpi-title">Aderenza alle fonti</div>
                        <div class="kpi-val text-primary" id="kpi-adherence">-</div>
                    </div>
                </div>
                <div class="col-md-3">
                    <div class="card kpi-card">
                        <div class="kpi-title">Qualità della bibliografia</div>
                        <div class="kpi-val" id="kpi-rating">-</div>
                        <div class="rating-legend mt-1">
                            <span class="rating-1"><i class="bi bi-x-octagon-fill"></i> 1 molto falso</span>
                            <span class="rating-2"><i class="bi bi-exclamation-triangle-fill"></i> 2 fuorviante</span>
                            <span class="rating-3"><i class="bi bi-check-circle-fill"></i> 3 buono</span>
                            <span class="rating-4"><i class="bi bi-hand-thumbs-up-fill"></i> 4 molto buono</span>
                            <span class="rating-5"><i class="bi bi-trophy-fill"></i> 5 ottimo</span>
                        </div>
                    </div>
                </div>
            </div>

            <div class="row">
                <!-- Colonna sinistra: tabella dei link verificati -->
                <div class="col-md-6">
                    <div class="card">
                        <div class="card-header">Verifica degli URL</div>
                        <div class="card-body">
                            <div class="table-responsive">
                                <table class="table table-sm align-middle">
                                    <thead>
                                        <tr>
                                            <th>URL della fonte</th>
                                            <th>Stato HTTP</th>
                                            <th>Attivo</th>
                                        </tr>
                                    </thead>
                                    <tbody id="links-table-body"></tbody>
                                </table>
                             </div>
                        </div>
                    </div>
                </div>

                <!-- Colonna destra: valutazione di Jev e giustificazione -->
                <div class="col-md-6">
                    <div class="card">
                        <div class="card-header">Valutazione semantica di Jev</div>
                        <div class="card-body">
                            <div class="mb-3">
                                <strong>Pertinenza della bibliografia:</strong> <span class="badge bg-info" id="eval-relation">-</span>
                            </div>
                            <div class="mb-3">
                                <strong>Giustificazione:</strong>
                                <p class="mt-1 text-muted" id="eval-explanation" style="font-size:0.92rem; line-height:1.5; white-space:pre-line;"></p>
                            </div>
                        </div>
                    </div>
                    <div class="card">
                        <div class="card-header"><i class="bi bi-stopwatch"></i> Tempi e costi</div>
                        <div class="card-body">
                            <table class="table table-sm align-middle mb-0 metrics-table">
                                <thead>
                                    <tr>
                                        <th>Servizio</th>
                                        <th>Modello</th>
                                        <th class="text-end">Tempo</th>
                                        <th class="text-end">Token (in/out)</th>
                                        <th class="text-end">Costo</th>
                                    </tr>
                                </thead>
                                <tbody id="metrics-table-body"></tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        window.onerror = function(message) {
            fetch('/api/js-error', {method: 'POST', body: JSON.stringify({message: String(message)})});
        };

        const RATING_LEVELS = {
            1: {icon: 'bi-x-octagon-fill', label: 'Molto falso'},
            2: {icon: 'bi-exclamation-triangle-fill', label: 'Vero ma fuorviante'},
            3: {icon: 'bi-check-circle-fill', label: 'Buono'},
            4: {icon: 'bi-hand-thumbs-up-fill', label: 'Molto buono'},
            5: {icon: 'bi-trophy-fill', label: 'Ottimo'}
        };

        function formatCents(c) {
            if (c === null || c === undefined) return '-';
            if (c === 0) return '0 ¢';
            const digits = c < 0.01 ? {maximumSignificantDigits: 2} : {minimumFractionDigits: 2, maximumFractionDigits: 4};
            return c.toLocaleString('it-IT', digits) + ' ¢';
        }

        function formatSeconds(s) {
            return (s === null || s === undefined) ? '-' : s.toLocaleString('it-IT', {minimumFractionDigits: 2, maximumFractionDigits: 2}) + ' s';
        }

        function renderMetrics(m) {
            const body = document.getElementById('metrics-table-body');
            body.innerHTML = '';
            if (!m) return;
            const row = (name, x) => {
                const tr = document.createElement('tr');
                const err = x.error ? ` <span class="badge bg-danger" title="${x.error.replace(/"/g, '&quot;')}">errore</span>` : '';
                tr.innerHTML = `<td>${name}${err}</td><td><code>${x.model || '-'}</code></td>`
                    + `<td class="text-end">${formatSeconds(x.seconds)}</td>`
                    + `<td class="text-end">${(x.input_tokens || 0).toLocaleString('it-IT')} / ${(x.output_tokens || 0).toLocaleString('it-IT')}</td>`
                    + `<td class="text-end">${formatCents(x.cost_cents)}</td>`;
                body.appendChild(tr);
            };
            row('Jev', m.jev);
            row('OpenRouter', m.openrouter);
            const total = document.createElement('tr');
            total.className = 'total-row';
            total.innerHTML = `<td colspan="2">Totale</td><td class="text-end">${formatSeconds(m.total_seconds)}</td>`
                + `<td></td><td class="text-end">${formatCents(m.total_cents)}</td>`;
            body.appendChild(total);
        }

        function renderRating(stars, fallbackText) {
            const el = document.getElementById('kpi-rating');
            el.className = 'kpi-val';
            const level = RATING_LEVELS[stars];
            if (!level) {
                el.textContent = fallbackText || '-';
                return;
            }
            let starsHtml = '';
            for (let i = 1; i <= 5; i++) {
                starsHtml += `<i class="bi ${i <= stars ? 'bi-star-fill' : 'bi-star'}"></i>`;
            }
            el.classList.add(`rating-${stars}`);
            el.innerHTML = `<div class="stars">${starsHtml}</div>`
                + `<div class="meaning"><i class="bi ${level.icon}"></i> ${level.label}</div>`;
        }

        document.getElementById('btn-run').addEventListener('click', function() {
            const articleText = document.getElementById('article-input').value;
            const bibText = document.getElementById('bib-input').value;

            if(!articleText || !bibText) {
                alert("Inserisci sia il testo dell'articolo sia i riferimenti bibliografici.");
                return;
            }

            document.getElementById('loader').classList.remove('d-none');
            document.getElementById('dashboard-results').classList.add('d-none');

            fetch('/api/verify', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({article: articleText, bibliography: bibText})
            })
                .then(response => response.text())
                .then(body => {
                    document.getElementById('loader').classList.add('d-none');

                    let data;
                    try {
                        if (!body) {
                            throw new Error("Risposta vuota dal server.");
                        }
                        data = JSON.parse(body);
                    } catch(e) {
                        alert("Errore nella lettura della risposta: " + e.message);
                        return;
                    }

                    if(data.error) {
                        alert("Errore durante la verifica: " + data.error);
                        return;
                    }

                    // Riempie le schede di riepilogo
                    const totalLinks = (data.links || []).length;
                    const activeLinks = (data.links || []).filter(l => l.active).length;
                    document.getElementById('kpi-total').textContent = totalLinks;
                    document.getElementById('kpi-active').textContent = activeLinks + " / " + totalLinks;
                    document.getElementById('kpi-adherence').textContent = data.adherence || '-';
                    renderRating(data.rating_stars, data.rating);

                    // Riempie la tabella dei link
                    const tableBody = document.getElementById('links-table-body');
                    tableBody.innerHTML = '';
                    (data.links || []).forEach(link => {
                        const row = document.createElement('tr');
                        const badgeClass = link.active ? 'bg-success' : 'bg-danger';
                        row.innerHTML = `
                            <td><a href="${link.url}" target="_blank" class="text-truncate d-inline-block" style="max-width:280px;">${link.url}</a></td>
                            <td><code>${link.status}</code></td>
                            <td><span class="badge ${badgeClass}">${link.active ? 'ATTIVO' : 'NON ATTIVO'}</span></td>
                        `;
                        tableBody.appendChild(row);
                    });

                    // Riempie la valutazione semantica
                    document.getElementById('eval-relation').textContent = data.relation || '-';
                    document.getElementById('eval-explanation').textContent = data.explanation || '-';
                    renderMetrics(data.metrics);

                    document.getElementById('dashboard-results').classList.remove('d-none');
                }).catch(err => {
                    document.getElementById('loader').classList.add('d-none');
                    alert("Operazione non riuscita: " + err);
                });
        });
    </script>
</body>
</html>
"""


# 3. Server HTTP locale minimale che sostituisce i callback di Colab
class DashboardHandler(BaseHTTPRequestHandler):
    def _send(self, status, body, content_type):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return {}

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, html_code, "text/html; charset=utf-8")
        else:
            self._send(404, "Non trovato", "text/plain; charset=utf-8")

    def do_POST(self):
        payload = self._read_json()
        if self.path == "/api/verify":
            result = run_bibliography_verifier(
                payload.get("article", ""), payload.get("bibliography", "")
            )
            self._send(200, result, "application/json")
        elif self.path == "/api/js-error":
            print(f"Errore JavaScript: {payload.get('message')}")
            self._send(204, "", "text/plain")
        else:
            self._send(404, "Non trovato", "text/plain; charset=utf-8")


def main():
    parser = argparse.ArgumentParser(description="Dashboard del Verificatore di Bibliografia e Affermazioni")
    parser.add_argument("--host", default="127.0.0.1", help="indirizzo su cui ascoltare (predefinito: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="porta del server (predefinita: 8000)")
    parser.add_argument("--no-browser", action="store_true", help="non aprire automaticamente il browser")
    args = parser.parse_args()

    if not os.getenv("TYPESAFE_API_KEY"):
        print("[AVVISO] TYPESAFE_API_KEY non impostata: le verifiche non funzioneranno.")
    if not os.getenv("OPENROUTER_API_KEY"):
        print("[AVVISO] OPENROUTER_API_KEY non impostata: verrà usato solo il riepilogo di Jev.")
    else:
        print(f"Modelli OpenRouter per la giustificazione: {' → '.join(_openrouter_models())}")

    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    url = f"http://{args.host}:{args.port}/"
    print(f"Dashboard attiva su {url} (Ctrl+C per fermare)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
