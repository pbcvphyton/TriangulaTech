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
from .regras import Regras, Tema

log = logging.getLogger(__name__)

VALIDADE_DETALHE_H = 24 * 60  # a classificação de uma matéria quase nunca muda

# As áreas temáticas da Câmara (código do arquivo proposicoesTemas → nome oficial): servem
# para dizer, na página, o que entra em cada macrotema.
AREAS_CAMARA = {
    34: "Administração Pública",
    35: "Arte, Cultura e Religião",
    37: "Comunicações",
    39: "Esporte e Lazer",
    40: "Economia",
    41: "Cidades e Desenvolvimento Urbano",
    42: "Direito Civil e Processual Civil",
    43: "Direito Penal e Processual Penal",
    44: "Direitos Humanos e Minorias",
    46: "Educação",
    48: "Meio Ambiente e Desenvolvimento Sustentável",
    51: "Estrutura Fundiária",
    52: "Previdência e Assistência Social",
    53: "Processo Legislativo e Atuação Parlamentar",
    54: "Energia, Recursos Hídricos e Minerais",
    55: "Relações Internacionais e Comércio Exterior",
    56: "Saúde",
    57: "Defesa e Segurança",
    58: "Trabalho e Emprego",
    60: "Turismo",
    61: "Viação, Transporte e Mobilidade",
    62: "Ciência, Tecnologia e Inovação",
    64: "Agricultura, Pecuária, Pesca e Extrativismo",
    66: "Indústria, Comércio e Serviços",
    67: "Direito e Defesa do Consumidor",
    68: "Direito Constitucional",
    70: "Finanças Públicas e Orçamento",
    72: "Homenagens e Datas Comemorativas",
    74: "Política, Partidos e Eleições",
    76: "Direito e Justiça",
    85: "Ciências Exatas e da Terra",
    86: "Ciências Sociais e Humanas",
}


def definicao(tema: Tema) -> dict[str, list[str]]:
    """O que entra num macrotema: as áreas temáticas da Câmara e as classes do Senado."""
    return {
        "camara": [AREAS_CAMARA.get(c, f"área {c}") for c in sorted(tema.camara, key=lambda c: AREAS_CAMARA.get(c, str(c)))],
        "senado": list(tema.senado_nomes),
    }


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
