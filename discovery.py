"""Ricerca opzionale di fonti esterne per i claim non coperti dalla bibliografia.

La ricerca non modifica il voto dell'articolo: serve soltanto come riscontro
separato, esplicitamente etichettato, quando l'utente la richiede.
"""
import json
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
MAX_CLAIMS = 5
MAX_SOURCES_PER_CLAIM = 2

# Una policy trasparente, non un'affermazione assoluta di autorevolezza. I domini
# non inclusi non vengono proposti automaticamente, anche se sono ben posizionati.
TRUSTED_DOMAINS = {
    "acm.org": "società scientifica",
    "bmj.com": "rivista medica",
    "cdc.gov": "sanità pubblica",
    "cochranelibrary.com": "revisione sistematica",
    "esa.int": "agenzia spaziale",
    "europa.eu": "istituzione UE",
    "ieee.org": "società scientifica",
    "nejm.org": "rivista medica",
    "nasa.gov": "agenzia pubblica",
    "nature.com": "rivista scientifica",
    "nih.gov": "istituto sanitario pubblico",
    "noaa.gov": "agenzia pubblica",
    "science.org": "rivista scientifica",
    "thelancet.com": "rivista medica",
    "usgs.gov": "agenzia pubblica",
    "who.int": "organizzazione internazionale",
}


class DiscoveryError(RuntimeError):
    """Errore recuperabile della ricerca di fonti esterne."""


def available():
    return bool(os.getenv("TAVILY_API_KEY"))


def authority_label(url):
    """Restituisce il criterio di inclusione del dominio, altrimenti None."""
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    for domain, label in TRUSTED_DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            return label
    # `.gov` è un suffisso, non basta che la stringa compaia in un sottodominio
    # controllato da terzi (per esempio `gov.example.org`).
    if host.endswith(".gov"):
        return "ente pubblico"
    if host.endswith(".edu") or host.endswith(".ac.uk"):
        return "università o ricerca"
    return None


def search_authoritative_sources(claim, limit=MAX_SOURCES_PER_CLAIM):
    """Cerca e filtra risultati secondo la policy di dominio dichiarata sopra."""
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        raise DiscoveryError("TAVILY_API_KEY non impostata")
    payload = {
        "query": f"{claim['text']} fonte ufficiale o scientifica primaria",
        "search_depth": "advanced",
        "chunks_per_source": 2,
        "max_results": 8,
        "topic": "general",
        "include_answer": False,
        "include_raw_content": False,
        "safe_search": True,
    }
    request = urllib.request.Request(
        TAVILY_SEARCH_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read())
    except Exception as exc:
        raise DiscoveryError(f"ricerca Tavily non riuscita: {exc}") from exc

    seen, sources = set(), []
    for result in data.get("results", []):
        url = result.get("url", "")
        authority = authority_label(url)
        if not authority or url in seen:
            continue
        seen.add(url)
        sources.append({
            "url": url,
            "title": result.get("title") or url,
            "snippet": (result.get("content") or "")[:1_500],
            "authority": authority,
        })
        if len(sources) >= limit:
            break
    return sources


def discover_for_claims(claims):
    """Ricerca fino a cinque claim in parallelo, preservandone l'indice."""
    selected = list(claims)[:MAX_CLAIMS]
    if not selected:
        return [], []

    def one(item):
        index, claim = item
        try:
            return index, search_authoritative_sources(claim), None
        except DiscoveryError as exc:
            return index, [], str(exc)

    with ThreadPoolExecutor(max_workers=min(3, len(selected))) as pool:
        outcomes = list(pool.map(one, selected))

    sources, errors = [], []
    for index, found, error in outcomes:
        for ordinal, source in enumerate(found, 1):
            sources.append({**source, "id": f"E{index + 1}-{ordinal}", "claim_index": index})
        if error:
            errors.append(error)
    return sources, errors
