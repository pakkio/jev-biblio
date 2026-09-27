import io
import json
import socket
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import claims
import discovery
import main
from pypdf import PdfWriter


class SourceUrlValidationTests(unittest.TestCase):
    @patch("claims.socket.getaddrinfo")
    def test_accepts_a_public_http_url(self, getaddrinfo):
        getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
        ]

        self.assertEqual(
            claims._validate_source_url("https://example.org/article"),
            "https://example.org/article",
        )

    @patch("claims.socket.getaddrinfo")
    def test_rejects_a_private_resolved_address(self, getaddrinfo):
        getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 80)),
        ]

        with self.assertRaises(claims.UnsafeSourceURL):
            claims._validate_source_url("http://internal.example")

    def test_rejects_credentials_and_non_http_schemes(self):
        with self.assertRaises(claims.UnsafeSourceURL):
            claims._validate_source_url("https://user:password@example.org")
        with self.assertRaises(claims.UnsafeSourceURL):
            claims._validate_source_url("file:///etc/passwd")

    def test_fetch_page_refuses_localhost_before_downloading(self):
        status, text = claims.fetch_page("http://127.0.0.1:8000/private")

        self.assertFalse(status["active"])
        self.assertFalse(status["content_available"])
        self.assertIn("URL non consentito", status["status"])
        self.assertIsNone(text)


class HtmlExtractionTests(unittest.TestCase):
    def test_html_extraction_skips_navigation_and_keeps_content(self):
        html = "<nav>menu non rilevante</nav><p>Una fonte contiene abbastanza testo da essere utile.</p>"

        self.assertEqual(claims.html_to_paragraphs(html), ["Una fonte contiene abbastanza testo da essere utile."])

    def test_plain_text_is_extracted_too(self):
        text = "Una fonte di testo semplice contiene abbastanza parole da essere verificata correttamente."

        self.assertEqual(claims.html_to_paragraphs(text), [text])


class PdfSupportTests(unittest.TestCase):
    def test_pdf_text_extraction_accepts_a_valid_pdf(self):
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        buffer = io.BytesIO()
        writer.write(buffer)

        self.assertEqual(claims._pdf_to_text(buffer.getvalue()), "")


class InputLimitTests(unittest.TestCase):
    def test_rejects_empty_and_oversized_input_before_calling_services(self):
        empty = json.loads(main.run_bibliography_verifier("", "https://example.org"))
        oversized = json.loads(
            main.run_bibliography_verifier("x" * (main.MAX_ARTICLE_CHARS + 1), "https://example.org")
        )

        self.assertIn("Inserisci", empty["error"])
        self.assertIn("troppo lungo", oversized["error"])


class DiscoveryTests(unittest.TestCase):
    @patch.dict("os.environ", {"TAVILY_API_KEY": "test-key"}, clear=False)
    @patch("discovery.urllib.request.urlopen")
    def test_search_keeps_only_domains_allowed_by_policy(self, urlopen):
        response = SimpleNamespace(read=lambda: json.dumps({"results": [
            {"url": "https://climate.nasa.gov/evidence/", "title": "NASA", "content": "evidenza"},
            {"url": "https://example.org/post", "title": "Blog", "content": "non ammesso"},
        ]}).encode("utf-8"))
        urlopen.return_value.__enter__.return_value = response

        sources = discovery.search_authoritative_sources({"text": "Il clima cambia"})

        self.assertEqual([source["url"] for source in sources], ["https://climate.nasa.gov/evidence/"])
        request = urlopen.call_args.args[0]
        self.assertTrue(json.loads(request.data)["safe_search"])

    def test_policy_does_not_accept_gov_as_an_arbitrary_subdomain(self):
        self.assertIsNone(discovery.authority_label("https://gov.example.org/page"))
        self.assertEqual(discovery.authority_label("https://www.nasa.gov/page"), "agenzia pubblica")


class ArticleBibliographySplitTests(unittest.TestCase):
    def test_heuristic_splits_on_a_heading_followed_by_a_url(self):
        raw = ("Testo dell'articolo, con più di una frase normale.\n\n"
               "Riferimenti:\n\n[1] Una fonte https://example.org/a\n[2] Un'altra fonte https://example.org/b")

        article, bibliography = claims._split_article_bibliography_heuristic(raw)

        self.assertTrue(article.startswith("Testo dell'articolo"))
        self.assertTrue(bibliography.startswith("Riferimenti:"))
        self.assertIn("example.org/a", bibliography)

    def test_heuristic_leaves_plain_articles_untouched(self):
        raw = "Solo testo dell'articolo, senza alcun elenco di fonti o titolo di sezione."

        article, bibliography = claims._split_article_bibliography_heuristic(raw)

        self.assertEqual(article, raw)
        self.assertEqual(bibliography, "")

    def test_jev_split_cuts_at_the_first_confirmed_bibliography_paragraph(self):
        class Client:
            def system_one(self, **_kwargs):
                probabilities = [0.1, 0.1, 0.85, 0.9, 0.9]
                return SimpleNamespace(
                    answers={f"p{i}": SimpleNamespace(noul=p) for i, p in enumerate(probabilities)},
                    usage=SimpleNamespace(input_tokens=50, output_tokens=5),
                )

        raw = ("Un paragrafo dell'articolo.\n\nUn secondo paragrafo dell'articolo.\n\nRiferimenti:\n\n"
               "[1] Una fonte https://example.org/a\n\n[2] Un'altra fonte https://example.org/b")

        article, bibliography, metrics = claims.split_article_and_bibliography(Client(), raw)

        self.assertIn("secondo paragrafo", article)
        self.assertNotIn("secondo paragrafo", bibliography)
        self.assertIn("example.org/a", bibliography)
        self.assertEqual(metrics, {"input_tokens": 50, "output_tokens": 5})

    def test_jev_split_returns_no_bibliography_when_nothing_is_flagged(self):
        class Client:
            def system_one(self, **_kwargs):
                return SimpleNamespace(
                    answers={f"p{i}": SimpleNamespace(noul=0.05) for i in range(3)},
                    usage=SimpleNamespace(input_tokens=30, output_tokens=4),
                )

        raw = "Primo paragrafo.\n\nSecondo paragrafo.\n\nTerzo paragrafo, tutto articolo, nessuna fonte."

        article, bibliography, metrics = claims.split_article_and_bibliography(Client(), raw)

        self.assertEqual(article, raw)
        self.assertEqual(bibliography, "")

    def test_too_few_paragraphs_skips_jev_and_uses_the_heuristic(self):
        class Client:
            def system_one(self, **_kwargs):
                raise AssertionError("Jev non dovrebbe essere chiamato per un unico paragrafo")

        raw = "Un solo paragrafo, senza alcuna bibliografia."

        article, bibliography, metrics = claims.split_article_and_bibliography(Client(), raw)

        self.assertEqual(article, raw)
        self.assertEqual(bibliography, "")
        self.assertIsNone(metrics)


class SourceAuthorityTests(unittest.TestCase):
    def test_nonexistent_source_is_flagged_without_calling_jev(self):
        class Client:
            def system_one(self, **_kwargs):
                raise AssertionError("una fonte inesistente non deve arrivare a Jev")

        refs = {"1": {"url": "https://blog-esempio-inesistente.invalid/post", "title": "Blog"}}
        link_statuses = [{"url": refs["1"]["url"], "status": "DNS / Irraggiungibile"}]

        result, in_tok, out_tok = claims.rate_source_authority(Client(), refs, link_statuses, {})

        self.assertEqual(result["1"]["label"], "Inaffidabile")
        self.assertEqual(result["1"]["color"], "rosso")
        self.assertEqual((in_tok, out_tok), (0, 0))

    def test_reachable_source_is_classified_by_jev(self):
        class Client:
            def system_one(self, **_kwargs):
                return SimpleNamespace(
                    answers={"authority": SimpleNamespace(choice="Autorevole", confidence=0.85)},
                    usage=SimpleNamespace(input_tokens=40, output_tokens=6),
                )

        refs = {"1": {"url": "https://www.nasa.gov/result", "title": "NASA"}}
        link_statuses = [{"url": refs["1"]["url"], "status": "200"}]

        result, in_tok, out_tok = claims.rate_source_authority(Client(), refs, link_statuses, {})

        self.assertEqual(result["1"], {"label": "Autorevole", "color": "verde", "confidence": 0.85})
        self.assertEqual((in_tok, out_tok), (40, 6))


class ExternalVerificationTests(unittest.TestCase):
    def test_external_check_uses_page_text_and_stays_separate(self):
        class Client:
            def system_one(self, **_kwargs):
                return SimpleNamespace(
                    answers={
                        "world": SimpleNamespace(noul=0.9),
                        "verdict": SimpleNamespace(choice="Sostenuta", confidence=0.8, probabilities={}),
                    },
                    usage=SimpleNamespace(input_tokens=11, output_tokens=3),
                )

        claim_list = [{"text": "La fonte conferma questo risultato.", "kind": "fatto", "keywords": ["risultato"]}]
        sources = [{"id": "E1-1", "claim_index": 0, "url": "https://www.nasa.gov/result",
                    "title": "NASA", "authority": "agenzia pubblica", "snippet": ""}]
        pages = {"https://www.nasa.gov/result": "La fonte conferma questo risultato con dati pubblici e riproducibili."}

        checks, metrics = claims.verify_external_sources(Client(), claim_list, sources, pages)

        self.assertEqual(checks[0]["verdict"], "Sostenuta")
        self.assertEqual(checks[0]["sources"][0]["content_origin"], "pagina")
        self.assertEqual(metrics["input_tokens"], 11)


if __name__ == "__main__":
    unittest.main()
