"""Offline granular tests for TSE cjsg parsing (no network/browser involved)."""
import json

import pandas as pd
import pytest

from juscraper.core.exceptions import BotChallengeBlockedError
from juscraper.courts.tse.client import TSEScraper
from juscraper.courts.tse.download import TSEUnexpectedResponseError, _check_not_blocked, _decode_page_response
from juscraper.courts.tse.parse import cjsg_parse_manager
from tests._helpers import load_sample

CJSG_MIN_COLUMNS = {
    "processo", "classe", "assunto", "relator", "ementa", "decisao",
    "data_julgamento", "tipo_decisao", "codigo_decisao", "url_inteiro_teor",
}


def _load_page():
    return json.loads(load_sample("tse", "cjsg/sample_page.json"))


def test_cjsg_parse_returns_dataframe_with_expected_schema():
    df = cjsg_parse_manager([_load_page()])

    assert isinstance(df, pd.DataFrame)
    assert CJSG_MIN_COLUMNS.issubset(set(df.columns))
    assert len(df) == 2


def test_cjsg_parse_strips_html_from_ementa_and_decisao():
    df = cjsg_parse_manager([_load_page()])

    primeira = df.iloc[0]
    assert "<p>" not in primeira["ementa"]
    assert "SÚMULA-TSE No 55" in primeira["ementa"]
    assert "<div>" not in primeira["decisao"]


def test_cjsg_parse_handles_missing_ementa():
    df = cjsg_parse_manager([_load_page()])

    segunda = df.iloc[1]
    assert segunda["ementa"] is None


def test_cjsg_parse_joins_multiple_assuntos():
    df = cjsg_parse_manager([_load_page()])

    assert df.iloc[0]["assunto"] == "AIJE; Matéria processual"


def test_cjsg_parse_maps_processo_to_numero_unico():
    df = cjsg_parse_manager([_load_page()])

    assert df.iloc[0]["processo"] == "06005735120246050114"


def test_cjsg_parse_url_inteiro_teor_only_when_available():
    df = cjsg_parse_manager([_load_page()])

    assert df.iloc[0]["url_inteiro_teor"].endswith("/3577492")
    assert df.iloc[1]["url_inteiro_teor"] is None


def test_cjsg_parse_converts_data_julgamento_to_date():
    df = cjsg_parse_manager([_load_page()])

    assert str(df.iloc[0]["data_julgamento"]) == "2026-09-11"


def test_cjsg_parse_concatenates_multiple_pages():
    pagina = _load_page()
    df = cjsg_parse_manager([pagina, pagina])

    assert len(df) == 4


def test_cjsg_parse_empty_input_returns_empty_dataframe():
    df = cjsg_parse_manager([])

    assert isinstance(df, pd.DataFrame)
    assert df.empty


def test_cjsg_download_rejects_combined_date_filters():
    """Combinar data_julgamento_* e data_publicacao_* falha rapido, sem precisar do Playwright."""
    scraper = TSEScraper()

    with pytest.raises(ValueError, match="data_julgamento_\\* e data_publicacao_\\*"):
        scraper.cjsg_download(
            "eleição",
            data_julgamento_inicio="01/01/2024",
            data_publicacao_inicio="01/01/2024",
        )


def test_decode_page_response_accepts_valid_envelope():
    raw = json.dumps({"mensagem": None, "content": [], "totalRegistros": 0, "aggs": {}}).encode("utf-8")

    resultado = _decode_page_response(raw, contexto="buscar 'x'")

    assert resultado["totalRegistros"] == 0


def test_decode_page_response_rejects_unexpected_shape():
    """Refs bug report 2026-09-18: o backend por vezes devolve `[]` em vez do envelope usual."""
    raw = json.dumps([]).encode("utf-8")

    with pytest.raises(TSEUnexpectedResponseError, match="buscar 'Meta AI'"):
        _decode_page_response(raw, contexto="buscar 'Meta AI'")
