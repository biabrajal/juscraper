"""Downloads raw jurisprudence data from the TSE search via a real browser session.

The TSE public search (``jurisprudencia.tse.jus.br``, app "sjur-pesquisa")
requires an hCaptcha token minted by the frontend itself on **every** search
request -- there is no way to produce that token with a plain HTTP client
(see :file:`docs/captcha/tse_captcha.md`). Instead of reimplementing the
frontend's Elasticsearch query DSL by hand, this module drives a real
(headless) browser with Playwright: it lets the SPA build and send its own
request, and captures the JSON response.

Reconnaissance findings this module depends on (2026-09-17/18):

* Deep-linking a search via URL query params only (``?expressaoLivre=...``)
  is **not reliable from a freshly-launched browser** -- it only worked
  during interactive reconnaissance because that browser session had
  already accumulated state from many prior navigations. A plain scripted
  ``page.goto()`` with query params, right after establishing a session in
  the same run, times out waiting for the search response. Confirmed with
  a minimal repro outside pytest. Driving the actual UI controls (fill +
  click) works reliably from a cold session and is what this module does.
* Consequently, the results-per-page control is the UI dropdown, which only
  offers ``1/10/25/50/100/250`` -- not the much larger values (confirmed up
  to 10000) that the backend silently accepts via direct URL/API access in
  a warmed-up session. ``tamanho_pagina`` is restricted to those options
  (see :class:`juscraper.courts.tse.schemas.InputCJSGTSE`).
* Pagination beyond page 1 must go through "Próxima página" clicks in
  sequence -- there is no verified reliable way to deep-link to page N
  directly. Downloading a non-contiguous ``paginas`` selection (e.g.
  ``[1, 5]``) still clicks through every page up to the highest one
  requested; pages outside the requested set are fetched (cost paid) but
  discarded.
* The response ``Content-Type: application/json`` header omits a charset,
  but the body bytes are actually Windows-1252 -- naive UTF-8 decoding turns
  every accented character into a single U+FFFD replacement char (e.g.
  "anotação" -> "anota��o"). Every response is decoded explicitly
  as ``cp1252`` before ``json.loads``.
* The inteiro teor PDF (``sjur-servicos.tse.jus.br/.../download/pdf/{id}``)
  is a plain, uncaptcha'd GET -- downloaded with ``requests``, not Playwright.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import math
import re
import sys
import time
from pathlib import Path

import requests
from tqdm import tqdm

from juscraper.core.exceptions import BotChallengeBlockedError

logger = logging.getLogger(__name__)

_AKAMAI_REFERENCE_RE = re.compile(r"Reference #([\w.]+)")

SEARCH_ENDPOINT_MARKER = "/rest/public/pesquisa"
# PDF servico e nacional/compartilhado -- confirmado byte-a-byte identico
# (mesmo bundle JS, mesmo host hardcoded "sjur-servicos.tse.jus.br") em
# jurisprudencia.tse.jus.br e jurisprudencia.tre-ba.jus.br. Nao muda por
# tribunal, diferente do BASE_URL (ver _base_url).
PDF_URL_TEMPLATE = "https://sjur-servicos.tse.jus.br/sjur-servicos/rest/download/pdf/{codigo_decisao}"


def _base_url(tribunal: str) -> str:
    """Monta a URL do frontend de jurisprudencia para ``tribunal``.

    A SPA e um unico bundle identico (mesmo hash de arquivo JS) servido em
    ``jurisprudencia.<tribunal>.jus.br`` para o TSE e para cada um dos 27
    TREs -- ela le o proprio hostname em runtime para montar a URL do
    backend (``https://sjur-pesquisa-api.tse.jus.br/{tribunal}/...``, com
    ``{tribunal}`` literal no bundle, substituido em runtime). Confirmado
    testando jurisprudencia.tre-ba.jus.br: mesmo hash de arquivo
    (``main.54a78eba13c2633cab1f.js``) que jurisprudencia.tse.jus.br.

    Args:
        tribunal: Sigla em minusculas do subdominio (``"tse"``, ``"tre-ba"``,
            ``"tre-sp"``, etc.).
    """
    return f"https://jurisprudencia.{tribunal}.jus.br/#/jurisprudencia/pesquisa"

# Unicas opcoes expostas pelo dropdown "N por página" da UI (refs docstring).
PAGE_SIZE_OPTIONS: tuple[int, ...] = (1, 10, 25, 50, 100, 250)

# Tamanho de pagina que a SPA ja usa por default logo apos o clique em
# "Pesquisar" -- selecionar essa MESMA opcao no dropdown nao dispara uma
# nova busca (Angular Material nao reemite a selecao quando o valor nao
# muda), entao _set_page_size trava esperando uma resposta que nunca vem.
DEFAULT_UI_PAGE_SIZE = 25

_PAGE_SIZE_LABEL_RE = re.compile(r"^\d+ por página$")

# Intervalo minimo entre cliques -- reduz a chance de o hCaptcha escalar de
# invisivel para um desafio visivel sob uso repetido em rajada.
SLEEP_BETWEEN_PAGES = 0.5

_RESPONSE_TIMEOUT_MS = 30_000

# O Akamai do TSE bloqueia (HTTP 403 "Access Denied") o Chromium headless
# pelo user agent, que contem "HeadlessChrome". Testado em 2026-10-20 no
# macOS: so ``channel="chromium"`` continua bloqueado; com este UA de
# Chrome comum o headless passa, no TSE e nos TREs.
_HEADLESS_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)


def _import_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - exercised only without the extra installed
        raise ImportError(
            "TSEScraper requer o pacote 'playwright' (extra opcional 'tse'). "
            "Instale com: pip install -e '.[tse]' && playwright install chromium"
        ) from exc
    return sync_playwright


class TSEUnexpectedResponseError(RuntimeError):
    """Raised when the TSE search backend replies with an unexpected response shape.

    Observed intermittently (refs bug report 2026-09-18): instead of the
    usual ``{mensagem, content, totalRegistros, aggs}`` envelope, the search
    endpoint occasionally answers with an empty JSON array (``[]``) for an
    otherwise valid search term. Not reproducible on demand and not tied to
    any specific term tested so far -- looks like a transient anti-bot/rate
    -limit signal from the hCaptcha layer rather than a query-parsing issue.
    Raised instead of letting the caller hit a bare ``AttributeError`` deep
    inside this module.
    """


def _decode_response_json(raw: bytes) -> dict:
    """Decode a TSE API response body, working around the missing charset (see module docstring)."""
    try:
        return json.loads(raw.decode("cp1252"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return json.loads(raw.decode("utf-8"))


def _eh_resposta_de_busca(resp) -> bool:
    """Predicate for ``page.expect_response``: only the search results envelope.

    Other XHRs share ``SEARCH_ENDPOINT_MARKER`` in their URL -- e.g. the list
    of procedural classes (``[{siglaClasse, descricaoClasse}, ...]``) that the
    form loads. Matching on the URL alone sometimes captured that list instead
    of the search results, raising ``TSEUnexpectedResponseError``. Requiring a
    JSON object (the results envelope is a dict, the class list is a list)
    skips those responses.
    """
    if SEARCH_ENDPOINT_MARKER not in resp.url:
        return False
    try:
        return isinstance(_decode_response_json(resp.body()), dict)
    except Exception:
        return False


def _decode_page_response(raw: bytes, *, contexto: str) -> dict:
    """Decodes a search-page response body and validates its shape.

    Args:
        raw: Raw response body bytes.
        contexto: Human-readable description of the action that produced
            this response, used in the error message (e.g. ``"buscar
            'eleição'"``, ``"ir para a próxima página"``).

    Raises:
        TSEUnexpectedResponseError: When the decoded body isn't the usual
            ``{mensagem, content, totalRegistros, aggs}`` dict (see class
            docstring for when this has been observed).
    """
    resultado = _decode_response_json(raw)
    if not isinstance(resultado, dict) or "content" not in resultado:
        raise TSEUnexpectedResponseError(
            f"Resposta inesperada do backend do TSE ao {contexto}: esperava um objeto "
            f"com a chave 'content', recebeu {type(resultado).__name__} "
            f"({resultado!r:.200}). Pode ser um sinal transitorio de rate limit/anti-bot "
            "do hCaptcha -- tentar de novo (a busca inteira, nao so a pagina) costuma resolver."
        )
    return resultado


def _check_not_blocked(page, tribunal: str) -> None:
    """Raises ``BotChallengeBlockedError`` if Akamai returned an "Access Denied" page.

    Observed 2026-09-18: ``headless=True`` (the default) gets this block
    consistently while ``headless=False`` does not, for the same IP within
    the same minute -- looks like Akamai's bot-fingerprinting flagging
    headless Chromium specifically, not (only) a request-volume/IP block.
    Checked right after navigation so this fails fast with a clear message
    instead of a confusing 30s timeout waiting for a search box that will
    never render.
    """
    if page.title() != "Access Denied":
        return
    match = _AKAMAI_REFERENCE_RE.search(page.content())
    raise BotChallengeBlockedError(tribunal.upper(), page.url, match.group(1) if match else None)


def _establish_session(
    page,
    pesquisa: str,
    *,
    tribunal: str = "tse",
    data_julgamento_inicio: str | None = None,
    data_julgamento_fim: str | None = None,
    data_publicacao_inicio: str | None = None,
    data_publicacao_fim: str | None = None,
) -> dict:
    """Drives the search UI once for ``pesquisa`` (+ optional date filter).

    Returns the raw JSON of the (default-size) first page. Plain searches
    use the simple search box; a date filter requires opening the
    "Avançada" (advanced) dialog, which exposes a single "Data inicial"/
    "Data final" pair plus a "Julgamento"/"Publicação" type selector
    (default "Julgamento"). Combining both date types (the dialog's own
    "+" button) is not implemented -- callers reject that combination
    upstream (see :meth:`juscraper.courts.tse.client.TSEScraper.cjsg_download`).

    Raises:
        juscraper.core.exceptions.BotChallengeBlockedError: When Akamai
            blocks the navigation itself (see :func:`_check_not_blocked`).
    """
    page.goto(_base_url(tribunal))
    _check_not_blocked(page, tribunal)
    tem_filtro_data = bool(
        data_julgamento_inicio or data_julgamento_fim or data_publicacao_inicio or data_publicacao_fim
    )
    if not tem_filtro_data:
        with page.expect_response(
            _eh_resposta_de_busca, timeout=_RESPONSE_TIMEOUT_MS
        ) as resp_info:
            page.get_by_role("textbox", name="Pesquisa livre").fill(pesquisa)
            page.get_by_role("button", name="Pesquisar", exact=True).first.click()
        return _decode_page_response(resp_info.value.body(), contexto=f"buscar {pesquisa!r}")

    page.get_by_role("button", name="Avançada").click()
    # A pagina ja tem, fora do modal, um painel "Refinar resultado da
    # pesquisa" oculto no DOM (mesmo sem nenhuma busca ainda feita) com
    # campos/labels de mesmo texto ("Data inicial", "Julgamento", ...).
    # Escopar tudo ao ``dialog`` evita ambiguidade (`strict mode violation`)
    # contra esses elementos escondidos.
    dialog = page.get_by_role("dialog")
    dialog.get_by_role("textbox", name="Pesquisar em todos os campos").fill(pesquisa)
    dialog.get_by_role("textbox", name="Data inicial").fill(
        data_julgamento_inicio or data_publicacao_inicio or ""
    )
    dialog.get_by_role("textbox", name="Data final").fill(
        data_julgamento_fim or data_publicacao_fim or ""
    )
    if data_publicacao_inicio or data_publicacao_fim:
        dialog.get_by_text("Julgamento", exact=True).click()
        page.get_by_role("option", name="Publicação").click()
    with page.expect_response(
        _eh_resposta_de_busca, timeout=_RESPONSE_TIMEOUT_MS
    ) as resp_info:
        dialog.get_by_role("button", name="Pesquisar", exact=True).click()
    return _decode_page_response(resp_info.value.body(), contexto=f"buscar {pesquisa!r} com filtro de data")


def _set_page_size(page, tamanho_pagina: int) -> dict:
    """Changes the results-per-page dropdown to ``tamanho_pagina``. Returns the resulting page-1 JSON."""
    label = f"{tamanho_pagina} por página"
    with page.expect_response(
        _eh_resposta_de_busca, timeout=_RESPONSE_TIMEOUT_MS
    ) as resp_info:
        page.get_by_text(_PAGE_SIZE_LABEL_RE).first.click()
        page.get_by_role("option", name=label).click()
    return _decode_page_response(resp_info.value.body(), contexto=f"mudar o tamanho de pagina para {tamanho_pagina}")


def _next_page(page) -> dict:
    """Clicks "Próxima página". Returns the raw JSON of the resulting page."""
    with page.expect_response(
        _eh_resposta_de_busca, timeout=_RESPONSE_TIMEOUT_MS
    ) as resp_info:
        page.get_by_role("button", name="Próxima página").first.click()
    return _decode_page_response(resp_info.value.body(), contexto="ir para a proxima pagina")


def cjsg_download_manager(
    *,
    pesquisa: str,
    paginas,
    tamanho_pagina: int,
    baixar_pdf: bool,
    download_path: str,
    tribunal: str = "tse",
    data_julgamento_inicio: str | None = None,
    data_julgamento_fim: str | None = None,
    data_publicacao_inicio: str | None = None,
    data_publicacao_fim: str | None = None,
    headless: bool = True,
    verbose: int = 0,
) -> list[dict]:
    """Downloads raw jurisprudence search results from the TSE (multiple pages).

    Args:
        pesquisa: Free-text search term.
        tribunal: Subdominio da SPA a navegar -- ``"tse"`` (default) ou
            ``"tre-xx"`` (ex.: ``"tre-ba"``, ``"tre-sp"``). Mesmo bundle
            identico servido em ``jurisprudencia.<tribunal>.jus.br`` para
            todos os 27 TREs (ver :func:`_base_url`).
        paginas: Pages to download (1-based, list/range), or ``None`` for all.
            Pagination is sequential (see module docstring): fetching page N
            always clicks through pages before it too.
        tamanho_pagina: Results per page. Must be one of
            :data:`PAGE_SIZE_OPTIONS` (validated upstream by the pydantic
            schema).
        baixar_pdf: When ``True``, also downloads each decision's inteiro
            teor PDF into ``download_path``.
        download_path: Directory used for PDF downloads (only when
            ``baixar_pdf=True``).
        data_julgamento_inicio, data_julgamento_fim: Judgment-date filter
            (``DD/MM/AAAA``), mutually exclusive with the publication-date
            filter (validated upstream by the caller).
        data_publicacao_inicio, data_publicacao_fim: Publication-date filter
            (``DD/MM/AAAA``).
        headless: Whether to run the browser headless.
        verbose: When falsy, suppresses the progress bar.

    Returns:
        list[dict]: One raw JSON response (``{mensagem, content,
        totalRegistros, aggs}``) per requested page, in ``paginas`` order.
    """
    # Roda o Playwright numa thread dedicada, nao na thread chamadora: a
    # sync API do Playwright precisa criar um subprocesso (o navegador), e
    # isso falha com ``NotImplementedError`` no Windows quando a thread
    # atual ja tem um event loop asyncio incompativel (ex.: dentro do
    # pytest, cujo pytest-asyncio configura um ``SelectorEventLoop`` na
    # thread principal). Uma ``ThreadPoolExecutor`` de 1 worker garante uma
    # thread nova, sem event loop preexistente. Confirmado via reproducao
    # minima: o mesmo codigo funciona direto (``python -c ...``) mas falha
    # sob ``pytest`` sem esse isolamento.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        resultados = executor.submit(
            _run_browser_session,
            pesquisa=pesquisa,
            paginas=paginas,
            tamanho_pagina=tamanho_pagina,
            tribunal=tribunal,
            data_julgamento_inicio=data_julgamento_inicio,
            data_julgamento_fim=data_julgamento_fim,
            data_publicacao_inicio=data_publicacao_inicio,
            data_publicacao_fim=data_publicacao_fim,
            headless=headless,
            verbose=verbose,
        ).result()

    if baixar_pdf:
        _baixar_pdfs(resultados, download_path)

    return resultados


def _run_browser_session(
    *,
    pesquisa: str,
    paginas,
    tamanho_pagina: int,
    data_julgamento_inicio: str | None,
    data_julgamento_fim: str | None,
    data_publicacao_inicio: str | None,
    data_publicacao_fim: str | None,
    headless: bool,
    verbose: int,
    tribunal: str = "tse",
) -> list[dict]:
    """Runs the Playwright-driven search + pagination. See :func:`cjsg_download_manager`."""
    if sys.platform == "win32":
        # pytest-asyncio (dependencia de dev) deixa uma SelectorEventLoopPolicy
        # configurada globalmente, que nao suporta ``asyncio.create_subprocess_exec``
        # no Windows (o Chromium e lancado como subprocesso pelo Playwright).
        # ``set_event_loop_policy`` afeta so a criacao de loop nesta thread nova,
        # entao isso nao interfere com o resto do processo/pytest.
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    sync_playwright = _import_playwright()

    paginas_por_numero: dict[int, dict] = {}
    with sync_playwright() as p:
        if headless:
            # Ver _HEADLESS_USER_AGENT: sem trocar o UA o Akamai bloqueia.
            browser = p.chromium.launch(
                headless=True,
                channel="chromium",
                args=["--disable-blink-features=AutomationControlled"],
            )
            page = browser.new_page(user_agent=_HEADLESS_USER_AGENT)
        else:
            browser = p.chromium.launch(headless=False)
            page = browser.new_page()
        try:
            resultado_inicial = _establish_session(
                page,
                pesquisa,
                tribunal=tribunal,
                data_julgamento_inicio=data_julgamento_inicio,
                data_julgamento_fim=data_julgamento_fim,
                data_publicacao_inicio=data_publicacao_inicio,
                data_publicacao_fim=data_publicacao_fim,
            )
            if tamanho_pagina == DEFAULT_UI_PAGE_SIZE:
                primeira_pagina = resultado_inicial
            else:
                primeira_pagina = _set_page_size(page, tamanho_pagina)
            paginas_por_numero[1] = primeira_pagina

            total = primeira_pagina.get("totalRegistros", 0) or 0
            n_pags = math.ceil(total / tamanho_pagina) if total else 1

            paginas_desejadas = list(range(1, n_pags + 1)) if paginas is None else sorted(set(paginas))
            maior_pagina = max(paginas_desejadas) if paginas_desejadas else 1

            for pagina_1based in tqdm(
                range(2, maior_pagina + 1), desc="Baixando páginas TSE", disable=not verbose
            ):
                time.sleep(SLEEP_BETWEEN_PAGES)
                paginas_por_numero[pagina_1based] = _next_page(page)
        finally:
            browser.close()

    return [paginas_por_numero[p] for p in paginas_desejadas if p in paginas_por_numero]


# Retry/backoff para o endpoint de PDF: sob muitas requisicoes seguidas ele
# responde HTTP 200 com um corpo de texto ("Erro ao tentar recuperar PDF:
# Excesso de requisicoes! ...") em vez do PDF -- nao ha status code de erro
# para detectar isso, so o conteudo em si (ver ``_extract_pdf_bytes``).
PDF_MAX_RETRIES = 3
PDF_RETRY_BACKOFF = 3.0
PDF_SLEEP_BETWEEN = 0.3


def _extract_pdf_bytes(raw: bytes) -> bytes:
    """Extracts the actual PDF payload from the endpoint's response body.

    The endpoint declares ``Content-Type: application/pdf`` but the body is
    actually wrapped in a ``multipart/form-data``-shaped envelope
    (``--{boundary}\\r\\nContent-Disposition: ...\\r\\nContent-Type:
    application/octet-stream\\r\\n\\r\\n<pdf bytes>\\r\\n--{boundary}--``).
    Passthrough when the body already starts with the PDF magic bytes
    (defensive, in case the server ever fixes this) or doesn't look like a
    multipart envelope at all (e.g. the plain-text rate-limit error).
    """
    if raw.startswith(b"%PDF") or not raw.startswith(b"--"):
        return raw
    first_line_end = raw.find(b"\r\n")
    if first_line_end == -1:
        return raw
    boundary = raw[2:first_line_end]
    header_end = raw.find(b"\r\n\r\n", first_line_end)
    if header_end == -1:
        return raw
    body_start = header_end + 4
    body_end = raw.find(b"\r\n--" + boundary, body_start)
    return raw[body_start:] if body_end == -1 else raw[body_start:body_end]


def _fetch_pdf_bytes(session: requests.Session, codigo_decisao: int) -> bytes | None:
    """Downloads and extracts one PDF, retrying on the endpoint's rate-limit response."""
    url = PDF_URL_TEMPLATE.format(codigo_decisao=codigo_decisao)
    for attempt in range(1, PDF_MAX_RETRIES + 1):
        resp = session.get(url, timeout=60)
        resp.raise_for_status()
        pdf_bytes = _extract_pdf_bytes(resp.content)
        if pdf_bytes.startswith(b"%PDF"):
            return pdf_bytes
        if attempt < PDF_MAX_RETRIES:
            time.sleep(PDF_RETRY_BACKOFF * attempt)
    return None


def _baixar_pdfs(resultados: list[dict], download_path: str) -> None:
    """Downloads the inteiro teor PDF of every decision that has one, mutating ``resultados`` in place."""
    pasta = Path(download_path)
    pasta.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    for pagina in resultados:
        for item in pagina.get("content", []):
            if item.get("temInteiroTeorPDF") != "true":
                continue
            codigo = item["codigoDecisao"]
            destino = pasta / f"{codigo}.pdf"
            if destino.exists():
                item["caminho_pdf"] = str(destino)
                continue
            pdf_bytes = _fetch_pdf_bytes(session, codigo)
            if pdf_bytes is None:
                logger.warning(
                    "Nao foi possivel baixar o inteiro teor da decisao %s (rate limit persistente).",
                    codigo,
                )
                continue
            destino.write_bytes(pdf_bytes)
            item["caminho_pdf"] = str(destino)
            time.sleep(PDF_SLEEP_BETWEEN)
