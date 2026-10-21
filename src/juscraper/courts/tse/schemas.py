"""Pydantic schemas for TSE scraper endpoints.

Wired em :mod:`juscraper.courts.tse.client`.
"""
from __future__ import annotations

from typing import ClassVar, Literal

from ...schemas import DataJulgamentoMixin, DataPublicacaoMixin, OutputCJSGBase, SearchBase


class InputCJSGTSE(SearchBase, DataJulgamentoMixin, DataPublicacaoMixin):
    """Accepted input for TSE ``cjsg`` / ``cjsg_download``.

    ``pesquisa`` e enviado como "Pesquisa livre" (aceita a sintaxe tipo
    Lucene do proprio TSE: ``E``, ``OU``, ``NAO``, ``*``, ``?``, aspas,
    ``~``, ``:``, ``^``, ``+``, ``-``, parenteses/colchetes/chaves).

    O backend so aceita buscar via um navegador real: toda chamada de
    busca exige um token hCaptcha mintado pelo proprio frontend
    (refs docs/captcha/tse_captcha.md). ``tamanho_pagina``/``baixar_pdf``
    sao especificos do TSE.

    ``tamanho_pagina`` fica restrito as opcoes do dropdown "N por pagina"
    da propria UI -- diferente dos demais tribunais, nao existe forma
    confiavel de pedir um tamanho arbitrario a partir de uma sessao de
    navegador nova (a URL aceita valores bem maiores, mas isso so
    funciona em sessoes ja "aquecidas" por navegacoes anteriores; nao e
    reproduzivel de forma confiavel em um scraper que abre um navegador
    do zero a cada chamada). Default ``250`` (o maior valor disponivel):
    como cada pagina custa uma rodada completa de clique + handshake de
    hCaptcha (nao o tamanho da resposta), maximizar o tamanho da pagina
    minimiza o numero de rodadas para "baixar tudo".

    ``data_julgamento_inicio/fim`` (campo ``dataDecisao`` no backend) e
    ``data_publicacao_inicio/fim`` (campo ``publicacoes.dataPublicacao``)
    sao mutuamente exclusivos nesta versao: a UI permite combinar os dois
    tipos de data via um botao "+" na busca avancada, mas esse fluxo ainda
    nao foi reverso-projetado/testado. Passar os dois pares ao mesmo tempo
    levanta ``ValueError`` em vez de aplicar so um deles silenciosamente.
    """

    BACKEND_DATE_FORMAT: ClassVar[str] = "%d/%m/%Y"

    tamanho_pagina: Literal[1, 10, 25, 50, 100, 250] = 250
    baixar_pdf: bool = False


class OutputCJSGTSE(OutputCJSGBase):
    """Colunas observaveis em uma linha do DataFrame de :meth:`TSEScraper.cjsg`.

    Reflete ``tse.parse.cjsg_parse_manager``. Nao herda ``OutputRelatoriaMixin``
    porque o parser nao extrai ``orgao_julgador`` (o TSE julga em turma unica).
    """

    classe: str | None = None
    assunto: str | None = None
    relator: str | None = None
    decisao: str | None = None
    tipo_decisao: str | None = None
    sigla_classe: str | None = None
    sigla_uf: str | None = None
    municipio: str | None = None
    codigo_decisao: int | None = None
    url_inteiro_teor: str | None = None
    caminho_pdf: str | None = None
