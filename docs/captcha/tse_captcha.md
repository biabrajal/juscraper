---
title: TSE — hCaptcha (invisible), token required per request
type: supported-via-browser-automation
---

# TSE Pesquisa de Jurisprudência (sjur-pesquisa)

The TSE jurisprudence search at
<https://www.tse.jus.br/jurisprudencia/pesquisa-de-jurisprudencia> is a
thin portal page that links out to the real SPA at
<https://jurisprudencia.tse.jus.br/#/jurisprudencia/pesquisa>
("sjur-pesquisa", v2.0.3-5). That SPA talks to
`https://sjur-pesquisa-api.tse.jus.br/tse/sjur-pesquisa-backend/rest/public/pesquisa`.

The search form itself is rich (free text with Solr/Lucene-style
operators, `Nº Processo/Nº Decisão/Nº Único/Nº Protocolo`, relatoria,
classe, eleição, ementa/decisão/indexação, observações, partes, data
inicial/final with a julgamento/publicação toggle, legislação filters,
tipo de decisão checkboxes) and the search POST body is a plain JSON
Elasticsearch `query_string` bool query — straightforward to
reverse-engineer.

The blocker is that every `POST .../rest/public/pesquisa` call must
carry a `captchaToken` field populated with a signed JWT-like token
minted by **hCaptcha** (sitekey `755c2e18-4bf4-4f28-b992-8c64b77c46b8`,
host `jurisprudencia.tse.jus.br`). Loading the page fires
`api.hcaptcha.com/checksiteconfig` and `api.hcaptcha.com/getcaptcha/...`
automatically (invisible/passive mode — no visible challenge widget
was shown while reconnoitering), and the resulting token is embedded
in the request body alongside the query. There is no server-side
fallback that accepts a missing/empty token.

## Why this is an exception to juscraper's "no browser automation" rule

hCaptcha (even in invisible mode) requires running hCaptcha's own JS
challenge in a real browser context to mint a valid token; it cannot
be produced by a plain `requests`/`httpx` client. juscraper's general
policy is to support only decorative captchas and text-based image
captchas (via [`txtcaptcha`](https://github.com/jtrecenti/txtcaptcha)) and
to not ship browser-automation-based scrapers.

**TSE is an explicit, user-approved exception** (2026-09-17): the hCaptcha
here runs in invisible/passive mode and passed for a plain headless
Chromium session during reconnaissance, with no visible challenge and no
paid solving service involved — a real browser just has to be present.
`TSEScraper` (`src/juscraper/courts/tse/client.py`) uses Playwright
(optional extra `tse`) purely to let the SPA build its own search request
and capture the JSON response, instead of reimplementing the frontend's
query DSL and captcha handshake by hand. The inteiro teor PDF download
(`sjur-servicos.tse.jus.br/.../download/pdf/{id}`) needs no browser at all
— it's a plain, uncaptcha'd GET, downloaded with `requests`.

## Reproduction

```python
import requests

r = requests.post(
    "https://sjur-pesquisa-api.tse.jus.br/tse/sjur-pesquisa-backend/rest/public/pesquisa",
    json={
        "refinaTermos": [],
        "refinaData": [],
        "termoPesquisa": (
            '{ "bool": { "must": [ '
            '{"query_string": {"query": "direito", "default_operator": "AND"}} '
            '], "filter": [], "must_not": [], "should": [] } }'
        ),
        "pagina": 0,
        "tamanho": 25,
        "tribunais": ["tse"],
        "captchaToken": "",
        "ordenacao": "dj_desc",
        "refinamento": [],
    },
    headers={
        "User-Agent": "Mozilla/5.0",
        "Origin": "https://jurisprudencia.tse.jus.br",
        "Referer": "https://jurisprudencia.tse.jus.br/",
        "Content-Type": "application/json",
    },
    timeout=30,
)
# Expected: request rejected/empty result — captchaToken is required
# and validated server-side (a real browser session produced a
# nonempty JWT-like token for the equivalent authenticated request).
```

## Second layer: Akamai bot-manager block on headless Chromium

Found 2026-09-18, after the hCaptcha-based implementation above was
already shipping real results. Independently of the hCaptcha, the site
also sits behind an Akamai bot manager that can flat-out block the page
navigation itself with an HTTP 403 "Access Denied" (`errors.edgesuite.net`
reference page) — the same failure mode already handled for TRF1/TRF3/TRF5
(`BotChallengeBlockedError`).

Reproduced consistently: launching Chromium with `headless=True` (the
`TSEScraper` default) got blocked on every attempt within a short window,
while `headless=False` (visible browser) succeeded repeatedly with the
same IP in the same window. This points at Akamai's bot-fingerprinting
flagging the headless Chromium signature specifically, not (only) a
request-volume/IP-based rate limit — though IP-based limiting from heavy
testing may compound it.

`TSEScraper` now detects this ("Access Denied" page title) right after
navigation and raises `BotChallengeBlockedError` immediately instead of
timing out 30s waiting for a search box that will never render. The
docstring tells users to try `headless=False` before waiting or changing
IP.

**Open question, not yet resolved:** should `TSEScraper`'s default flip to
`headless=False`? A visible/GUI-requiring browser as the *default* for a
library is unusual (breaks headless servers/CI without a virtual display
like Xvfb) and needs a deliberate call, not a silent change — tracked as a
follow-up.

## Status

- **Date checked:** 2026-09-18
- **Type:** hCaptcha, invisible mode (interactive challenge infra, passive UX)
  **+** Akamai bot-manager block on headless Chromium (see section above).
- **Backend validates token:** yes (`captchaToken` field on every search POST)
- **Decision:** supported via Playwright-driven browser automation
  (`TSEScraper`, extra `tse`) — explicit exception to juscraper's default
  "requests only, no browser automation" policy, approved by the maintainer
  requesting this scraper after being informed of the tradeoff. The Akamai
  layer is handled by raising `BotChallengeBlockedError` (same convention
  as TRF1/TRF3/TRF5); `headless=False` is the current workaround.
