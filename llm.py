"""Chiamate a OpenRouter: scelta dei modelli, costi e tempi."""
import json
import os
import re
import time
import urllib.request

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
# Modello principale: economico, veloce e ottimo in italiano (usato con ragionamento disattivato)
OPENROUTER_PRIMARY = "openai/gpt-6-luna"
# Ultima riserva se non risponde nessun altro modello
OPENROUTER_FALLBACK = "openrouter/free"
MIN_FLASH_LITE_VERSION = (3, 5)
_chain = None
_pricing = {}


def primary_model():
    """Modello principale della catena (quello che dovrebbe rispondere)."""
    return model_chain()[0]


def model_chain():
    """Catena di modelli: principale (OPENROUTER_MODEL o gpt-6-luna),
    poi il Gemini Flash Lite più recente con versione >= 3.5, infine openrouter/free."""
    global _chain
    if _chain:
        return _chain
    primary = os.getenv("OPENROUTER_MODEL", OPENROUTER_PRIMARY)
    try:
        with urllib.request.urlopen(OPENROUTER_MODELS_URL, timeout=15) as response:
            models = json.loads(response.read())["data"]
        for m in models:
            _pricing[m["id"]] = m.get("pricing", {})
        candidates = []
        for m in models:
            match = re.fullmatch(r"google/gemini-(\d+)\.(\d+)-flash-lite", m["id"])
            if match and (int(match[1]), int(match[2])) >= MIN_FLASH_LITE_VERSION:
                candidates.append(((int(match[1]), int(match[2])), m["id"]))
        flash_lite = max(candidates)[1] if candidates else None
    except Exception as e:
        print(f"[AVVISO] Impossibile leggere l'elenco modelli di OpenRouter: {e}")
        flash_lite = None
    _chain = _dedupe([primary, flash_lite, OPENROUTER_FALLBACK])
    return _chain


def _dedupe(models):
    return [m for i, m in enumerate(models) if m and m not in models[:i]]


def cost_cents(model, usage):
    """Costo in centesimi: usa usage.cost di OpenRouter, altrimenti lo calcola dal listino."""
    if usage.get("cost") is not None:
        return float(usage["cost"]) * 100
    pricing = _pricing.get(model, {})
    usd = (usage.get("prompt_tokens", 0) * float(pricing.get("prompt", 0))
           + usage.get("completion_tokens", 0) * float(pricing.get("completion", 0)))
    return usd * 100


def available():
    return bool(os.getenv("OPENROUTER_API_KEY"))


def chat(prompt, model=None, reasoning=False, timeout=90, json_mode=False):
    """Una chiamata di chat. `model` sceglie un modello specifico, con la catena come riserva;
    `json_mode` chiede una risposta in JSON valido.

    Restituisce testo, modello effettivo, secondi, token e costo in centesimi."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY non impostata")
    # OpenRouter accetta al massimo 3 modelli nella lista di riserva
    models = _dedupe([model] + model_chain())[:3]
    body = {
        "model": models[0],
        # se il modello principale non risponde, OpenRouter prova i successivi
        "models": models,
        # senza ragionamento nascosto le risposte sono più rapide ed economiche;
        # per i casi difficili si chiede un ragionamento breve
        "reasoning": {"effort": "low"} if reasoning else {"enabled": False},
        "usage": {"include": True},
        "messages": [
            {"role": "system", "content": "Rispondi sempre e solo in italiano."},
            {"role": "user", "content": prompt},
        ],
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(
        OPENROUTER_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-Title": "Verificatore Bibliografia",
        },
    )
    start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as response:
        data = json.loads(response.read())
    seconds = time.perf_counter() - start
    text = (data["choices"][0]["message"].get("content") or "").strip()
    if not text:
        raise RuntimeError("risposta vuota dal modello")
    used_model = data.get("model", models[0])
    usage = data.get("usage") or {}
    return {
        "text": text,
        "model": used_model,
        "seconds": round(seconds, 3),
        "input_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
        "cost_cents": cost_cents(used_model, usage),
    }


def parse_json(text):
    """Estrae il primo oggetto JSON da una risposta (anche se racchiuso in ```json ... ```)."""
    start = text.find("{")
    if start < 0:
        raise ValueError(f"nessun JSON nella risposta: {text[:120]}")
    # raw_decode ignora eventuale testo dopo la fine dell'oggetto
    return json.JSONDecoder().raw_decode(text[start:])[0]
