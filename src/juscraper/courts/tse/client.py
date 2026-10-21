"""Scraper para o Tribunal Superior Eleitoral (TSE)."""
from __future__ import annotations

from typing import Literal

import pandas as pd

from juscraper.core.base import BaseScraper
from juscraper.utils.params import apply_input_pipeline_search

from .download import cjsg_download_manager
from .parse import cjsg_parse_manager
from .schemas import InputCJSGTSE


class TSEScraper(BaseScraper):
    """Scraper para o Tribunal Superior Eleitoral.

    Diferente dos demais scrapers do juscraper, este usa um navegador real
    (Playwright) como transporte em vez de ``requests``: a busca de
    jurisprudência do TSE exige um token hCaptcha mintado pelo próprio
    frontend em toda chamada, o que não pode ser reproduzido com um cliente
    HTTP puro (ver ``docs/captcha/tse_captcha.md``). Requer o extra opcional
    ``tse``: ``pip install -e ".[tse]"`` seguido de
    ``playwright install chromium``.

    ``headless=True`` (default) pode esbarrar num bloqueio Akamai ("Access
    Denied") -- observado em 2026-09-18, o bot manager parece sinalizar
    especificamente a fingerprint do Chromium headless (``headless=False``
    passou de forma consistente com o mesmo IP, na mesma janela de tempo).
    Se `cjsg`/`cjsg_download` levantar ``BotChallengeBlockedError``,
    experimente ``TSEScraper(headless=False)`` antes de esperar ou trocar
    de IP.
    """

    INPUT_CJSG = InputCJSGTSE

    def __init__(
        self,
        verbose: int = 0,
        download_path: str | None = None,
        headless: bool = True,
        tribunal: str = "tse",
    ):
        super().__init__("TSE")
        self.set_verbose(verbose)
        self.set_download_path(download_path)
        self.headless = headless
        # A SPA de jurisprudencia e um bundle identico servido tambem em
        # jurisprudencia.<tribunal>.jus.br para os 27 TREs (mesmo hash de
        # arquivo JS, runtime le o hostname pra montar a URL do backend).
        # Ver docstring de download._base_url para os detalhes da
        # reconhecimento. Ex.: tribunal="tre-ba", "tre-sp".
        self.tribunal = tribunal.lower()

    def cjsg_download(
        self,
        pesquisa: str | None = None,
        paginas: int | list | range | None = None,
        tamanho_pagina: Literal[1, 10, 25, 50, 100, 250] = 250,
        baixar_pdf: bool = False,
        data_julgamento_inicio: str | None = None,
        data_julgamento_fim: str | None = None,
        data_publicacao_inicio: str | None = None,
        data_publicacao_fim: str | None = None,
        **kwargs,
    ) -> list[dict]:
        """Baixa os resultados brutos (JSON) da pesquisa de jurisprudência do TSE.

        Usa um navegador real (Playwright) para carregar a SPA de pesquisa e
        capturar a resposta JSON da busca — o backend exige um token
        hCaptcha, mintado pelo próprio frontend, em toda chamada. A
        paginação é sequencial: buscar a página N sempre clica primeiro em
        "Próxima página" nas páginas anteriores (não há como pular direto
        para N a partir de uma sessão de navegador nova).

        Args:
            pesquisa (str): Termo de busca livre. Aceita a sintaxe tipo
                Lucene do próprio TSE (``E``, ``OU``, ``NÃO``, ``*``, ``?``,
                aspas, ``~``, ``:``, ``^``, ``+``, ``-``, parênteses,
                colchetes, chaves).
            paginas (int | list | range | None): Páginas 1-based; ``None``
                baixa todas. Cada página cobre até ``tamanho_pagina``
                decisões — como cada página custa um clique + handshake de
                hCaptcha (~1-2s), prefira ``tamanho_pagina`` grande a muitas
                páginas pequenas. Default ``None``.
            tamanho_pagina (Literal[1, 10, 25, 50, 100, 250]): Decisões por
                página. Restrito às opções do dropdown "N por página" da
                própria UI — diferente dos demais tribunais, não há forma
                confiável de pedir um tamanho arbitrário a partir de uma
                sessão de navegador nova. Default ``250`` (o maior
                disponível, minimizando o número de páginas para "baixar
                tudo").
            baixar_pdf (bool): Se ``True``, baixa o inteiro teor (PDF) de
                cada decisão com ``temInteiroTeorPDF`` — requisição HTTP
                simples, sem captcha — para ``self.download_path``. Default
                ``False``.
            data_julgamento_inicio (str): Filtra pela data de julgamento
                (campo "Julgamento" da busca avançada). Formato ``DD/MM/AAAA``.
            data_julgamento_fim (str): Ver ``data_julgamento_inicio``.
            data_publicacao_inicio (str): Filtra pela data de publicação
                (campo "Publicação" da busca avançada). Formato ``DD/MM/AAAA``.
            data_publicacao_fim (str): Ver ``data_publicacao_inicio``.

        Raises:
            TypeError: Quando um kwarg desconhecido é passado.
            ValueError: Quando ``data_julgamento_*`` e ``data_publicacao_*``
                são passados ao mesmo tempo — a UI permite combinar os dois
                tipos de data via um botão "+" na busca avançada, mas esse
                fluxo ainda não foi implementado neste scraper.
            ImportError: Quando o pacote ``playwright`` (extra ``tse``) não
                está instalado.
            juscraper.courts.tse.download.TSEUnexpectedResponseError:
                Quando o backend devolve um formato de resposta inesperado
                (observado de forma intermitente, sem termo específico
                associado — possível sinal transitório de rate limit/anti
                -bot do hCaptcha). Tentar a busca de novo costuma resolver.
            juscraper.core.exceptions.BotChallengeBlockedError: Quando o
                Akamai bloqueia a navegação em si (HTTP 403 "Access
                Denied"). Observado consistentemente com ``headless=True``
                (default); ``TSEScraper(headless=False)`` é o primeiro
                workaround a tentar antes de esperar ou trocar de IP.

        Returns:
            list[dict]: Uma resposta JSON bruta por página.

        See also:
            :class:`InputCJSGTSE` — schema pydantic dos filtros aceitos.
        """
        if (data_julgamento_inicio or data_julgamento_fim) and (
            data_publicacao_inicio or data_publicacao_fim
        ):
            raise ValueError(
                "TSEScraper.cjsg_download() nao suporta filtrar por "
                "data_julgamento_* e data_publicacao_* ao mesmo tempo "
                "(fluxo de combinar os dois tipos de data na busca avancada "
                "ainda nao foi implementado). Use apenas um dos dois pares."
            )
        inp = apply_input_pipeline_search(
            InputCJSGTSE,
            "TSEScraper.cjsg_download()",
            pesquisa=pesquisa,
            paginas=paginas,
            kwargs=kwargs,
            consume_pesquisa_aliases=True,
            data_julgamento_inicio=data_julgamento_inicio,
            data_julgamento_fim=data_julgamento_fim,
            data_publicacao_inicio=data_publicacao_inicio,
            data_publicacao_fim=data_publicacao_fim,
            tamanho_pagina=tamanho_pagina,
            baixar_pdf=baixar_pdf,
        )
        return cjsg_download_manager(
            pesquisa=inp.pesquisa,
            paginas=inp.paginas,
            tamanho_pagina=inp.tamanho_pagina,
            baixar_pdf=inp.baixar_pdf,
            tribunal=self.tribunal,
            data_julgamento_inicio=inp.data_julgamento_inicio,
            data_julgamento_fim=inp.data_julgamento_fim,
            data_publicacao_inicio=inp.data_publicacao_inicio,
            data_publicacao_fim=inp.data_publicacao_fim,
            download_path=self.download_path,
            headless=self.headless,
            verbose=self.verbose,
        )

    def cjsg_parse(self, resultados_brutos: list[dict]) -> pd.DataFrame:
        """Extrai os dados relevantes dos resultados brutos devolvidos pelo TSE.

        Args:
            resultados_brutos (list[dict]): Saída de :meth:`cjsg_download`.

        Returns:
            pd.DataFrame: Uma linha por decisão.
        """
        return cjsg_parse_manager(resultados_brutos)

    def cjsg(
        self,
        pesquisa: str | None = None,
        paginas: int | list | range | None = None,
        tamanho_pagina: Literal[1, 10, 25, 50, 100, 250] = 250,
        baixar_pdf: bool = False,
        data_julgamento_inicio: str | None = None,
        data_julgamento_fim: str | None = None,
        data_publicacao_inicio: str | None = None,
        data_publicacao_fim: str | None = None,
        **kwargs,
    ) -> pd.DataFrame:
        """Consulta a jurisprudência do TSE de forma simplificada (download + parse).

        Args:
            pesquisa (str): Termo de busca livre. Ver :meth:`cjsg_download`
                para a sintaxe aceita.
            paginas (int | list | range | None): Ver :meth:`cjsg_download`.
            tamanho_pagina (int): Ver :meth:`cjsg_download`.
            baixar_pdf (bool): Ver :meth:`cjsg_download`.
            data_julgamento_inicio (str): Ver :meth:`cjsg_download`.
            data_julgamento_fim (str): Ver :meth:`cjsg_download`.
            data_publicacao_inicio (str): Ver :meth:`cjsg_download`.
            data_publicacao_fim (str): Ver :meth:`cjsg_download`.

        Returns:
            pd.DataFrame: Colunas ``processo``, ``classe``, ``assunto``,
            ``relator``, ``ementa``, ``decisao``, ``data_julgamento``,
            ``tipo_decisao``, ``codigo_decisao``, ``url_inteiro_teor`` e
            (com ``baixar_pdf=True``) ``caminho_pdf``.

        Exemplo:
            >>> import juscraper as jus
            >>> tse = jus.scraper("tse")
            >>> df = tse.cjsg("direito de resposta", paginas=1)
            >>> df2 = tse.cjsg("eleição", data_julgamento_inicio="01/01/2024",
            ...                data_julgamento_fim="31/01/2024")

        See also:
            :class:`InputCJSGTSE` — schema pydantic dos filtros aceitos.
        """
        brutos = self.cjsg_download(
            pesquisa=pesquisa,
            paginas=paginas,
            tamanho_pagina=tamanho_pagina,
            baixar_pdf=baixar_pdf,
            data_julgamento_inicio=data_julgamento_inicio,
            data_julgamento_fim=data_julgamento_fim,
            data_publicacao_inicio=data_publicacao_inicio,
            data_publicacao_fim=data_publicacao_fim,
            **kwargs,
        )
        return self.cjsg_parse(brutos)
