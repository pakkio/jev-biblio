"""
Verifica affermazione per affermazione.

1. un LLM estrae dall'articolo le affermazioni atomiche, separando fatti e opinioni,
   con i riferimenti citati e parole chiave in italiano e inglese;
2. dal testo delle fonti scaricate si scelgono i passaggi più pertinenti a ogni affermazione;
3. Jev decide, per ogni fatto, se gli estratti delle fonti lo sostengono;
4. se Jev è incerto (confidenza sotto soglia) il caso passa a un LLM che ragiona.
"""
import io
import ipaddress
import json
import math
import os
import re
import socket
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit

from typesafe_sdk import Choice, Noul

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
# Selezione degli estratti: "jev" fa scegliere a Jev i paragrafi pertinenti tra i candidati
# trovati per parole chiave; "keywords" usa solo le parole chiave
EXCERPT_SELECTION = os.getenv("EXCERPT_SELECTION", "jev")
CANDIDATES = 12
CANDIDATE_CHARS = 700
RELEVANCE_THRESHOLD = 0.5
MAX_SELECTED = 3
MAX_SOURCE_BYTES = 2_000_000
MAX_SOURCE_CHARS = 250_000
MAX_PDF_PAGES = 50

STOPWORDS = set("""
alla alle allo anche avere come con contro cosa così dalla dalle dallo degli della delle dello dopo dove
essere fino fra gli grazie hanno loro molto negli nella nelle nello nostro ogni oltre però perché più poco
quale quando quanto quella quelle quelli quello questa queste questi questo sono stata stato suoi sulla
sulle sullo tale tanto tutti tutto una uno verso about after also been from have into more than that
their there these they this were which with would
""".split())


# --- Fonti: parsing, protezioni e download -----------------------------------------------------


class UnsafeSourceURL(ValueError):
    """URL non adatto a essere scaricato da un servizio di verifica."""


def _validate_source_url(url):
    """Accetta solo URL HTTP(S) pubblici, evitando richieste verso reti interne.

    La dashboard ascolta di default solo su localhost, ma questa protezione evita
    SSRF accidentali se l'API viene pubblicata dietro un proxy o con --host remoto.
    """
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeSourceURL("sono ammessi solo URL http:// o https://")
    if not parsed.hostname or parsed.username or parsed.password:
        raise UnsafeSourceURL("l'URL deve avere un host pubblico e non può contenere credenziali")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = {
            info[4][0]
            for info in socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
        }
    except (OSError, ValueError) as exc:
        raise UnsafeSourceURL("host non risolvibile") from exc
    if not addresses:
        raise UnsafeSourceURL("host non risolvibile")
    if any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise UnsafeSourceURL("gli indirizzi privati, locali o riservati non sono consentiti")
    return url


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Riapplica il controllo SSRF anche a ogni redirect HTTP."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_source_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

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


def _pdf_to_text(body):
    """Estrae testo da PDF entro limiti deliberatamente conservativi."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("supporto PDF non disponibile") from exc
    reader = PdfReader(io.BytesIO(body))
    text = "\n\n".join((page.extract_text() or "") for page in reader.pages[:MAX_PDF_PAGES])
    return text[:MAX_SOURCE_CHARS]


def _download_raw(url, timeout, max_bytes):
    """Esegue la richiesta HTTP e restituisce (status, content_type, body, charset)."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Skepsis/1.0",
        "Accept-Language": "it,en;q=0.8",
    })
    opener = urllib.request.build_opener(_SafeRedirectHandler())
    with opener.open(req, timeout=timeout) as response:
        status = str(response.getcode())
        content_type = response.headers.get("Content-Type", "")
        body = response.read(max_bytes + 1)
        charset = response.headers.get_content_charset() or "utf-8"
    return status, content_type, body, charset


def _decode_body(body, content_type, charset, max_bytes):
    """Converte i byte scaricati in testo (HTML/testo o PDF). Solleva ValueError se non è possibile."""
    if len(body) > max_bytes:
        raise ValueError(f"contenuto oltre {max_bytes // 1_000_000} MB")
    is_pdf = "pdf" in content_type.lower() or body.lstrip().startswith(b"%PDF-")
    is_text = "html" in content_type.lower() or "text" in content_type.lower()
    if is_pdf:
        return _pdf_to_text(body), "PDF"
    if is_text:
        return body.decode(charset, "ignore")[:MAX_SOURCE_CHARS], "HTML/testo"
    raise ValueError("formato non testuale")


WAYBACK_API = "https://archive.org/wayback/available?url={}"


def _wayback_snapshot_url(url, timeout=6):
    """Cerca l'ultimo snapshot di Wayback Machine per un URL, come riserva quando il sito
    blocca le richieste automatiche ma un umano può comunque raggiungerlo da browser."""
    try:
        api_url = WAYBACK_API.format(quote(url, safe=""))
        req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0 Skepsis/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8", "ignore"))
    except Exception:
        return None
    snapshot = data.get("archived_snapshots", {}).get("closest", {})
    if snapshot.get("available") and snapshot.get("url"):
        return snapshot["url"].replace("http://web.archive.org", "https://web.archive.org", 1)
    return None


def fetch_page(url, timeout=8, max_bytes=MAX_SOURCE_BYTES):
    """Scarica HTML, testo o PDF; blocca URL privati e contenuti troppo grandi.

    Se il sito blocca la richiesta con una verifica anti-bot (es. sfida Cloudflare),
    tenta di recuperare l'ultimo snapshot da Wayback Machine come riserva.
    """
    try:
        _validate_source_url(url)
    except UnsafeSourceURL as exc:
        return {"url": url, "status": f"URL non consentito: {exc}", "active": False,
                "content_available": False}, None
    try:
        status, content_type, body, charset = _download_raw(url, timeout, max_bytes)
    except HTTPError as e:
        status = str(e.code)
        server = (e.headers.get("Server") or "").lower()
        blocked_by_bot_check = e.code in (403, 503) and ("cloudflare" in server or e.headers.get("cf-mitigated"))
        if blocked_by_bot_check:
            snapshot_url = _wayback_snapshot_url(url)
            if snapshot_url:
                try:
                    _validate_source_url(snapshot_url)
                    _, snap_type, snap_body, snap_charset = _download_raw(snapshot_url, timeout, max_bytes)
                    text, source_type = _decode_body(snap_body, snap_type, snap_charset, max_bytes)
                    if text:
                        return {"url": url,
                                "status": f"{status} (bloccata dal sito, contenuto recuperato da Wayback Machine)",
                                "active": True, "content_available": True,
                                "source_type": f"{source_type} (Wayback Machine)"}, text
                except Exception:
                    pass
            status += " (bloccata da verifica anti-bot: la pagina può essere raggiungibile da un browser umano)"
        return {"url": url, "status": status, "active": False, "content_available": False}, None
    except URLError:
        return {"url": url, "status": "DNS / Irraggiungibile", "active": False, "content_available": False}, None
    except UnsafeSourceURL as exc:
        return {"url": url, "status": f"Redirect non consentito: {exc}", "active": False,
                "content_available": False}, None
    except Exception:
        return {"url": url, "status": "Errore", "active": False, "content_available": False}, None

    try:
        text, source_type = _decode_body(body, content_type, charset, max_bytes)
    except ValueError as exc:
        return {"url": url, "status": f"{status} ({exc})", "active": True, "content_available": False}, None
    except Exception:
        return {"url": url, "status": f"{status} (contenuto non leggibile)", "active": True,
                "content_available": False}, None
    return {"url": url, "status": status, "active": True, "content_available": bool(text),
            "source_type": source_type}, text or None


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
            results.append(({"url": url, "status": "Timeout", "active": False,
                             "content_available": False}, None))
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
        # Anche PDF e testo semplice passano da qui: senza tag HTML il buffer
        # finale va materializzato, altrimenti una fonte leggibile risulta vuota.
        parser._flush()
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

# Sopra questa soglia l'articolo viene diviso in blocchi ed estratto in parallelo:
# è la chiamata più lenta della pipeline (10-20 s) e la sua durata scala con la lunghezza del testo.
CHUNK_CHARS = 2200


def _split_into_chunks(article, chunk_chars=CHUNK_CHARS):
    """Divide l'articolo in blocchi sui confini di paragrafo, ciascuno entro chunk_chars."""
    paragraphs = [p for p in re.split(r"\n\s*\n", article) if p.strip()]
    chunks, current = [], ""
    for paragraph in paragraphs:
        if current and len(current) + len(paragraph) > chunk_chars:
            chunks.append(current)
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}" if current else paragraph
    if current:
        chunks.append(current)
    return chunks


def _extract_claims_llm_one(article, refs, max_claims, partial=False):
    """Una chiamata di estrazione. `partial` avvisa che il testo è solo un blocco dell'articolo."""
    sources = "\n".join(f"[{n}] {r['title']} {r['url']}" for n, r in refs.items()) or "(nessuna fonte)"
    context_note = (
        " Il testo è UN BLOCCO di un articolo più lungo: estrai solo le affermazioni di questo blocco, "
        "sostituendo comunque pronomi e riferimenti impliciti con ciò che indicano (nomi, soggetti) "
        "quando è chiaro dal blocco stesso."
        if partial else ""
    )
    prompt = (
        f"Estrai dal testo le affermazioni da verificare, al massimo {max_claims}, "
        f"nell'ordine in cui compaiono.{context_note}\n"
        "Regole:\n"
        "- ogni affermazione è atomica (un solo fatto) e comprensibile da sola: sostituisci pronomi e riferimenti impliciti;\n"
        "- riporta ciò che l'articolo afferma, comprese esagerazioni ed errori: non correggerlo e non attenuarlo;\n"
        '- "tipo": "fatto" se è verificabile con una fonte pubblicata (date, numeri, eventi, risultati, attribuzioni); '
        '"esperienza" se è un resoconto in prima persona di ciò che l\'autore ha fatto o osservato, che nessuna fonte pubblicata può verificare; '
        '"conclusione" se è un\'inferenza dell\'autore presentata come conseguenza dei fatti o delle fonti '
        '("dimostra che", "quindi", "significa che", "è confermato che", "è la prova che"); '
        '"opinione" se è un giudizio personale o una previsione dichiarata come tale. '
        'Se dividi una conclusione in più affermazioni atomiche, tutte le parti restano "conclusione", '
        'comprese le attribuzioni o i fatti che l\'autore presenta come conseguenza ("quindi", "dunque", dopo i due punti);\n'
        '- "fonti": i numeri delle fonti citate con [n] vicino all\'affermazione; se l\'articolo non usa [n], '
        "indica le fonti dell'elenco che trattano l'argomento, altrimenti lascia la lista vuota;\n"
        '- "parole_chiave": 4-8 termini per ritrovare il passaggio nelle fonti, in italiano e in inglese, '
        "compresi nomi propri, termini tecnici e numeri anche in forma inglese (es. 4.700 -> 4,700 e 4.7k; nove -> nine).\n\n"
        'Rispondi solo con JSON: {"affermazioni": [{"testo": "...", "tipo": "fatto", "fonti": ["1"], '
        '"parole_chiave": ["..."]}]}\n\n'
        f"FONTI:\n{sources}\n\nTESTO:\n{article}"
    )
    # temperatura 0: l'estrazione deve essere la più ripetibile possibile
    reply = llm.chat(prompt, timeout=120, json_mode=True, temperature=0)
    data = llm.parse_json(reply["text"])
    claims = []
    for item in data.get("affermazioni", [])[:max_claims]:
        text = str(item.get("testo", "")).strip()
        if not text:
            continue
        claims.append({
            "text": text,
            "kind": {"opin": "opinione", "espe": "esperienza", "conc": "conclusione"}.get(
                str(item.get("tipo", "")).lower()[:4], "fatto"),
            "refs": [str(r).strip("[] ") for r in item.get("fonti", []) if str(r).strip("[] ") in refs],
            "keywords": [str(k) for k in item.get("parole_chiave", [])][:10],
        })
    return claims, reply


def extract_claims_llm(article, refs):
    """Chiede a un LLM le affermazioni atomiche dell'articolo, a blocchi in parallelo se è lungo.

    Restituisce (affermazioni, metriche): seconds è il tempo di parete (i blocchi vanno in parallelo),
    token e costo sono la somma di tutte le chiamate."""
    chunks = _split_into_chunks(article)
    if len(chunks) <= 1:
        claims, reply = _extract_claims_llm_one(article, refs, MAX_CLAIMS)
        return claims, {k: v for k, v in reply.items() if k != "text"}

    per_chunk = max(4, MAX_CLAIMS // len(chunks) + 2)
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=len(chunks)) as pool:
        results = list(pool.map(lambda c: _extract_claims_llm_one(c, refs, per_chunk, partial=True), chunks))
    claims = [claim for claims_i, _ in results for claim in claims_i][:MAX_CLAIMS]
    replies = [reply for _, reply in results]
    metrics = {
        "model": replies[0]["model"],
        "seconds": round(time.perf_counter() - start, 3),
        "input_tokens": sum(r["input_tokens"] for r in replies),
        "output_tokens": sum(r["output_tokens"] for r in replies),
        "cost_cents": sum(r["cost_cents"] for r in replies),
        "chunks": len(chunks),
    }
    return claims, metrics


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


def candidate_paragraphs(query, paragraphs, limit=CANDIDATES):
    """I paragrafi della fonte che condividono più parole chiave e numeri con la ricerca."""
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
    return [paragraph for _, _, paragraph in scored[:limit]]


def best_excerpt(query, paragraphs, max_chars=EXCERPT_CHARS):
    """Selezione solo per parole chiave: i primi paragrafi candidati, fino a max_chars."""
    excerpt, used = [], 0
    for paragraph in candidate_paragraphs(query, paragraphs, limit=4):
        piece = paragraph[: max_chars - used]
        excerpt.append(piece)
        used += len(piece)
        if used >= max_chars:
            break
    return "\n".join(excerpt)


# --- Verifica -----------------------------------------------------------------------------------

def _select_with_jev(client, claim, candidates):
    """Una chiamata Jev con una domanda sì/no per ogni paragrafo candidato.

    `candidates` è una lista di (riferimento, paragrafo). Restituisce l'estratto scelto per
    ogni riferimento (vuoto se nessun paragrafo è pertinente) e i token usati."""
    state = f"AFFERMAZIONE:\n{claim['text']}\n\nPARAGRAFI CANDIDATI DALLE FONTI:\n"
    for i, (ref, paragraph) in enumerate(candidates):
        state += f"\nP{i} (fonte [{ref}]): {paragraph[:CANDIDATE_CHARS]}\n"
    questions = {
        f"p{i}": Noul(instructions=f"Il paragrafo P{i} contiene informazioni che permettono di confermare "
                                   "o smentire l'affermazione?")
        for i in range(len(candidates))
    }
    response = client.system_one(state=state, model="jev-latest", questions=questions)
    relevance = [(response.answers[f"p{i}"].noul, i) for i in range(len(candidates))]
    chosen = sorted((i for p, i in relevance if p >= RELEVANCE_THRESHOLD),
                    key=lambda i: -response.answers[f"p{i}"].noul)
    excerpts = {ref: [] for ref, _ in candidates}
    for i in chosen:
        ref, paragraph = candidates[i]
        if len(excerpts[ref]) < MAX_SELECTED:
            excerpts[ref].append(paragraph[:CANDIDATE_CHARS])
    return ({ref: "\n".join(parts) for ref, parts in excerpts.items()},
            response.usage.input_tokens, response.usage.output_tokens)


WORLD_QUESTION = ("Indipendentemente dagli estratti, l'affermazione è vera secondo le conoscenze storiche "
                  "e scientifiche consolidate?")
NUMBER_QUESTION = ("L'affermazione contiene numeri (date, quantità, misure). Gli estratti contengono "
                   "almeno uno di quei numeri, esattamente com'è nell'affermazione o con un arrotondamento "
                   "normale (es. 8.849 e circa 8.850 contano uguali)? Rispondi no se gli estratti riportano "
                   "un numero diverso per lo stesso dato, o se non contengono affatto quel numero.")


def _has_checkable_numbers(text):
    """Solo numeri abbastanza specifici da poter essere alterati in modo rilevabile."""
    return bool({n for n in _numbers(text) if n >= 10 or n != int(n)})


def _ask_jev(client, claim, sources, source_description="citate"):
    """Una chiamata Jev per affermazione: verdetto sugli estratti (se ci sono) e plausibilità
    secondo la conoscenza generale, utile quando la fonte manca o non tratta il punto."""
    state = f"AFFERMAZIONE:\n{claim['text']}\n"
    for ref, source in sources.items():
        state += f"\nESTRATTO DELLA FONTE [{ref}] ({source['url']}):\n{source['excerpt'] or '(nessun passaggio pertinente trovato)'}\n"
    questions = {"world": Noul(instructions=WORLD_QUESTION)}
    check_numbers = sources and _has_checkable_numbers(claim["text"])
    if check_numbers:
        questions["numbers"] = Noul(instructions=NUMBER_QUESTION)
    if sources:
        questions["verdict"] = Choice(
            instructions=f"Gli estratti delle fonti {source_description} sostengono l'affermazione?",
            criteria=VERDICTS,
        )
    response = client.system_one(state=state, model="jev-latest", questions=questions)
    result = {
        "world": response.answers["world"].noul,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    if check_numbers:
        result["number_match"] = response.answers["numbers"].noul
    if sources:
        answer = response.answers["verdict"]
        result.update(verdict=answer.choice, confidence=answer.confidence, probabilities=answer.probabilities)
    return result


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
    """Verifica ogni affermazione. `pages` mappa URL -> testo della fonte (None se irraggiungibile).

    Restituisce (righe, metriche Jev, metriche escalation)."""
    paragraphs = {url: html_to_paragraphs(content) for url, content in pages.items() if content}

    def check(claim):
        row = {"text": claim["text"], "kind": claim["kind"], "refs": claim["refs"], "verdict": None,
               "confidence": None, "decided_by": None, "reason": "", "excerpts": {}}
        if claim["kind"] == "opinione":
            return {**row, "verdict": OPINION}
        if claim["kind"] == "esperienza":
            return {**row, "verdict": EXPERIENCE}

        query = claim["text"] + " " + " ".join(claim.get("keywords", []))
        urls = {ref: refs[ref]["url"] for ref in claim["refs"] if ref in refs and paragraphs.get(refs[ref]["url"])}
        extra_in, extra_out, calls = 0, 0, 1
        if EXCERPT_SELECTION == "jev":
            candidates = [(ref, p) for ref, url in urls.items() for p in candidate_paragraphs(query, paragraphs[url])]
            chosen = {ref: "" for ref in urls}
            if candidates:
                chosen, extra_in, extra_out = _select_with_jev(client, claim, candidates)
                calls = 2
        else:
            chosen = {ref: best_excerpt(query, paragraphs[url]) for ref, url in urls.items()}
        sources = {ref: {"url": url, "excerpt": chosen.get(ref, "")} for ref, url in urls.items()}
        row["excerpts"] = {ref: s["excerpt"] for ref, s in sources.items()}

        result = _ask_jev(client, claim, sources)
        result.update(input_tokens=result["input_tokens"] + extra_in,
                      output_tokens=result["output_tokens"] + extra_out, jev_calls=calls)
        if not sources:
            return {**row, **result, "verdict": UNREACHABLE if claim["refs"] else NO_SOURCE}
        return {**row, **result, "decided_by": "Jev", "jev_verdict": result["verdict"], "_sources": sources}

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(check, claims))
    jev_rows = [r for r in rows if r.get("input_tokens")]
    jev = {
        "calls": sum(r.get("jev_calls", 1) for r in jev_rows),
        "seconds": round(time.perf_counter() - start, 3),
        "input_tokens": sum(r["input_tokens"] for r in jev_rows),
        "output_tokens": sum(r["output_tokens"] for r in jev_rows),
    }

    # Instradamento per confidenza: solo i casi incerti passano al LLM, il più incerto per primo
    escalation = {"model": ESCALATION_MODEL, "calls": 0, "seconds": 0.0, "input_tokens": 0,
                  "output_tokens": 0, "cost_cents": 0.0, "errors": []}
    uncertain = sorted((r for r in jev_rows if r.get("confidence") is not None and r["confidence"] < ESCALATION_THRESHOLD),
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
        for key in ("_sources", "probabilities", "input_tokens", "output_tokens", "jev_calls"):
            row.pop(key, None)
    return rows, jev, escalation


def verify_external_sources(client, claims, discovered_sources, pages):
    """Verifica separatamente fonti trovate su richiesta dall'utente.

    I risultati non confluiscono in `rate()`: chiariscono se un claim può essere
    riscontrato altrove, ma non trasformano una bibliografia carente in una buona.
    """
    by_claim = {}
    for source in discovered_sources:
        by_claim.setdefault(source["claim_index"], []).append(source)

    def check(item):
        index, claim = item
        sources = by_claim.get(index, [])
        if not sources:
            return None, None
        query = claim["text"] + " " + " ".join(claim.get("keywords", []))
        excerpts, displayed_sources = {}, []
        for source in sources:
            content = pages.get(source["url"]) or source.get("snippet", "")
            excerpt = best_excerpt(query, html_to_paragraphs(content)) or source.get("snippet", "")[:EXCERPT_CHARS]
            excerpts[source["id"]] = {"url": source["url"], "excerpt": excerpt}
            displayed_sources.append({
                "id": source["id"], "title": source["title"], "url": source["url"],
                "authority": source["authority"],
                "content_origin": "pagina" if pages.get(source["url"]) else "snippet di ricerca",
            })
        result = _ask_jev(client, claim, excerpts, source_description="esterne selezionate da Skepsis")
        return {
            "claim_index": index,
            "claim": claim["text"],
            "verdict": result.get("verdict", "Non trovata"),
            "confidence": result.get("confidence"),
            "world": result.get("world"),
            "number_match": result.get("number_match"),
            "sources": displayed_sources,
        }, result

    candidates = [(index, claim) for index, claim in enumerate(claims) if index in by_claim]
    def safe_check(item):
        try:
            return check(item), None
        except Exception as exc:
            return (None, None), str(exc)

    start = time.perf_counter()
    checks, raw_results, errors = [], [], []
    with ThreadPoolExecutor(max_workers=min(4, len(candidates) or 1)) as pool:
        for (row, raw), error in pool.map(safe_check, candidates):
            if error:
                errors.append(error)
            elif row:
                checks.append(row)
                raw_results.append(raw)
    metrics = {
        "calls": len(raw_results),
        "seconds": round(time.perf_counter() - start, 3),
        "input_tokens": sum(result["input_tokens"] for result in raw_results),
        "output_tokens": sum(result["output_tokens"] for result in raw_results),
        "errors": errors,
    }
    return checks, metrics


def summarize(rows):
    """Riepilogo testuale dei verdetti, da passare alla valutazione globale e al LLM."""
    lines = []
    for i, row in enumerate(rows, 1):
        conf = f" {row['confidence']:.0%}" if row.get("confidence") is not None else ""
        by = f", deciso da {row['decided_by']}" if row.get("decided_by") == "LLM" else ""
        if row.get("world") is not None:
            by += f", plausibilità {row['world']:.0%}"
        if row["kind"] == "conclusione":
            by += ", conclusione dell'autore"
        refs = "".join(f"[{r}]" for r in row["refs"])
        lines.append(f"{i}. [{row['verdict']}{conf}{by}] {row['text']} {refs}".rstrip())
    return "\n".join(lines) or "(nessuna affermazione estratta)"


def counts(rows):
    """Quante affermazioni per ciascun verdetto."""
    result = {}
    for row in rows:
        result[row["verdict"]] = result.get(row["verdict"], 0) + 1
    return result


# --- Voto finale con regole esplicite ------------------------------------------------------------

SUPPORTED = {"Sostenuta", "Parzialmente sostenuta"}
UNVERIFIED = {"Non trovata", NO_SOURCE, UNREACHABLE}
ENCYCLOPEDIC = ("wikipedia.org",)


def rate(rows, refs):
    """Calcola le stelle dai verdetti delle singole affermazioni.

    Restituisce (stelle, motivo) oppure (None, motivo) se non ci sono fatti da valutare."""
    checked = [r for r in rows if r["kind"] in ("fatto", "conclusione")]
    if not checked:
        return None, "nessun fatto verificabile: voto affidato alla valutazione globale di Jev"

    def world(row):
        return row.get("world", 0.5)

    def only_unreachable(row):
        return row["verdict"] == UNREACHABLE

    facts = [r for r in checked if r["kind"] == "fatto"]
    conclusions = [r for r in checked if r["kind"] == "conclusione"]

    # Fatti gravemente problematici: implausibili secondo Jev, contraddetti dalle fonti,
    # con numeri alterati, o citati solo da fonti irraggiungibili e implausibili.
    # Una singola bandierina rossa può essere un errore isolato di giudizio (il modello non
    # conosce un evento recente, un estratto è ambiguo): serve un pattern per bocciare tutto
    # l'articolo a 1 stella; un singolo caso scende comunque a 2.
    very_implausible = [r for r in facts if world(r) < 0.15]
    contradicted = [r for r in facts if r["verdict"] == "Contraddetta" and world(r) < 0.5]
    altered_numbers = [r for r in facts
                        if r.get("number_match") is not None and r["number_match"] < 0.35 and world(r) < 0.65]
    unreachable_implausible = [r for r in checked if only_unreachable(r) and world(r) < 0.3]

    seen, serious = set(), []
    for group in (very_implausible, contradicted, altered_numbers, unreachable_implausible):
        for r in group:
            if id(r) not in seen:
                seen.add(id(r))
                serious.append(r)

    # 1 stella: più fatti gravemente problematici (problema sistemico, non un singolo errore isolato)
    if len(serious) >= 2:
        return 1, f"più fatti gravemente problematici, tra cui: «{serious[0]['text']}»"
    # 2 stelle: un singolo fatto problematico isolato, o conclusioni non giustificate
    if serious:
        return 2, f"fatto problematico, ma isolato: «{serious[0]['text']}»"
    for r in conclusions:
        if r["verdict"] in ("Esagerata", "Contraddetta") or (r["verdict"] in UNVERIFIED and world(r) < 0.5) or world(r) < 0.15:
            return 2, f"conclusione non giustificata dalle fonti: «{r['text']}»"
    for r in facts:
        # un fatto che Jev ritiene vero non è una distorsione, anche se l'estratto lo copre solo in parte
        if r["verdict"] == "Esagerata" and world(r) < 0.5:
            return 2, f"fonte distorta: «{r['text']}»"

    # 3-5 stelle: quota di affermazioni confermate dalle fonti
    supported = [r for r in checked if r["verdict"] in SUPPORTED]
    share = len(supported) / len(checked)
    cited = {ref for r in checked for ref in r["refs"]}
    only_encyclopedic = cited and all(any(d in refs.get(ref, {}).get("url", "") for d in ENCYCLOPEDIC) for ref in cited)
    fully = all(r["verdict"] == "Sostenuta" for r in checked)
    if fully and len(checked) >= 3 and not only_encyclopedic:
        return 5, "tutte le affermazioni sono confermate da fonti primarie"
    if share >= 0.6:
        note = " (fonti solo enciclopediche: massimo 4 stelle)" if fully and only_encyclopedic else ""
        return 4, f"{len(supported)} affermazioni su {len(checked)} confermate dalle fonti{note}"
    return 3, f"solo {len(supported)} affermazioni su {len(checked)} confermate dalle fonti"
