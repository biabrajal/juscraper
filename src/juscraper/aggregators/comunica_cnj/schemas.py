"""Pydantic schemas for the ComunicaCNJ aggregator.

A API publica em https://comunicaapi.pje.jus.br/api/v1/comunicacao aceita
filtros opcionais por intervalo de data de disponibilizacao em formato ISO
(``YYYY-MM-DD``). Datas em formato brasileiro (``DD/MM/YYYY``) sao convertidas
no client antes de instanciar o schema.
"""
from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from ...schemas import PaginasMixin


class InputListarComunicacoesComunicaCNJ(PaginasMixin):
    """Filtros aceitos por :meth:`ComunicaCNJScraper.listar_comunicacoes`.

    Campos:
        pesquisa: Termo livre buscado no texto da comunicacao (parametro
            ``texto`` da API). Opcional — ver exigencia de pelo menos um
            filtro abaixo.
        numero_processo: CNJ (formatado ou so digitos) para localizar as
            comunicacoes de um processo especifico (parametro
            ``numeroProcesso`` da API). Opcional.
        sigla_tribunal: Sigla do tribunal de origem (ex.: ``"TSE"``,
            ``"TRE-SP"``) (parametro ``siglaTribunal`` da API). Opcional.
        data_disponibilizacao_inicio: Limite inferior de
            ``dataDisponibilizacao``, ISO ``YYYY-MM-DD``. Opcional.
        data_disponibilizacao_fim: Limite superior de
            ``dataDisponibilizacao``, ISO ``YYYY-MM-DD``. Opcional.
        itens_por_pagina: Quantos resultados por requisicao (1-100). Default
            100 para minimizar round-trips.

    Pelo menos um entre ``pesquisa``, ``numero_processo`` e
    ``sigla_tribunal`` e obrigatorio — sem nenhum filtro, a API devolve o
    feed nacional inteiro (todos os tribunais, sem corte), o que nao e um
    uso responsavel de uma API publica gratuita. Essa exigencia e checada
    em :meth:`ComunicaCNJScraper.listar_comunicacoes` (``ValueError``), nao
    aqui no schema — mesmo padrao de ``tribunal``/``numero_processo`` em
    :class:`~juscraper.aggregators.datajud.schemas.InputListarProcessosDataJud`,
    que mantem o Input sempre instanciavel com payload minimo vazio
    (``tests/schemas/test_schema_contract.py``).

    Os nomes ``data_inicio``/``data_fim`` ficaram **fora** do canonico de
    proposito: no resto do juscraper, esses dois sao aliases deprecados que
    ``normalize_datas`` mapeia para ``data_julgamento_inicio``/
    ``data_julgamento_fim``. O ComunicaCNJ filtra por data de
    disponibilizacao (semantica diferente), entao usar nome explicito evita
    colisao com o pipeline canonico (``DEPRECATED_ALIASES``/``DATE_ALIASES``
    em ``utils/params.py``).
    """

    BACKEND_DATE_FORMAT: ClassVar[str] = "%Y-%m-%d"

    pesquisa: str | None = None
    numero_processo: str | None = None
    sigla_tribunal: str | None = None
    data_disponibilizacao_inicio: str | None = None
    data_disponibilizacao_fim: str | None = None
    itens_por_pagina: int = Field(default=100, ge=1, le=100)

    model_config = ConfigDict(
        extra="forbid",
        arbitrary_types_allowed=True,
    )


class OutputListarComunicacoesComunicaCNJ(BaseModel):
    """Colunas observaveis em uma linha do DataFrame de
    :meth:`ComunicaCNJScraper.listar_comunicacoes`.

    A API devolve cada item com ``numero_processo`` (CNJ formatado) +
    diversos campos camelCase relativos ao tribunal de origem (``siglaTribunal``,
    ``nomeOrgao``, ``texto``, ``link``, ``dataDisponibilizacao``, etc.).
    A coluna minima garantida e ``numero_processo``; ``extra='allow'``
    propaga o restante do passthrough sem precisar enumerar campo a campo.
    """

    numero_processo: str

    model_config = ConfigDict(extra="allow")
