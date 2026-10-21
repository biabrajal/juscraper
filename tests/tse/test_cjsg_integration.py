"""Integration tests for TSE cjsg — hit the live jurisprudencia.tse.jus.br via Playwright.

Requires the optional ``tse`` extra (``pip install -e ".[tse]"``) and a
Chromium binary (``playwright install chromium``). Skipped automatically
when ``playwright`` is not installed.
"""
from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip("playwright")

from juscraper.courts.tse.client import TSEScraper  # noqa: E402


@pytest.mark.integration
def test_cjsg_basic_dataframe():
    scraper = TSEScraper()
    df = scraper.cjsg("direito de resposta", paginas=1, tamanho_pagina=25)

    assert isinstance(df, pd.DataFrame)
    assert not df.empty
    assert {"processo", "ementa", "relator", "data_julgamento"}.issubset(set(df.columns))


@pytest.mark.integration
def test_cjsg_paginas_int_equivale_a_range():
    scraper = TSEScraper()
    df_int = scraper.cjsg("eleição", paginas=2, tamanho_pagina=10)
    df_range = scraper.cjsg("eleição", paginas=range(1, 3), tamanho_pagina=10)

    assert len(df_int) == len(df_range)


@pytest.mark.integration
def test_cjsg_download_e_parse(tmp_path):
    scraper = TSEScraper(download_path=str(tmp_path))
    brutos = scraper.cjsg_download("direito de resposta", paginas=1, tamanho_pagina=10)
    df = scraper.cjsg_parse(brutos)

    assert isinstance(brutos, list)
    assert isinstance(df, pd.DataFrame)
    assert not df.empty


@pytest.mark.integration
def test_cjsg_baixar_pdf(tmp_path):
    scraper = TSEScraper(download_path=str(tmp_path))
    df = scraper.cjsg("direito de resposta", paginas=1, tamanho_pagina=10, baixar_pdf=True)

    com_pdf = df[df["url_inteiro_teor"].notnull()]
    assert not com_pdf.empty
    assert com_pdf["caminho_pdf"].notnull().all()
    for caminho in com_pdf["caminho_pdf"]:
        assert Path(caminho).exists()


@pytest.mark.integration
def test_cjsg_filtro_data_julgamento():
    scraper = TSEScraper()
    df = scraper.cjsg(
        "eleição",
        paginas=1,
        tamanho_pagina=25,
        data_julgamento_inicio="01/01/2022",
        data_julgamento_fim="31/12/2022",
    )

    assert not df.empty
    datas = pd.to_datetime(df["data_julgamento"])
    assert datas.min() >= pd.Timestamp("2022-01-01")
    assert datas.max() <= pd.Timestamp("2022-12-31")


@pytest.mark.integration
def test_cjsg_filtro_data_publicacao():
    scraper = TSEScraper()
    df = scraper.cjsg(
        "eleição",
        paginas=1,
        tamanho_pagina=25,
        data_publicacao_inicio="01/01/2022",
        data_publicacao_fim="31/12/2022",
    )

    assert not df.empty
