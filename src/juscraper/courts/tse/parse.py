"""Parses raw jurisprudence data downloaded from the TSE search."""
from __future__ import annotations

import pandas as pd
from bs4 import BeautifulSoup

from .download import PDF_URL_TEMPLATE


def _strip_html(value: str | None) -> str | None:
    if not value:
        return value
    return BeautifulSoup(value, "html.parser").get_text(separator=" ", strip=True)


def _join_names(items: list[dict] | None, key: str) -> str | None:
    if not items:
        return None
    nomes = [item.get(key) for item in items if item.get(key)]
    return "; ".join(nomes) if nomes else None


def _parse_item(item: dict) -> dict:
    tem_pdf = item.get("temInteiroTeorPDF") == "true"
    codigo_decisao = item.get("codigoDecisao")
    return {
        "processo": item.get("numeroUnico"),
        "classe": item.get("descricaoClasse"),
        "assunto": _join_names(item.get("assuntos"), "nomeAssunto"),
        "relator": _join_names(item.get("relatores"), "nome"),
        "ementa": _strip_html(item.get("textoEmenta")),
        "decisao": _strip_html(item.get("textoDecisao")),
        "data_julgamento": item.get("dataDecisao"),
        "tipo_decisao": item.get("descricaoTipoDecisao"),
        "sigla_classe": item.get("siglaClasse"),
        "sigla_uf": item.get("siglaUF"),
        "municipio": item.get("nomeMunicipio"),
        "codigo_decisao": codigo_decisao,
        "url_inteiro_teor": (
            PDF_URL_TEMPLATE.format(codigo_decisao=codigo_decisao) if tem_pdf else None
        ),
        "caminho_pdf": item.get("caminho_pdf"),
    }


def cjsg_parse_manager(resultados_brutos: list[dict]) -> pd.DataFrame:
    """Extracts relevant data from the raw TSE search responses into a DataFrame.

    Args:
        resultados_brutos: List of raw JSON responses as returned by
            :func:`juscraper.courts.tse.download.cjsg_download_manager`.

    Returns:
        pd.DataFrame: One row per decision.
    """
    linhas = [
        _parse_item(item)
        for pagina in resultados_brutos
        for item in pagina.get("content", [])
    ]
    df = pd.DataFrame(linhas)
    if not df.empty:
        df["data_julgamento"] = pd.to_datetime(
            df["data_julgamento"], format="%d/%m/%Y", errors="coerce"
        ).dt.date
    return df
