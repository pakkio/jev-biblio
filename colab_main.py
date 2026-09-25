# @title 📊 Bibliography & Claim Verifier Dashboard {display-mode: "form"}
import sys
import subprocess

# Force dynamic installation of typesafe-sdk at runtime start to prevent ImportErrors
try:
    from typesafe_sdk import Choice, TypeSafeClient
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "typesafe-sdk"])
    import site
    from importlib import reload
    reload(site)
    from typesafe_sdk import Choice, TypeSafeClient

import json
import re
import urllib.request
from urllib.error import URLError, HTTPError
from IPython.display import HTML
from google.colab import output
from google.colab import userdata

# 1. Register JS error callback helper
def _report_js_error(message):
    print(f"JavaScript Error: {message}")

output.register_callback('report_js_error', _report_js_error)

# 2. Main verifier logic called from the frontend
def run_bibliography_verifier(article_text, bibliography_raw):
    try:
        # Retrieve key safely from userdata inside the function execution context
        api_key = userdata.get('TYPESAFE_API_KEY')
        if not api_key:
            return json.dumps({"error": "API Key 'TYPESAFE_API_KEY' not found in Colab Secrets."})

        # Extract unique URLs from raw bibliography input
        urls = re.findall(r'https?://[^\s<>"\x7f-\xff]+', bibliography_raw)
        link_statuses = []

        # Check up to 15 URLs for live connection (non-404)
        for url in list(set(urls))[:15]:
            clean_url = url.strip().rstrip('.,;()[]{}')
            status_code = "N/A"
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
                status_code = "DNS / Unreachable"
            except Exception as e:
                status_code = "Error"

            link_statuses.append({
                "url": clean_url,
                "status": status_code,
                "active": is_active
            })

        # Use Jev to assess alignment, citation relevance, and bibliography quality rating
        client = TypeSafeClient(api_key=api_key)
        state_data = "ARTICLE CONTENT:\n" + str(article_text) + "\n\nBIBLIOGRAPHY/SOURCES:\n" + str(bibliography_raw)

        response = client.system_one(
            state=state_data,
            model="jev-latest",
            questions={
                "relation_rating": Choice(
                    instructions="Are the bibliography resources highly related and topic-coherent with the main thesis of the article?",
                    criteria={
                        "Excellent": "The resources are highly relevant, authoritative, and closely support the core topic.",
                        "Fair": "The resources are tangentially related to the topic but lack direct integration or look superficial.",
                        "Poor": "The resources are irrelevant, mismatched, or fail to support the article's context."
                    }
                ),
                "claim_adherence": Choice(
                    instructions="How accurately does the article adhere to, verify, or represent the claims made in its cited bibliography?",
                    criteria={
                        "Strong Adherence": "The article correctly references its source material without hyperbole or fabrication.",
                        "Partial Mismatch": "Some citations are stretched, exaggerated, or represent minor misalignments with the source material.",
                        "Severe Hallucination": "The article misrepresents critical facts or cites sources that don't back up the corresponding statements."
                    }
                ),
                "overall_score": Choice(
                    instructions="Rate the quality and reliability of this bibliography on a 1-5 scale.",
                    criteria={
                        "5 (Excellent)": "Perfect match, verified sources, exceptional topical relevance and clean claims.",
                        "4 (Very Good)": "Highly reliable with very minor formatting or structural disconnects.",
                        "3 (Satisfactory)": "Acceptable relationship, but lacks deep relevance or robust citations.",
                        "2 (Weak)": "Many unsupported claims and loosely related sources.",
                        "1 (Unacceptable)": "Completely misleading bibliography, invalid claims, or unrelated links."
                    }
                ),
                "justification": Choice(
                    instructions="Provide an analytical explanation supporting these choices.",
                    criteria={
                        "explanation": "Provide critical reasoning referencing specific themes, potential vulnerabilities, or strengths observed."
                    }
                )
            }
        )

        result = {
            "links": link_statuses,
            "relation": response.answers["relation_rating"].choice,
            "adherence": response.answers["claim_adherence"].choice,
            "rating": response.answers["overall_score"].choice,
            "explanation": response.answers["justification"].choice
        }
        return json.dumps(result)

    except Exception as e:
        return json.dumps({"error": str(e)})

# 3. Register the verifier logic to Colab output callbacks
output.register_callback('run_bibliography_verifier', run_bibliography_verifier)

# 4. Raw unified html string to avoid f-string parsing issues in notebooks
html_code = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Bibliography Verifier</title>
    <!-- Bootstrap CSS -->
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
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
        <h2 class="mb-4" style="color: #1e1b4b; font-weight: 700;">📚 Live Bibliography & Claim Verifier</h2>

        <!-- Inputs -->
        <div class="card p-4">
            <div class="row">
                <div class="col-md-6 mb-3">
                    <label class="form-label fw-bold">Article Text & Claims</label>
                    <textarea id="article-input" class="form-control" rows="5" placeholder="Paste the text or claims of the article here...">Quantum computing is expected to revolutionize chemistry by simulating complex molecules. A 2021 study by IBM researchers proved quantum supremacy in this space, claiming a 50-qubit processor calculated molecular ground state energies with 99.9% fidelity.</textarea>
                </div>
                <div class="col-md-6 mb-3">
                    <label class="form-label fw-bold">Bibliography References</label>
                    <textarea id="bib-input" class="form-control" rows="5" placeholder="Paste references and bibliography sources with URLs...">- Watson, H. Simulating molecules on classic hardware. https://httpbin.org/status/200
- Smith, J. Quantum Simulations in Chemistry (2021). Nature. https://httpbin.org/status/404
- Miller, A. False claims on Quantum Computing. (2022). https://httpbin.org/status/500</textarea>
                </div>
            </div>
            <button class="btn btn-primary mt-2" id="btn-run" style="background-color:#4f46e5; border:none;">Run Jev Analysis & Live Checks</button>
        </div>

        <!-- Loading status indicator -->
        <div id="loader" class="alert alert-info d-none" role="alert">
            🔄 Checking bibliography URLs and utilizing Jev's evaluation engine to parse alignment... Please wait (around 15 seconds).
        </div>

        <!-- Dashboard Grid of Results -->
        <div id="dashboard-results" class="d-none">
            <div class="row mb-3">
                <div class="col-md-3">
                    <div class="card kpi-card">
                        <div class="kpi-title">Total References</div>
                        <div class="kpi-val" id="kpi-total">0</div>
                    </div>
                </div>
                <div class="col-md-3">
                    <div class="card kpi-card">
                        <div class="kpi-title">Active Links (Non-404)</div>
                        <div class="kpi-val text-success" id="kpi-active">0</div>
                    </div>
                </div>
                <div class="col-md-3">
                    <div class="card kpi-card">
                        <div class="kpi-title">Claim Adherence</div>
                        <div class="kpi-val text-primary" id="kpi-adherence">-</div>
                    </div>
                </div>
                <div class="col-md-3">
                    <div class="card kpi-card">
                        <div class="kpi-title">Biblio Quality Rating</div>
                        <div class="kpi-val text-warning" id="kpi-rating">-</div>
                    </div>
                </div>
            </div>

            <div class="row">
                <!-- Left Column: Table of live verified links -->
                <div class="col-md-6">
                    <div class="card">
                        <div class="card-header">URL Live Status Validation</div>
                        <div class="card-body">
                            <div class="table-responsive">
                                <table class="table table-sm align-middle">
                                    <thead>
                                        <tr>
                                            <th>URL Source</th>
                                            <th>HTTP Status</th>
                                            <th>Active</th>
                                        </tr>
                                    </thead>
                                    <tbody id="links-table-body"></tbody>
                                </table>
                             </div>
                        </div>
                    </div>
                </div>

                <!-- Right Column: Jev evaluation and context explanations -->
                <div class="col-md-6">
                    <div class="card">
                        <div class="card-header">Jev Semantic Assessment</div>
                        <div class="card-body">
                            <div class="mb-3">
                                <strong>Bibliography Relevance:</strong> <span class="badge bg-info" id="eval-relation">-</span>
                            </div>
                            <div class="mb-3">
                                <strong>Jev Analytical Justification:</strong>
                                <p class="mt-1 text-muted" id="eval-explanation" style="font-size:0.92rem; line-height:1.5;"></p>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        window.onerror = function(message) {
            google.colab.kernel.invokeFunction('report_js_error', [message], {});
        };

        document.getElementById('btn-run').addEventListener('click', function() {
            const articleText = document.getElementById('article-input').value;
            const bibText = document.getElementById('bib-input').value;

            if(!articleText || !bibText) {
                alert("Please provide both the Article Text and the Bibliography references.");
                return;
            }

            document.getElementById('loader').classList.remove('d-none');
            document.getElementById('dashboard-results').classList.add('d-none');

            google.colab.kernel.invokeFunction('run_bibliography_verifier', [articleText, bibText], {})
                .then(response => {
                    document.getElementById('loader').classList.add('d-none');

                    let data;
                    try {
                        if (!response || !response.data) {
                            throw new Error("Empty response object received from backend.");
                        }
                        data = JSON.parse(response.data['application/json']);
                    } catch(e) {
                        alert("Error parsing response: " + e.message);
                        return;
                    }

                    if(data.error) {
                        alert("Error running verification pipeline: " + data.error);
                        return;
                    }

                    // Populate summary cards
                    const totalLinks = (data.links || []).length;
                    const activeLinks = (data.links || []).filter(l => l.active).length;
                    document.getElementById('kpi-total').textContent = totalLinks;
                    document.getElementById('kpi-active').textContent = activeLinks + " / " + totalLinks;
                    document.getElementById('kpi-adherence').textContent = data.adherence || '-';
                    document.getElementById('kpi-rating').textContent = data.rating || '-';

                    // Populate links table
                    const tableBody = document.getElementById('links-table-body');
                    tableBody.innerHTML = '';
                    (data.links || []).forEach(link => {
                        const row = document.createElement('tr');
                        const badgeClass = link.active ? 'bg-success' : 'bg-danger';
                        row.innerHTML = `
                            <td><a href="${link.url}" target="_blank" class="text-truncate d-inline-block" style="max-width:280px;">${link.url}</a></td>
                            <td><code>${link.status}</code></td>
                            <td><span class="badge ${badgeClass}">${link.active ? 'LIVE' : 'DOWN'}</span></td>
                        `;
                        tableBody.appendChild(row);
                    });

                    // Populate semantic results
                    document.getElementById('eval-relation').textContent = data.relation || '-';
                    document.getElementById('eval-explanation').textContent = data.explanation || '-';

                    document.getElementById('dashboard-results').classList.remove('d-none');
                }).catch(err => {
                    document.getElementById('loader').classList.add('d-none');
                    alert("Process failed: " + err);
                });
        });
    </script>
</body>
</html>
"""

# Render dashboard directly within the Colab output pane
HTML(html_code)

