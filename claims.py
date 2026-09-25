"""
Verifica affermazione per affermazione.

1. un LLM estrae dall'articolo le affermazioni atomiche, separando fatti e opinioni,
   con i riferimenti citati e parole chiave in italiano e inglese;
2. dal testo delle fonti scaricate si scelgono i passaggi più pertinenti a ogni affermazione;
3. Jev decide, per ogni fatto, se gli estratti delle fonti lo sostengono;
4. se Jev è incerto (confidenza sotto soglia) il caso passa a un LLM che ragiona.
"""
import math
import os
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError

from typesafe_sdk import Choice

import llm

# Verdetti possibili per un fatto verificato con le fonti
VERDICTS = {
    "Sostenuta": "Gli estratti delle fonti affermano esplicitamente ciò che dice l'affermazione (anche in un'altra lingua o con parole diverse).",
    "Parzialmente sostenuta": "Gli estratti confermano il nucleo dell'affermazione, ma alcuni dettagli (date, numeri, nomi) non compaiono negli estratti.",
    "Esagerata": "L'affermazione distorce le fonti: ne trae conclusioni, certezze o generalizzazioni che le fonti non contengono o non giustificano.",
    "Contraddetta": "Gli estratti dicono qualcosa di diverso o di opposto rispetto all'affermazione.",
    "Non trovata": "Gli estratti non trattano l'argomento dell'affermazione, quindi non la confermano né la smentiscono.",
}
# Verdetti assegnati senza chiamare Jev
OPINION = "Opinione"
EXPERIENCE = "Esperienza personale"
NO_SOURCE = "Senza fonte"
UNREACHABLE = "Fonte irraggiungibile"

ESCALATION_THRESHOLD = float(os.getenv("ESCALATION_THRESHOLD", "0.8"))
ESCALATION_MODEL = os.getenv("ESCALATION_MODEL", "openai/gpt-6-luna-pro")
MAX_ESCALATIONS = int(os.getenv("MAX_ESCALATIONS", "8"))
MAX_CLAIMS = 20
MAX_LINKS = 15
EXCERPT_CHARS = 1500

STOPWORDS = set("""
alla alle allo anche avere come con contro cosa così dalla dalle dallo degli della delle dello dopo dove
essere fino fra gli grazie hanno loro molto negli nella nelle nello nostro ogni oltre però perché più poco
quale quando quanto quella quelle quelli quello questa queste questi questo sono stata stato suoi sulla
sulle sullo tale tanto tutti tutto una uno verso about after also been from have into more than that
their there these they this were which with would
""".split())


# --- Bibliografia e download delle fonti -------------------------------------------------------

def clean_url(url):
    """Rimuove la punteggiatura finale, mantenendo una ')' che chiude una '(' interna all'URL."""
    url = url.strip()
    while url and url[-1] in '.,;:!?]}"\'':
        url = url[:-1]
        url = url.strip()
    while url.endswith(')') and url.count(')') > url.count('('):
        url = url[:-1].rstrip('.,;:!?')
    return url


def parse_references(bibliography):
    """Mappa numero di riferimento -> {url, titolo}. Le righe senza [n] vengono numerate in ordine."""
    refs, order = {}, 0
    for line in bibliography.splitlines():
        url = re.search(r'https?://[^\s<>"\x7f-\xff]+', line)
        if not url:
            continue
        order += 1
        number = re.match(r"\s*[-*]?\s*\[?(\d+)[\].)]", line)
        title = line.replace(url.group(0), "").strip(" -*\t")
        refs[number.group(1) if number else str(order)] = {"url": clean_url(url.group(0)), "title": title}
    return refs


def fetch_page(url, timeout=8, max_bytes=2_000_000):
    """Scarica una pagina e restituisce lo stato del link e l'HTML (None se non è testo)."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) BibliographyVerifier/1.0",
        "Accept-Language": "it,en;q=0.8",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            status = str(response.getcode())
            content_type = response.headers.get("Content-Type", "")
            body = response.read(max_bytes)
            charset = response.headers.get_content_charset() or "utf-8"
    except HTTPError as e:
        return {"url": url, "status": str(e.code), "active": False}, None
    except URLError:
        return {"url": url, "status": "DNS / Irraggiungibile", "active": False}, None
    except Exception:
        return {"url": url, "status": "Errore", "active": False}, None
    is_text = "html" in content_type or "text" in content_type
    return {"url": url, "status": status, "active": True}, (body.decode(charset, "ignore") if is_text else None)


def fetch_all(urls, deadline=8):
    """Controlla e scarica fino a MAX_LINKS URL in parallelo, entro `deadline` secondi in tutto.

    Serve un tetto complessivo perché la risoluzione DNS di un dominio inesistente
    può ignorare il timeout della singola richiesta."""
    urls = list(dict.fromkeys(urls))[:MAX_LINKS]
    pool = ThreadPoolExecutor(max_workers=8)
    futures = [pool.submit(fetch_page, url) for url in urls]
    start = time.perf_counter()
    results = []
    for url, future in zip(urls, futures):
        try:
            results.append(future.result(timeout=max(0.1, deadline - (time.perf_counter() - start))))
        except FuturesTimeout:
            results.append(({"url": url, "status": "Timeout", "active": False}, None))
    pool.shutdown(wait=False, cancel_futures=True)
    return [status for status, _ in results], {status["url"]: html for status, html in results}


class _TextExtractor(HTMLParser):
    """Estrae i blocchi di testo leggibile da una pagina HTML."""

    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "button", "select"}
    BLOCK = {"p", "li", "h1", "h2", "h3", "h4", "td", "th", "div", "section", "article", "blockquote", "dd", "figcaption"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.buffer = []
        self.blocks = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip_depth += 1
        elif tag in self.BLOCK:
            self._flush()

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip_depth:
            self.skip_depth -= 1
        elif tag in self.BLOCK:
            self._flush()

    def handle_data(self, data):
        if not self.skip_depth:
            self.buffer.append(data)

    def _flush(self):
        text = re.sub(r"\s+", " ", "".join(self.buffer)).strip()
        self.buffer = []
        if len(text) >= 40:
            self.blocks.append(text)


def html_to_paragraphs(html):
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        pass
    # rimuove le note tipo [12] di Wikipedia e i blocchi duplicati
    seen, paragraphs = set(), []
    for block in parser.blocks:
        block = re.sub(r"\[\d+\]", "", block)
        if block not in seen:
            seen.add(block)
            paragraphs.append(block)
    return paragraphs


# --- Estrazione delle affermazioni --------------------------------------------------------------

def extract_claims_llm(article, refs):
    """Chiede a un LLM le affermazioni atomiche dell'articolo. Restituisce (affermazioni, metriche)."""
    sources = "\n".join(f"[{n}] {r['title']} {r['url']}" for n, r in refs.items()) or "(nessuna fonte)"
    prompt = (
        "Estrai dall'articolo le affermazioni da verificare, al massimo "
        f"{MAX_CLAIMS}, nell'ordine in cui compaiono.\n"
        "Regole:\n"
        "- ogni affermazione è atomica (un solo fatto) e comprensibile da sola: sostituisci pronomi e riferimenti impliciti;\n"
        "- riporta ciò che l'articolo afferma, comprese esagerazioni ed errori: non correggerlo e non attenuarlo;\n"
        '- "tipo": "fatto" se è verificabile con una fonte pubblicata (date, numeri, eventi, risultati, attribuzioni); '
        '"esperienza" se è un resoconto in prima persona di ciò che l\'autore ha fatto o osservato, che nessuna fonte pubblicata può verificare; '
        '"opinione" se è un giudizio, un\'interpretazione o una previsione dichiarata come tale. '
        'Un\'interpretazione presentata come conseguenza dei fatti o delle fonti ("dimostra che", "significa che", '
        '"è confermato che") è un "fatto" da verificare;\n'
        '- "fonti": i numeri delle fonti citate con [n] vicino all\'affermazione; se l\'articolo non usa [n], '
        "indica le fonti dell'elenco che trattano l'argomento, altrimenti lascia la lista vuota;\n"
        '- "parole_chiave": 4-8 termini per ritrovare il passaggio nelle fonti, in italiano e in inglese, '
        "compresi nomi propri, termini tecnici e numeri anche in forma inglese (es. 4.700 -> 4,700 e 4.7k; nove -> nine).\n\n"
        'Rispondi solo con JSON: {"affermazioni": [{"testo": "...", "tipo": "fatto", "fonti": ["1"], '
        '"parole_chiave": ["..."]}]}\n\n'
        f"FONTI:\n{sources}\n\nARTICOLO:\n{article}"
    )
    reply = llm.chat(prompt, timeout=120, json_mode=True)
    data = llm.parse_json(reply["text"])
    claims = []
    for item in data.get("affermazioni", [])[:MAX_CLAIMS]:
        text = str(item.get("testo", "")).strip()
        if not text:
            continue
        claims.append({
            "text": text,
            "kind": {"opin": "opinione", "espe": "esperienza"}.get(str(item.get("tipo", "")).lower()[:4], "fatto"),
            "refs": [str(r).strip("[] ") for r in item.get("fonti", []) if str(r).strip("[] ") in refs],
            "keywords": [str(k) for k in item.get("parole_chiave", [])][:10],
        })
    return claims, {k: v for k, v in reply.items() if k != "text"}


def split_claims(article):
    """Riserva senza LLM: una frase = un'affermazione, con i riferimenti [n] che contiene."""
    claims = []
    for paragraph in re.split(r"\n+", article):
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-ZÀ-Ý«\"“])", paragraph.strip()):
            refs = list(dict.fromkeys(re.findall(r"\[(\d+)\]", sentence)))
            text = re.sub(r"\s*\[\d+\]", "", sentence).strip()
            text = re.sub(r"\s+([.,;:!?])", r"\1", text)
            # salta titoli e frammenti troppo brevi per essere affermazioni
            if len(text.split()) < 6 or not re.search(r"[.!?:]$", text):
                continue
            claims.append({"text": text, "kind": "fatto", "refs": refs, "keywords": []})
    return claims[:MAX_CLAIMS]


# --- Scelta degli estratti ----------------------------------------------------------------------

NUMBER = re.compile(r"(\d+(?:[.,]\d+)*)\s*(k|mila|milion[ei]|million[s]?|miliard[oi]|billion[s]?)?\b", re.I)
MULTIPLIERS = {"k": 1e3, "mila": 1e3, "milion": 1e6, "million": 1e6, "miliard": 1e9, "billion": 1e9}


def _numbers(text):
    """Valori numerici del testo, con tutte le letture possibili dei separatori:
    "4.700" -> {4.7, 4700}, "4.7k" -> {4700}, "1,5 milioni" -> {1500000}."""
    values = set()
    for digits, suffix in NUMBER.findall(text):
        readings = {digits.replace(",", "."), digits.replace(".", "").replace(",", "."),
                    digits.replace(",", "").replace(".", "."), digits.replace(".", "").replace(",", "")}
        multiplier = 1.0
        if suffix:
            multiplier = next(v for k, v in MULTIPLIERS.items() if suffix.lower().startswith(k))
        for reading in readings:
            try:
                values.add(round(float(reading) * multiplier, 6))
            except ValueError:
                pass
    return values


def _keywords(text):
    """Parole chiave pesate: nomi propri contano di più; il prefisso aiuta tra lingue diverse.
    I numeri sono confrontati a parte, per valore."""
    weights = {}
    for token in re.findall(r"[\wÀ-ÿ][\wÀ-ÿ\-,.]*[\wÀ-ÿ]|\d", text):
        token = token.strip(",.")
        lower = token.lower()
        if any(c.isdigit() for c in token):
            continue
        elif token[0].isupper() and len(token) > 2:
            key, weight = lower[:6], 2.0
        elif len(lower) >= 5 and lower not in STOPWORDS:
            key, weight = lower[:5], 1.0
        else:
            continue
        weights[key] = max(weights.get(key, 0), weight)
    return weights


def best_excerpt(query, paragraphs, max_chars=EXCERPT_CHARS):
    """Sceglie i paragrafi della fonte che condividono più parole chiave con la ricerca."""
    keywords = _keywords(query)
    # i numeri interi piccoli (es. "5 strati") combaciano con troppi paragrafi
    numbers = {n for n in _numbers(query) if n >= 10 or n != int(n)}
    scored = []
    for index, paragraph in enumerate(paragraphs):
        lower = paragraph.lower()
        score = sum(w for k, w in keywords.items() if k in lower)
        score += 3.0 * len(numbers & _numbers(paragraph))
        if score:
            scored.append((score / math.sqrt(1 + len(paragraph) / 400), index, paragraph))
    scored.sort(key=lambda s: (-s[0], s[1]))
    excerpt, used = [], 0
    for _, _, paragraph in scored[:4]:
        piece = paragraph[: max_chars - used]
        excerpt.append(piece)
        used += len(piece)
        if used >= max_chars:
            break
    return "\n".join(excerpt)


# --- Verifica -----------------------------------------------------------------------------------

def _ask_jev(client, claim, sources):
    state = f"AFFERMAZIONE:\n{claim['text']}\n"
    for ref, source in sources.items():
        state += f"\nESTRATTO DELLA FONTE [{ref}] ({source['url']}):\n{source['excerpt'] or '(nessun passaggio pertinente trovato)'}\n"
    response = client.system_one(
        state=state,
        model="jev-latest",
        questions={
            "verdict": Choice(
                instructions="Gli estratti delle fonti citate sostengono l'affermazione?",
                criteria=VERDICTS,
            )
        },
    )
    answer = response.answers["verdict"]
    return {
        "verdict": answer.choice,
        "confidence": answer.confidence,
        "probabilities": answer.probabilities,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }


def _escalate(row):
    """Chiede a un LLM che ragiona di decidere un caso in cui Jev è incerto."""
    excerpts = "\n\n".join(
        f"FONTE [{ref}] ({s['url']}):\n{s['excerpt'] or '(nessun passaggio pertinente trovato)'}"
        for ref, s in row["_sources"].items()
    )
    labels = "\n".join(f"- {k}: {v}" for k, v in VERDICTS.items())
    probs = ", ".join(f"{k} {v:.0%}" for k, v in row["probabilities"].items())
    prompt = (
        "Devi stabilire se gli estratti delle fonti sostengono un'affermazione di un articolo. "
        f"Un classificatore rapido era incerto ({probs}).\n\n"
        f"AFFERMAZIONE:\n{row['text']}\n\n{excerpts}\n\n"
        f"Scegli un solo verdetto tra:\n{labels}\n\n"
        'Rispondi solo con JSON: {"verdetto": "<uno dei verdetti>", "motivo": "<una frase in italiano>"}'
    )
    reply = llm.chat(prompt, model=ESCALATION_MODEL, reasoning=True, json_mode=True)
    data = llm.parse_json(reply["text"])
    if data.get("verdetto") not in VERDICTS:
        raise ValueError(f"verdetto non valido dal LLM: {reply['text'][:120]}")
    return data["verdetto"], str(data.get("motivo", "")), reply


def verify_claims(client, claims, refs, pages):
    """Verifica ogni affermazione. `pages` mappa URL -> HTML scaricato (None se irraggiungibile).

    Restituisce (righe, metriche Jev, metriche escalation)."""
    paragraphs = {url: html_to_paragraphs(html) for url, html in pages.items() if html}

    def check(claim):
        query = claim["text"] + " " + " ".join(claim.get("keywords", []))
        sources = {}
        for ref in claim["refs"]:
            url = refs[ref]["url"] if ref in refs else None
            if url and paragraphs.get(url):
                sources[ref] = {"url": url, "excerpt": best_excerpt(query, paragraphs[url])}
        row = {"text": claim["text"], "kind": claim["kind"], "refs": claim["refs"], "verdict": None,
               "confidence": None, "decided_by": None, "reason": "",
               "excerpts": {ref: s["excerpt"] for ref, s in sources.items()}}
        if claim["kind"] == "opinione":
            return {**row, "verdict": OPINION}
        if claim["kind"] == "esperienza":
            return {**row, "verdict": EXPERIENCE}
        if not claim["refs"]:
            return {**row, "verdict": NO_SOURCE}
        if not sources:
            return {**row, "verdict": UNREACHABLE}
        result = _ask_jev(client, claim, sources)
        return {**row, **result, "decided_by": "Jev", "jev_verdict": result["verdict"], "_sources": sources}

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(check, claims))
    jev_rows = [r for r in rows if r.get("input_tokens")]
    jev = {
        "calls": len(jev_rows),
        "seconds": round(time.perf_counter() - start, 3),
        "input_tokens": sum(r["input_tokens"] for r in jev_rows),
        "output_tokens": sum(r["output_tokens"] for r in jev_rows),
    }

    # Instradamento per confidenza: solo i casi incerti passano al LLM, il più incerto per primo
    escalation = {"model": ESCALATION_MODEL, "calls": 0, "seconds": 0.0, "input_tokens": 0,
                  "output_tokens": 0, "cost_cents": 0.0, "errors": []}
    uncertain = sorted((r for r in jev_rows if r["confidence"] < ESCALATION_THRESHOLD),
                       key=lambda r: r["confidence"])[:MAX_ESCALATIONS]
    if uncertain and llm.available():
        def escalate(row):
            try:
                return row, _escalate(row), None
            except Exception as e:
                return row, None, str(e)

        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=4) as pool:
            for row, outcome, error in pool.map(escalate, uncertain):
                if error:
                    escalation["errors"].append(error)
                    continue
                verdict, reason, reply = outcome
                row.update(verdict=verdict, reason=reason, decided_by="LLM")
                escalation["calls"] += 1
                escalation["model"] = reply["model"]
                escalation["input_tokens"] += reply["input_tokens"]
                escalation["output_tokens"] += reply["output_tokens"]
                escalation["cost_cents"] += reply["cost_cents"]
        escalation["seconds"] = round(time.perf_counter() - start, 3)

    for row in rows:
        for key in ("_sources", "probabilities", "input_tokens", "output_tokens"):
            row.pop(key, None)
    return rows, jev, escalation


def summarize(rows):
    """Riepilogo testuale dei verdetti, da passare alla valutazione globale e al LLM."""
    lines = []
    for i, row in enumerate(rows, 1):
        conf = f" {row['confidence']:.0%}" if row.get("confidence") is not None else ""
        by = f", deciso da {row['decided_by']}" if row.get("decided_by") == "LLM" else ""
        refs = "".join(f"[{r}]" for r in row["refs"])
        lines.append(f"{i}. [{row['verdict']}{conf}{by}] {row['text']} {refs}".rstrip())
    return "\n".join(lines) or "(nessuna affermazione estratta)"


def counts(rows):
    """Quante affermazioni per ciascun verdetto."""
    result = {}
    for row in rows:
        result[row["verdict"]] = result.get(row["verdict"], 0) + 1
    return result
