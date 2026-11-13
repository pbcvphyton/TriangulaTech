"""De que trata cada matéria votada: a classificação oficial de cada Casa, levada
aos macrotemas de ``config/congresso.yaml``.

- **Câmara**: as áreas temáticas atribuídas pelo Centro de Documentação e
  Informação (arquivo em lote ``proposicoesTemas-<ano>.csv``, um por ano da
  proposição), lidas só para os anos que aparecem nas votações.
- **Senado**: as classes do processo (``/processo/<id>``, campo
  ``classificacoes``), uma consulta por matéria votada, guardada no cache.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from . import fontes
from .fontes import Coletor
from .regras import Regras

log = logging.getLogger(__name__)

VALIDADE_DETALHE_H = 24 * 60  # a classificação de uma matéria quase nunca muda


class TemasCamara:
    """``temas(id_da_proposição, ano_da_proposição)`` → macrotemas, na ordem do arquivo."""

    def __init__(self, coletor: Coletor, regras: Regras, *, validade_h: float = 24 * 7) -> None:
        self._coletor = coletor
        self._regras = regras
        self._validade_h = validade_h
        self._anos: dict[int, dict[int, list[str]]] = {}

    def _ano(self, ano: int) -> dict[int, list[str]]:
        if ano not in self._anos:
            mapa: dict[int, list[str]] = defaultdict(list)
            try:
                arquivo = fontes.camara_arquivo(self._coletor, "proposicoesTemas", ano, validade_h=self._validade_h)
                for r in fontes.csv_linhas(arquivo):
                    pid = fontes.id_da_uri(r.get("uriProposicao"))
                    tema = self._regras.tema_camara(fontes.inteiro(r.get("codTema")))
                    if pid is not None and tema and tema not in mapa[pid]:
                        mapa[pid].append(tema)
            except Exception as exc:  # noqa: BLE001 — sem o arquivo do ano, as matérias dele ficam sem tema
                log.warning("Câmara: temas de %d indisponíveis (%s)", ano, exc)
            self._anos[ano] = dict(mapa)
        return self._anos[ano]

    def __call__(self, prop_id: int | None, prop_ano: int | None) -> list[str]:
        if prop_id is None or not prop_ano or not 1900 < prop_ano < 2200:
            return []
        return list(self._ano(prop_ano).get(prop_id, []))


class TemasSenado:
    """``temas(id_do_processo)`` → macrotemas, pela classificação do processo."""

    def __init__(self, coletor: Coletor, regras: Regras) -> None:
        self._coletor = coletor
        self._regras = regras
        self._visto: dict[int, list[str]] = {}

    def __call__(self, id_processo: int | None) -> list[str]:
        if id_processo is None:
            return []
        if id_processo not in self._visto:
            temas: list[str] = []
            try:
                detalhe = fontes.senado_api(self._coletor, f"processo/{id_processo}", validade_h=VALIDADE_DETALHE_H)
                for classe in (detalhe.get("classificacoes") if isinstance(detalhe, dict) else None) or []:
                    tema = self._regras.tema_senado(classe.get("descricaoHierarquia") or classe.get("descricao"))
                    if tema and tema not in temas:
                        temas.append(tema)
            except Exception as exc:  # noqa: BLE001 — uma matéria sem detalhe só fica sem tema
                log.debug("Senado: processo %s sem classificação (%s)", id_processo, exc)
            self._visto[id_processo] = temas
        return list(self._visto[id_processo])
