# /// script
# requires-python = ">=3.10"
# dependencies = ["typesafe-sdk", "python-dotenv", "pypdf"]
# ///
"""
📊 Skepsis — verifica di fonti e affermazioni (versione locale).

Uso:
    uv run main.py [--port 8000] [--no-browser]

Legge TYPESAFE_API_KEY e OPENROUTER_API_KEY dall'ambiente, da ../.env o da ./.env.
Le altre variabili facoltative sono descritte nel README.
"""
import argparse
import json
import os
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dotenv import load_dotenv
from typesafe_sdk import Choice, Noul, Score, TypeSafeClient

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")
load_dotenv(HERE.parent / ".env")

import claims  # noqa: E402  (dopo load_dotenv: legge le variabili d'ambiente)
import discovery  # noqa: E402
import llm  # noqa: E402

# Prezzo Jev: $42 per miliardo di token di input (typesafe.ai), output gratuito
JEV_PRICE_PER_M_INPUT = float(os.getenv("JEV_PRICE_PER_M_INPUT", "0.042"))
MAX_REQUEST_BYTES = 1_000_000
MAX_ARTICLE_CHARS = 100_000
MAX_BIBLIOGRAPHY_CHARS = 30_000

# Significato delle stelle: 1-2 non buono (1 molto falso, 2 vero ma fuorviante), 3 buono, 4 molto buono, 5 ottimo
STAR_MEANING = {1: "Molto falso", 2: "Vero ma fuorviante", 3: "Buono", 4: "Molto buono", 5: "Ottimo"}
# Livelli della domanda Score: la posizione (da 0) corrisponde a stelle - 1
STAR_CRITERIA = [
    "1 stella, molto falso: le affermazioni principali sono false o inventate, oppure le fonti le contraddicono, non esistono o non c'entrano.",
    "2 stelle, vero ma fuorviante: i fatti sono in parte veri o supportati, ma presentati, selezionati o forzati in modo da ingannare il lettore.",
    "3 stelle, buono: fonti accettabili che sostengono le affermazioni principali, anche se alcune citazioni sono poco approfondite o non verificabili.",
    "4 stelle, molto buono: fonti affidabili che sostengono le affermazioni, con solo lacune minori.",
    "5 stelle, ottimo: fonti autorevoli e verificate che sostengono pienamente ogni affermazione.",
]

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


def _jev_cents(input_tokens):
    # Jev fattura solo i token di input; l'output è gratuito
    return input_tokens * JEV_PRICE_PER_M_INPUT / 1e6 * 100


def _step(name, model, seconds, input_tokens=0, output_tokens=0, cost_cents=0.0, error=None):
    return {"step": name, "model": model, "seconds": round(seconds, 3) if seconds is not None else None,
            "input_tokens": input_tokens, "output_tokens": output_tokens, "cost_cents": cost_cents, "error": error}


# 1. Logica principale del verificatore, chiamata dal frontend
def run_bibliography_verifier(article_text, bibliography_raw, find_authoritative_sources=False):
    try:
        if not isinstance(article_text, str) or not isinstance(bibliography_raw, str):
            return json.dumps({"error": "Articolo e bibliografia devono essere testo."})
        if not isinstance(find_authoritative_sources, bool):
            return json.dumps({"error": "find_authoritative_sources deve essere booleano."})
        if not article_text.strip() or not bibliography_raw.strip():
            return json.dumps({"error": "Inserisci sia l'articolo sia almeno una fonte bibliografica."})
        if len(article_text) > MAX_ARTICLE_CHARS:
            return json.dumps({"error": f"Articolo troppo lungo: massimo {MAX_ARTICLE_CHARS:,} caratteri."})
        if len(bibliography_raw) > MAX_BIBLIOGRAPHY_CHARS:
            return json.dumps({"error": f"Bibliografia troppo lunga: massimo {MAX_BIBLIOGRAPHY_CHARS:,} caratteri."})
        api_key = os.getenv('TYPESAFE_API_KEY')
        if not api_key:
            return json.dumps({"error": "TYPESAFE_API_KEY non trovata nell'ambiente o nel file .env."})
        client = TypeSafeClient(api_key=api_key)
        steps = []

        # Controllo dei link e download delle fonti, in parallelo
        start = time.perf_counter()
        refs = claims.parse_references(bibliography_raw)
        link_statuses, pages = claims.fetch_all([r["url"] for r in refs.values()])
        steps.append(_step("Controllo link e download fonti", "-", time.perf_counter() - start))

        # Estrazione delle affermazioni con un LLM (riserva: una frase = un'affermazione)
        start = time.perf_counter()
        claim_list, extraction_error = None, None
        if llm.available():
            try:
                claim_list, ext = claims.extract_claims_llm(article_text, refs)
                steps.append(_step("Estrazione affermazioni", ext["model"], ext["seconds"],
                                   ext["input_tokens"], ext["output_tokens"], ext["cost_cents"]))
            except Exception as e:
                extraction_error = str(e)
                print(f"[AVVISO] Estrazione con LLM non riuscita, divido per frasi: {e}")
        if claim_list is None:
            claim_list = claims.split_claims(article_text)
            steps.append(_step("Estrazione affermazioni (per frasi)", "-", time.perf_counter() - start,
                               error=extraction_error))

        # Verifica di ogni affermazione con Jev; i casi incerti passano a un LLM che ragiona
        rows, jev_claims, escalation = claims.verify_claims(client, claim_list, refs, pages)
        steps.append(_step(f"Jev: verifica affermazioni ({jev_claims['calls']} chiamate)", "jev-latest",
                           jev_claims["seconds"], jev_claims["input_tokens"], jev_claims["output_tokens"],
                           _jev_cents(jev_claims["input_tokens"])))
        if escalation["calls"] or escalation["errors"]:
            steps.append(_step(f"LLM: casi incerti ({escalation['calls']})", escalation["model"],
                               escalation["seconds"], escalation["input_tokens"], escalation["output_tokens"],
                               escalation["cost_cents"], "; ".join(escalation["errors"]) or None))

        # Riscontro esterno opzionale: non entra nel voto, perché non deve far
        # sembrare citato bene un articolo le cui fonti originali sono carenti.
        external_check = {"requested": find_authoritative_sources, "available": discovery.available(),
                          "sources": [], "checks": [], "message": None}
        if find_authoritative_sources:
            if not discovery.available():
                external_check["message"] = "Ricerca non disponibile: configura TAVILY_API_KEY."
            else:
                missing_verdicts = {"Non trovata", "Contraddetta", "Esagerata", claims.NO_SOURCE, claims.UNREACHABLE}
                candidates = [
                    (index, claim) for index, (claim, row) in enumerate(zip(claim_list, rows))
                    if claim["kind"] in {"fatto", "conclusione"} and row["verdict"] in missing_verdicts
                ]
                if not candidates:
                    external_check["message"] = "Nessun claim non coperto dalla bibliografia richiede una ricerca esterna."
                else:
                    start = time.perf_counter()
                    sources, search_errors = discovery.discover_for_claims(candidates)
                    steps.append(_step(f"Ricerca fonti autorevoli ({len(candidates)} claim)", "Tavily",
                                       time.perf_counter() - start, error="; ".join(search_errors) or None))
                    external_check["sources"] = sources
                    if not sources:
                        external_check["message"] = "Nessuna fonte ha superato la policy di dominio autorevole."
                    else:
                        external_links, external_pages = claims.fetch_all([source["url"] for source in sources])
                        status_by_url = {status["url"]: status for status in external_links}
                        for source in sources:
                            source.update(status_by_url.get(source["url"], {}))
                        checks, metrics = claims.verify_external_sources(client, claim_list, sources, external_pages)
                        external_check["checks"] = checks
                        external_check["message"] = (
                            "Riscontro esterno: non modifica il voto della bibliografia originale."
                        )
                        if metrics["calls"] or metrics["errors"]:
                            steps.append(_step(f"Jev: riscontro fonti esterne ({metrics['calls']} claim)", "jev-latest",
                                               metrics["seconds"], metrics["input_tokens"], metrics["output_tokens"],
                                               _jev_cents(metrics["input_tokens"]), "; ".join(metrics["errors"]) or None))
        claims_summary = claims.summarize(rows)

        # Valutazione globale con Jev, informata dai verdetti delle singole affermazioni
        links_summary = "\n".join(f"- {l['url']}: {l['status']}" for l in link_statuses) or "(nessun link)"
        state_data = (f"CONTENUTO DELL'ARTICOLO:\n{article_text}\n\nBIBLIOGRAFIA/FONTI:\n{bibliography_raw}\n\n"
                      f"STATO DEI LINK:\n{links_summary}\n\n"
                      f"VERIFICA DELLE AFFERMAZIONI SUL TESTO DELLE FONTI:\n{claims_summary}")
        start = time.perf_counter()
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
                "overall_score": Score(
                    instructions="Valuta la qualità e l'affidabilità dell'articolo e della sua bibliografia da 1 a 5 stelle, "
                                 "tenendo conto della verifica delle singole affermazioni. Opinioni ed esperienze personali "
                                 "presentate come tali non richiedono fonti e non abbassano il voto.",
                    criteria=STAR_CRITERIA,
                ),
                # Jev restituisce giudizi, non testo: questi segnali sì/no alimentano
                # la giustificazione scritta (OpenRouter) e il riepilogo di riserva.
                **{key: Noul(instructions=text) for key, text in SIGNALS.items()}
            }
        )
        steps.append(_step("Jev: valutazione globale", response.model, time.perf_counter() - start,
                           response.usage.input_tokens, response.usage.output_tokens,
                           _jev_cents(response.usage.input_tokens)))

        relation = response.answers["relation_rating"]
        adherence = response.answers["claim_adherence"]
        rating = response.answers["overall_score"]
        rating_value = rating.score + 1
        # Le stelle vengono dalle regole sui verdetti delle affermazioni; il voto globale di Jev
        # resta come informazione e come riserva quando non ci sono fatti da verificare
        stars, rating_reason = claims.rate(rows, refs)
        rating_source = "regole"
        if stars is None:
            stars, rating_source = min(5, max(1, round(rating_value))), "Jev"
        verdict = _build_explanation(
            relation, adherence, rating_value, stars, rating_reason,
            {key: response.answers[key].noul for key in SIGNALS},
            link_statuses, rows,
        )

        # Giustificazione scritta
        try:
            reply = _llm_justification(article_text, bibliography_raw, verdict, claims_summary)
            explanation = f"{reply['text']}\n\n[Jev] {verdict}"
            steps.append(_step("Giustificazione", reply["model"], reply["seconds"], reply["input_tokens"],
                               reply["output_tokens"], reply["cost_cents"]))
        except Exception as e:
            print(f"[AVVISO] Giustificazione OpenRouter non riuscita, uso il riepilogo di Jev: {e}")
            explanation = verdict
            steps.append(_step("Giustificazione", llm.primary_model() if llm.available() else "-", None, error=str(e)))

        result = {
            "links": link_statuses,
            "external_check": external_check,
            "claims": rows,
            "claim_counts": claims.counts(rows),
            "relation": relation.choice,
            "adherence": adherence.choice,
            "rating": _stars_label(stars),
            "rating_stars": stars,
            "rating_reason": rating_reason,
            "rating_source": rating_source,
            "rating_value": round(rating_value, 2),
            "rating_confidence": round(rating.confidence, 3),
            "rating_good": stars >= 3,
            "explanation": explanation,
            "metrics": {
                "steps": steps,
                "total_seconds": round(sum(s["seconds"] or 0 for s in steps), 3),
                "total_cents": sum(s["cost_cents"] for s in steps),
            }
        }
        return json.dumps(result)

    except Exception as e:
        return json.dumps({"error": str(e)})


def _llm_justification(article_text, bibliography_raw, verdict, claims_summary):
    """Chiede a un modello OpenRouter di scrivere la giustificazione del verdetto di Jev."""
    prompt = (
        "Stai revisionando un articolo e la sua bibliografia. Un classificatore ha già prodotto questo verdetto:\n"
        f"{verdict}\n\n"
        "Ogni affermazione è stata anche confrontata con il testo delle fonti scaricate:\n"
        f"{claims_summary}\n\n"
        "Scrivi in italiano una giustificazione concisa (4-6 frasi, testo semplice, niente markdown) che spieghi il verdetto. "
        "Cita affermazioni specifiche dell'articolo e fonti specifiche, indica punti di forza e debolezze "
        "e menziona i link irraggiungibili se rilevanti. Non modificare le valutazioni.\n\n"
        f"ARTICOLO:\n{article_text}\n\nBIBLIOGRAFIA:\n{bibliography_raw}"
    )
    return llm.chat(prompt)


def _stars_label(stars):
    return "★" * stars + "☆" * (5 - stars) + f" {STAR_MEANING[stars]}"


def _build_explanation(relation, adherence, rating_value, stars, rating_reason, signals, link_statuses, rows):
    """Compone una motivazione leggibile a partire dalle risposte calibrate di Jev."""
    parts = [
        f"Pertinenza: {relation.choice} (confidenza {relation.confidence:.0%}). "
        f"Aderenza alle fonti: {adherence.choice} ({adherence.confidence:.0%}). "
        f"Complessivo: {_stars_label(stars)}, perché {rating_reason} (voto globale di Jev: {rating_value:.1f}/5)."
    ]
    if rows:
        parts.append("Affermazioni: " + ", ".join(f"{n} {v.lower()}" for v, n in claims.counts(rows).items()) + ".")
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
    <title>Skepsis — Verifica fonti</title>
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
        .claims-table td { font-size: 0.88rem; vertical-align: top; }
        .claims-table details summary { cursor: pointer; color: #4f46e5; font-size: 0.78rem; }
        .claims-table .excerpt { font-size: 0.78rem; color: #6b7280; white-space: pre-line; max-height: 14em; overflow-y: auto; }
        .verdict-Sostenuta { background: #16a34a; }
        .verdict-Parzialmente-sostenuta { background: #65a30d; }
        .verdict-Esagerata { background: #eab308; color: #1f2937; }
        .verdict-Contraddetta { background: #dc2626; }
        .verdict-Non-trovata { background: #6b7280; }
        .verdict-Fonte-irraggiungibile { background: #374151; }
        .verdict-Senza-fonte { background: #9ca3af; }
        .verdict-Opinione, .verdict-Esperienza-personale { background: #e5e7eb; color: #374151; }
        .kind-conclusione { font-size: 0.7rem; color: #7c3aed; font-weight: 600; }
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
        <h2 class="mb-1" style="color: #1e1b4b; font-weight: 700;">📚 Skepsis</h2>
        <p class="text-muted mb-4">Verifica delle affermazioni fondata sulle fonti con Jev.</p>

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
            <div class="form-check mt-1">
                <input class="form-check-input" type="checkbox" id="external-sources-input">
                <label class="form-check-label" for="external-sources-input">
                    Cerca fonti autorevoli aggiuntive per i claim non coperti
                </label>
                <div class="form-text">Riscontro separato: non modifica il voto della bibliografia. Richiede <code>TAVILY_API_KEY</code>.</div>
            </div>
            <button class="btn btn-primary mt-2" id="btn-run" style="background-color:#4f46e5; border:none;">Avvia analisi Jev e controllo link</button>
        </div>

        <!-- Indicatore di caricamento -->
        <div id="loader" class="alert alert-info d-none" role="alert">
            🔄 Controllo dei link, estrazione e verifica delle affermazioni con Jev in corso... Attendere (circa 20-40 secondi).
        </div>

        <!-- Griglia dei risultati -->
        <div id="dashboard-results" class="d-none">
            <div class="row mb-3">
                <div class="col-md-3">
                    <div class="card kpi-card">
                        <div class="kpi-title">Fatti sostenuti dalle fonti</div>
                        <div class="kpi-val" id="kpi-claims">-</div>
                        <div class="text-muted" style="font-size:0.75rem;" id="kpi-claims-detail"></div>
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

            <!-- Verifica affermazione per affermazione -->
            <div class="card">
                <div class="card-header"><i class="bi bi-list-check"></i> Verifica delle affermazioni</div>
                <div class="card-body">
                    <div class="table-responsive">
                        <table class="table table-sm align-middle claims-table">
                            <thead>
                                <tr>
                                    <th>#</th>
                                    <th>Affermazione</th>
                                    <th>Fonti</th>
                                    <th>Verdetto</th>
                                    <th class="text-end">Confidenza</th>
                                    <th>Deciso da</th>
                                </tr>
                            </thead>
                            <tbody id="claims-table-body"></tbody>
                        </table>
                    </div>
                </div>
            </div>

            <div class="card d-none" id="external-sources-card">
                <div class="card-header"><i class="bi bi-search"></i> Riscontro con fonti autorevoli aggiuntive</div>
                <div class="card-body">
                    <p class="text-muted mb-2" id="external-sources-message"></p>
                    <div class="table-responsive">
                        <table class="table table-sm align-middle mb-0">
                            <thead><tr><th>Claim</th><th>Fonte trovata</th><th>Policy</th><th>Riscontro Jev</th></tr></thead>
                            <tbody id="external-sources-table-body"></tbody>
                        </table>
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
                                            <th>Contenuto</th>
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
                                        <th>Passaggio</th>
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

        function esc(text) {
            return String(text ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
        }

        function renderClaims(claims, counts) {
            const body = document.getElementById('claims-table-body');
            body.innerHTML = '';
            (claims || []).forEach((c, i) => {
                const tr = document.createElement('tr');
                const conf = c.confidence === null || c.confidence === undefined ? '-' : Math.round(c.confidence * 100) + '%';
                let by = '-';
                if (c.decided_by === 'Jev') by = '<i class="bi bi-lightning-charge-fill text-primary"></i> Jev';
                if (c.decided_by === 'LLM') by = `<i class="bi bi-cpu-fill text-warning"></i> LLM <span class="text-muted" style="font-size:0.75rem;">(Jev: ${esc(c.jev_verdict)})</span>`;
                const excerpts = Object.entries(c.excerpts || {}).map(([ref, text]) => `<b>[${esc(ref)}]</b> ${esc(text) || '<i>nessun passaggio pertinente</i>'}`).join('\\n\\n');
                const details = excerpts ? `<details><summary>estratti delle fonti</summary><div class="excerpt">${excerpts}</div></details>` : '';
                const reason = c.reason ? `<div class="text-muted" style="font-size:0.78rem;"><i class="bi bi-chat-left-text"></i> ${esc(c.reason)}</div>` : '';
                const kind = c.kind === 'conclusione' ? '<span class="kind-conclusione">CONCLUSIONE</span> ' : '';
                const world = c.world === undefined || c.world === null ? '' :
                    `<div class="text-muted" style="font-size:0.75rem;">plausibilità secondo Jev: ${Math.round(c.world * 100)}%</div>`;
                const numbers = c.number_match === undefined || c.number_match === null ? '' :
                    `<div class="text-muted" style="font-size:0.75rem;"><i class="bi bi-123"></i> numero confermato dalla fonte: ${Math.round(c.number_match * 100)}%</div>`;
                tr.innerHTML = `<td>${i + 1}</td><td>${kind}${esc(c.text)}${reason}${world}${numbers}${details}</td>`
                    + `<td>${(c.refs || []).map(r => '[' + esc(r) + ']').join('')}</td>`
                    + `<td><span class="badge verdict-${esc(c.verdict).replace(/ /g, '-')}">${esc(c.verdict)}</span></td>`
                    + `<td class="text-end">${conf}</td><td>${by}</td>`;
                body.appendChild(tr);
            });
            const facts = (claims || []).filter(c => c.kind === 'fatto' || c.kind === 'conclusione').length;
            const supported = (counts || {})['Sostenuta'] || 0;
            document.getElementById('kpi-claims').textContent = facts ? `${supported} / ${facts}` : '-';
            document.getElementById('kpi-claims-detail').textContent =
                Object.entries(counts || {}).map(([v, n]) => `${n} ${v.toLowerCase()}`).join(' · ');
        }

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
                const err = x.error ? ` <span class="badge bg-danger" title="${esc(x.error)}">errore</span>` : '';
                tr.innerHTML = `<td>${esc(name)}${err}</td><td><code>${esc(x.model || '-')}</code></td>`
                    + `<td class="text-end">${formatSeconds(x.seconds)}</td>`
                    + `<td class="text-end">${(x.input_tokens || 0).toLocaleString('it-IT')} / ${(x.output_tokens || 0).toLocaleString('it-IT')}</td>`
                    + `<td class="text-end">${formatCents(x.cost_cents)}</td>`;
                body.appendChild(tr);
            };
            (m.steps || []).forEach(step => row(step.step, step));
            const total = document.createElement('tr');
            total.className = 'total-row';
            total.innerHTML = `<td colspan="2">Totale</td><td class="text-end">${formatSeconds(m.total_seconds)}</td>`
                + `<td></td><td class="text-end">${formatCents(m.total_cents)}</td>`;
            body.appendChild(total);
        }

        function renderRating(stars, fallbackText, reason) {
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
                + `<div class="meaning"><i class="bi ${level.icon}"></i> ${level.label}</div>`
                + (reason ? `<div class="text-muted" style="font-size:0.72rem; font-weight:400;">${esc(reason)}</div>` : '');
        }

        function renderExternalSources(external) {
            const card = document.getElementById('external-sources-card');
            if (!external || !external.requested) {
                card.classList.add('d-none');
                return;
            }
            card.classList.remove('d-none');
            document.getElementById('external-sources-message').textContent = external.message || '';
            const checksByClaim = new Map((external.checks || []).map(check => [check.claim_index, check]));
            const body = document.getElementById('external-sources-table-body');
            body.innerHTML = '';
            (external.sources || []).forEach(source => {
                const check = checksByClaim.get(source.claim_index);
                const row = document.createElement('tr');
                const verdict = check ? `${check.verdict}${check.confidence != null ? ' · ' + Math.round(check.confidence * 100) + '%' : ''}` : 'Non verificata';
                row.innerHTML = `
                    <td>#${source.claim_index + 1}</td>
                    <td><a href="${esc(source.url)}" target="_blank" rel="noopener">${esc(source.title)}</a><br><small class="text-muted">${esc(source.status || '')}</small></td>
                    <td>${esc(source.authority || '')}<br><small class="text-muted">${esc(source.content_available ? 'pagina letta' : 'snippet di ricerca')}</small></td>
                    <td>${esc(verdict)}</td>
                `;
                body.appendChild(row);
            });
            if (!(external.sources || []).length) {
                body.innerHTML = '<tr><td colspan="4" class="text-muted">Nessuna fonte aggiuntiva disponibile.</td></tr>';
            }
        }

        function renderResult(data) {
            // Riempie le schede di riepilogo
            const totalLinks = (data.links || []).length;
            const activeLinks = (data.links || []).filter(l => l.active).length;
            document.getElementById('kpi-active').textContent = activeLinks + " / " + totalLinks;
            document.getElementById('kpi-adherence').textContent = data.adherence || '-';
            renderRating(data.rating_stars, data.rating, data.rating_reason);
            renderClaims(data.claims, data.claim_counts);
            renderExternalSources(data.external_check);

            // Riempie la tabella dei link
            const tableBody = document.getElementById('links-table-body');
            tableBody.innerHTML = '';
            (data.links || []).forEach(link => {
                const row = document.createElement('tr');
                const badgeClass = link.active ? 'bg-success' : 'bg-danger';
                const contentClass = link.content_available ? 'bg-success' : 'bg-secondary';
                const contentLabel = link.content_available
                    ? `LETTO${link.source_type ? ' · ' + link.source_type : ''}`
                    : 'NON DISPONIBILE';
                row.innerHTML = `
                    <td><a href="${esc(link.url)}" target="_blank" rel="noopener" class="text-truncate d-inline-block" style="max-width:280px;">${esc(link.url)}</a></td>
                    <td><code>${esc(link.status)}</code></td>
                    <td><span class="badge ${badgeClass}">${link.active ? 'ATTIVO' : 'NON ATTIVO'}</span></td>
                    <td><span class="badge ${contentClass}">${esc(contentLabel)}</span></td>
                `;
                tableBody.appendChild(row);
            });

            // Riempie la valutazione semantica
            document.getElementById('eval-relation').textContent = data.relation || '-';
            document.getElementById('eval-explanation').textContent = data.explanation || '-';
            renderMetrics(data.metrics);

            document.getElementById('dashboard-results').classList.remove('d-none');
        }

        document.getElementById('btn-run').addEventListener('click', function() {
            const articleText = document.getElementById('article-input').value;
            const bibText = document.getElementById('bib-input').value;
            const findAuthoritativeSources = document.getElementById('external-sources-input').checked;

            if(!articleText || !bibText) {
                alert("Inserisci sia il testo dell'articolo sia i riferimenti bibliografici.");
                return;
            }

            document.getElementById('loader').classList.remove('d-none');
            document.getElementById('dashboard-results').classList.add('d-none');

            fetch('/api/verify', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({article: articleText, bibliography: bibText,
                    find_authoritative_sources: findAuthoritativeSources})
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

                    renderResult(data);
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
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send(400, json.dumps({"error": "Content-Length non valido."}), "application/json")
            return None
        if length < 0 or length > MAX_REQUEST_BYTES:
            self._send(413, json.dumps({"error": f"Richiesta troppo grande: massimo {MAX_REQUEST_BYTES // 1_000_000} MB."}),
                       "application/json")
            return None
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send(400, json.dumps({"error": "JSON non valido."}), "application/json")
            return None
        if not isinstance(payload, dict):
            self._send(400, json.dumps({"error": "Il corpo JSON deve essere un oggetto."}), "application/json")
            return None
        return payload

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, html_code, "text/html; charset=utf-8")
        else:
            self._send(404, "Non trovato", "text/plain; charset=utf-8")

    def do_POST(self):
        payload = self._read_json()
        if payload is None:
            return
        if self.path == "/api/verify":
            result = run_bibliography_verifier(
                payload.get("article", ""), payload.get("bibliography", ""),
                payload.get("find_authoritative_sources", False),
            )
            self._send(200, result, "application/json")
        elif self.path == "/api/js-error":
            print(f"Errore JavaScript: {payload.get('message')}")
            self._send(204, "", "text/plain")
        else:
            self._send(404, "Non trovato", "text/plain; charset=utf-8")


def main():
    parser = argparse.ArgumentParser(description="Dashboard Skepsis per la verifica di fonti e affermazioni")
    parser.add_argument("--host", default="127.0.0.1", help="indirizzo su cui ascoltare (predefinito: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="porta del server (predefinita: 8000)")
    parser.add_argument("--no-browser", action="store_true", help="non aprire automaticamente il browser")
    parser.add_argument("--allow-remote", action="store_true",
                        help="consenti un host non locale: usa solo dietro autenticazione e proxy sicuro")
    args = parser.parse_args()

    if args.host not in {"127.0.0.1", "::1", "localhost"} and not args.allow_remote:
        parser.error("per ascoltare su un host non locale devi aggiungere --allow-remote")

    if not os.getenv("TYPESAFE_API_KEY"):
        print("[AVVISO] TYPESAFE_API_KEY non impostata: le verifiche non funzioneranno.")
    if not os.getenv("OPENROUTER_API_KEY"):
        print("[AVVISO] OPENROUTER_API_KEY non impostata: verrà usato solo il riepilogo di Jev.")
    else:
        print(f"Modelli OpenRouter: {' → '.join(llm.model_chain())} (casi incerti: {claims.ESCALATION_MODEL})")
    if args.allow_remote:
        print("[AVVISO] API esposta: configura autenticazione e rate limiting nel proxy davanti a Skepsis.")

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
