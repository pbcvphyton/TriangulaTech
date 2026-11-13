"""Um Congresso de mentira, pequeno o bastante para conferir as contas à mão.

Os testes do analítico do Congresso não acessam a rede: :func:`fetch_falso` responde
às mesmas URLs das fontes reais (arquivos em lote e API da Câmara, API do Senado)
com o conteúdo montado aqui, no formato de cada uma.

**Câmara, 2026** (hoje = 13/11/2026). Onze deputados: três do PT (1 a 3), três do PL
(4 a 6) e cinco do PP (7 a 11). O 10 vota com o PL; o 11 é suplente e só assume em
01/06; o 9 preside a Câmara desde 2025. Votações de Plenário:

====  =====  ============  ================================  ======  ===  ===  ============
 id   data   tipo          matéria                           Governo  PT   PL   PP
====  =====  ============  ================================  ======  ===  ===  ============
V1    03/03  texto         PL 10/2026 (economia)             Sim      S    N    S (10: N)
V2    10/03  texto         PL 11/2026 (segurança)            Não      N    S    S
V3    17/03  requerimento  PL 11/2026                        Não      N    S    N
V4    07/04  destaque      PL 10/2026                        Sim      S    N    S (10: N)
V5    09/06  urgência      PL 12/2026                        —        S    S    S (2 falta)
V6    16/06  texto         PL 12/2026 (apresentado em 01/06) Sim      S    S    S (3 falta)
====  =====  ============  ================================  ======  ===  ===  ============

Só V1, V2 e V4 são de conteúdo e dividem os polos. O deputado 7 (PP) fica com a
esquerda em V1 e V4 e com a direita em V2: saldo −33. O 10 fica sempre com a
direita: +100, e é dissidente do PP em V1 e V4. Há ainda uma votação na CCJC (V7),
que não entra nas contas de Plenário.

**Senado, 2026**: quatro senadores (101 PT, 102 PL, 103 MDB, 104 PSD) e duas
votações: uma aberta (PL 20/2026, PT Sim × PL Não) e uma secreta (indicação).
"""

from __future__ import annotations

import json
from typing import Any

from qijournal.congresso import regras
from qijournal.net import FetchError, Response

HOJE = "2026-11-13"
CAMARA = "https://dadosabertos.camara.leg.br"
SENADO = "https://legis.senado.leg.br/dadosabertos"

DEPUTADOS = [
    (1, "Ana Lima", "PT", "SP"),
    (2, "Bia Reis", "PT", "RJ"),
    (3, "Caio Luz", "PT", "MG"),
    (4, "Davi Rocha", "PL", "SP"),
    (5, "Eva Prado", "PL", "RJ"),
    (6, "Fábio Nunes", "PL", "MG"),
    (7, "Gil Castro", "PP", "BA"),
    (8, "Hugo Leal", "PP", "PE"),
    (9, "Íris Mota", "PP", "CE"),
    (10, "João Paz", "PP", "GO"),
    (11, "Lia Sá", "PP", "SP"),
]
PT, PL, PP = (1, 2, 3), (4, 5, 6), (7, 8, 9, 10)

# id, data, hora, órgão, aprovação, descrição, proposição afetada (id, sigla, número), governo, votos {deputado: voto}
VOTACOES: list[tuple[str, str, str, str, str, str, tuple[int, str, int], str, dict[int, str]]] = [
    ("1010-1", "2026-03-03", "15:00", "PLEN", "1", "Aprovado o Substitutivo ao Projeto de Lei nº 10, de 2026. Sim: 6; Não: 4; Total: 10.", (1010, "PL", 10), "Sim",
     {**{d: "Sim" for d in PT}, **{d: "Não" for d in PL}, 7: "Sim", 8: "Sim", 9: "Sim", 10: "Não"}),
    ("1011-1", "2026-03-10", "16:00", "PLEN", "1", "Aprovado o Projeto de Lei nº 11, de 2026. Sim: 7; Não: 3; Total: 10.", (1011, "PL", 11), "Não",
     {**{d: "Não" for d in PT}, **{d: "Sim" for d in PL}, **{d: "Sim" for d in PP}}),
    ("1011-2", "2026-03-17", "16:30", "PLEN", "0", "Rejeitado o Requerimento. Sim: 3; Não: 7; Total: 10.", (1011, "PL", 11), "Não",
     {**{d: "Não" for d in PT}, **{d: "Sim" for d in PL}, **{d: "Não" for d in PP}}),
    ("1010-2", "2026-04-07", "17:00", "PLEN", "1", "Mantido o texto. Sim: 6; Não: 4; Total: 10.", (1010, "PL", 10), "Sim",
     {**{d: "Sim" for d in PT}, **{d: "Não" for d in PL}, 7: "Sim", 8: "Sim", 9: "Artigo 17", 10: "Não"}),
    ("1012-1", "2026-06-09", "18:00", "PLEN", "1", "Aprovado o Requerimento de Urgência (Art. 155 do RICD). Sim: 10; Total: 10.", (1012, "PL", 12), "Liberado",
     {d: "Sim" for d in (1, 3, 4, 5, 6, 7, 8, 9, 10, 11)}),
    ("1012-2", "2026-06-16", "19:00", "PLEN", "1", "Aprovado o Projeto de Lei nº 12, de 2026. Sim: 10; Total: 10.", (1012, "PL", 12), "Sim",
     {d: "Sim" for d in (1, 2, 4, 5, 6, 7, 8, 9, 10, 11)}),
    ("1010-3", "2026-05-05", "10:00", "CCJC", "1", "Aprovado o Parecer.", (1010, "PL", 10), "",
     {1: "Sim", 4: "Não", 7: "Sim"}),
]

# id, sigla, número, ementa, apresentação, situação, órgão, último andamento, relator, tema (código), autores [(deputado, ordem, proponente)], autoria de fora
PROPOSICOES: list[tuple[int, str, int, str, str, str, str, str, int | None, int | None, list[tuple[int, int, int]], str]] = [
    (1010, "PL", 10, "Altera a tabela do imposto de renda.", "2026-01-05", "Aguardando Designação de Relator(a)", "CCJC", "2026-01-10", None, 40, [(1, 1, 1)], ""),
    (1011, "PL", 11, "Aumenta penas para o crime organizado.", "2026-01-20", "Aguardando Parecer", "CSPCCO", "2026-02-01", 7, 43, [(4, 1, 1)], ""),
    (1012, "PL", 12, "Abre crédito para a reconstrução de estradas.", "2026-06-01", "Transformado em Norma Jurídica", "MESA", "2026-07-01", None, 61, [], "Poder Executivo"),
    (1013, "PEC", 1, "Altera o sistema eleitoral.", "2026-02-10", "Aguardando Criação de Comissão Temporária", "MESA", "2026-02-12", None, 74, [(4, 1, 1), (5, 2, 0), (7, 3, 0)], ""),
    (1014, "PL", 13, "Cria o dia nacional do leitor.", "2026-03-01", "Arquivada", "MESA", "2026-09-01", None, 72, [(1, 1, 1), (2, 2, 1)], ""),
    (1015, "PL", 14, "Regula o trabalho por aplicativo.", "2026-02-20", "Pronta para Pauta", "PLEN", "2026-03-01", None, 58, [(1, 1, 1)], ""),
    (1016, "REQ", 5, "Requer audiência pública.", "2026-03-02", "", "CCJC", "2026-03-02", None, None, [(1, 1, 1)], ""),
]

SENADORES = [(101, "Rita Alves", "PT", "BA"), (102, "Sérgio Dias", "PL", "SC"), (103, "Túlio Braga", "MDB", "PA"), (104, "Vera Campos", "PSD", "MG")]


def regras_de_teste() -> regras.Regras:
    """Réguas pequenas, na escala deste Congresso de mentira (uma legislatura, só 2026)."""
    return regras.de_dict(
        {
            "legislaturas": [{"numero": 57, "inicio": "2026-01-01", "fim": "2027-01-31"}],
            "governos": [{"nome": "Lula", "inicio": "2023-01-01"}],
            "polos": [{"inicio": "2019-01-01", "esquerda": ["PT"], "direita": ["PL"]}],
            "limiares": {
                "coesao_polo": 0.75,
                "minimo_polo": 2,
                "minimo_divisivas": 2,
                "coesao_partido": 0.7,
                "minimo_partido": 2,
                "minimo_presenca": 2,
                "dias_parado": 100,
                "dias_relatoria": 100,
            },
            "temas": [
                {"id": "economia", "nome": "Economia", "camara": [40], "senado": ["Economia e Desenvolvimento"]},
                {"id": "seguranca", "nome": "Segurança", "camara": [43], "senado": ["Jurídico / Direito Penal e Penitenciário"]},
                {"id": "cidades", "nome": "Cidades", "camara": [61], "senado": ["Infraestrutura"]},
                {"id": "politica", "nome": "Política", "camara": [74], "senado": ["Organização do Estado"]},
                {"id": "trabalho", "nome": "Trabalho", "camara": [58], "senado": []},
                {"id": "homenagens", "nome": "Homenagens", "camara": [72], "senado": ["Honorífico"]},
            ],
        }
    )


def csv_bytes(colunas: list[str], linhas: list[list[Any]]) -> bytes:
    """CSV como os da Câmara: BOM, ``;`` e tudo entre aspas."""
    def celula(valor: Any) -> str:
        return '"' + str("" if valor is None else valor).replace('"', '""') + '"'

    texto = "\n".join(";".join(celula(c) for c in linha) for linha in [colunas, *linhas]) + "\n"
    return b"\xef\xbb\xbf" + texto.encode("utf-8")


def _uri(tipo: str, ident: int | None) -> str:
    return f"{CAMARA}/api/v2/{tipo}/{ident}" if ident else ""


def arquivos_da_camara() -> dict[str, bytes]:
    nomes = {d[0]: d for d in DEPUTADOS}
    votos, orientacoes, afetadas, votacoes = [], [], [], []
    for vid, data, hora, orgao, aprovacao, descricao, (pid, sigla, numero), governo, quem in VOTACOES:
        votacoes.append([vid, data, f"{data}T{hora}:00", orgao, aprovacao, descricao])
        afetadas.append([vid, pid, sigla, numero, 2026, next(p[3] for p in PROPOSICOES if p[0] == pid)])
        if governo:
            orientacoes.append([vid, "GOV.", governo])
            orientacoes.append([vid, "PT", "Sim" if governo == "Sim" else "Não"])
        for dep, voto in quem.items():
            _id, nome, partido, uf = nomes[dep]
            votos.append([vid, voto, dep, nome, partido, uf])
    proposicoes, autores, temas = [], [], []
    for pid, sigla, numero, ementa, apresentada, situacao, orgao, movimento, relator, tema, assinam, de_fora in PROPOSICOES:
        proposicoes.append([pid, sigla, numero, 2026, ementa, f"{apresentada}T10:00:00", situacao, orgao, f"{movimento}T00:00:00", _uri("deputados", relator), "Ordinário"])
        if tema:
            temas.append([_uri("proposicoes", pid), tema])
        for dep, ordem, proponente in assinam:
            autores.append([pid, dep, nomes[dep][1], ordem, proponente])
        if de_fora:
            autores.append([pid, "", de_fora, 1, 1])
    orgaos = [["CCJC", _uri("deputados", 8), "Hugo Leal", "Presidente", "2026-02-05", ""], ["CCJC", _uri("deputados", 1), "Ana Lima", "Titular", "2026-02-05", ""]]
    return {
        "votacoes/csv/votacoes-2026.csv": csv_bytes(["id", "data", "dataHoraRegistro", "siglaOrgao", "aprovacao", "descricao"], votacoes),
        "votacoesVotos/csv/votacoesVotos-2026.csv": csv_bytes(["idVotacao", "voto", "deputado_id", "deputado_nome", "deputado_siglaPartido", "deputado_siglaUf"], votos),
        "votacoesOrientacoes/csv/votacoesOrientacoes-2026.csv": csv_bytes(["idVotacao", "siglaBancada", "orientacao"], orientacoes),
        "votacoesProposicoes/csv/votacoesProposicoes-2026.csv": csv_bytes(
            ["idVotacao", "proposicao_id", "proposicao_siglaTipo", "proposicao_numero", "proposicao_ano", "proposicao_ementa"], afetadas
        ),
        "proposicoes/csv/proposicoes-2026.csv": csv_bytes(
            ["id", "siglaTipo", "numero", "ano", "ementa", "dataApresentacao", "ultimoStatus_descricaoSituacao", "ultimoStatus_siglaOrgao", "ultimoStatus_dataHora", "ultimoStatus_uriRelator", "ultimoStatus_regime"],
            proposicoes,
        ),
        "proposicoesAutores/csv/proposicoesAutores-2026.csv": csv_bytes(["idProposicao", "idDeputadoAutor", "nomeAutor", "ordemAssinatura", "proponente"], autores),
        "proposicoesTemas/csv/proposicoesTemas-2026.csv": csv_bytes(["uriProposicao", "codTema"], temas),
        "orgaosDeputados/csv/orgaosDeputados-L57.csv": csv_bytes(["siglaOrgao", "uriDeputado", "nomeDeputado", "cargo", "dataInicio", "dataFim"], orgaos),
    }


def historico(dep: int) -> list[dict[str, Any]]:
    inicio = "2026-06-01T10:00" if dep == 11 else "2023-02-01T12:05"
    return [
        {"dataHora": "2023-02-01T00:00", "situacao": None, "idLegislatura": 57},
        {"dataHora": inicio, "situacao": "Exercício", "idLegislatura": 57},
    ]


def votacoes_do_senado() -> list[dict[str, Any]]:
    def voto(codigo: int, sigla: str) -> dict[str, Any]:
        _c, nome, partido, uf = next(s for s in SENADORES if s[0] == codigo)
        return {"codigoParlamentar": codigo, "nomeParlamentar": nome, "siglaPartidoParlamentar": partido, "siglaUFParlamentar": uf, "siglaVotoParlamentar": sigla}

    return [
        {
            "sequencialVotacao": 501, "codigoSessaoVotacao": 9001, "dataSessao": "2026-05-12", "votacaoSecreta": "N", "resultadoVotacao": "A",
            "sigla": "PL", "identificacao": "PL 20/2026", "idProcesso": 7001, "dataApresentacao": "2026-04-30",
            "descricaoVotacao": "Votação nominal do Projeto de Lei nº 20, de 2026, nos termos do parecer.", "ementa": "Altera o imposto de renda.",
            "votos": [voto(101, "Sim"), voto(102, "Não"), voto(103, "Sim"), voto(104, "AP")],
        },
        {
            "sequencialVotacao": 502, "codigoSessaoVotacao": 9002, "dataSessao": "2026-05-13", "votacaoSecreta": "S", "resultadoVotacao": "A",
            "sigla": "MSF", "identificacao": "MSF 3/2026", "idProcesso": 7002, "descricaoVotacao": "Votação nominal da Mensagem nº 3.", "ementa": "Indica embaixador.",
            "votos": [voto(101, "Votou"), voto(102, "NCom"), voto(103, "Presidente (art. 51 RISF)"), voto(104, "P-NRV")],
        },
    ]


def processos_do_senado() -> list[dict[str, Any]]:
    return [
        {"id": 7001, "codigoMateria": 160001, "identificacao": "PL 20/2026", "ementa": "Altera o imposto de renda.", "dataApresentacao": "2026-04-30",
         "situacaoAtual": "REMETIDA À CÂMARA DOS DEPUTADOS", "dataSituacaoAtual": "2026-05-20", "tramitando": "Não", "autoria": "Senadora Rita Alves (PT/BA)"},
        {"id": 7003, "codigoMateria": 160003, "identificacao": "PL 21/2026", "ementa": "Cria o cadastro de obras paradas.", "dataApresentacao": "2026-01-15",
         "situacaoAtual": "AGUARDANDO DESIGNAÇÃO DO RELATOR", "dataSituacaoAtual": "2026-01-20", "tramitando": "Sim", "autoria": "Senadora Rita Alves (PT/BA), Senador Túlio Braga (MDB/PA)"},
        {"id": 7004, "codigoMateria": 160004, "identificacao": "RQS 9/2026", "ementa": "Requer voto de aplauso.", "dataApresentacao": "2026-02-01",
         "situacaoAtual": "AGUARDANDO DESPACHO", "dataSituacaoAtual": "2026-02-01", "tramitando": "Sim", "autoria": "Senadora Rita Alves (PT/BA)"},
    ]


def busca_do_dia() -> dict[str, Any]:
    """Um arquivo de busca (``data/busca/2026-11-12.json``) com manchetes que citam os deputados."""
    return {
        "v": 1,
        "data": "2026-11-12",
        "secoes": [["politica", "Política", "#C90055"], ["esporte", "Esporte", "#B45309"]],
        "veiculos": ["Folha", "Valor"],
        "materias": [{"id": "pacote-fiscal", "s": 0, "t": "Pacote fiscal avança", "l": "Relator promete parecer", "c": "O deputado Gil Castro (PP-BA) disse que entrega o texto."}],
        "noticias": [
            [0, -1, 0, "Davi Rocha critica o pacote fiscal", "folha.example/a", 10],
            [0, -1, 1, "Deputado Hugo Leal defende a reforma", "valor.example/b", 20],
            [1, -1, 0, "Davi Rocha marca dois gols no clássico", "folha.example/c", 30],
            [0, -1, 1, "João Paz e Davi Rocha votam contra o governo", "valor.example/d", 40],
        ],
    }


def respostas() -> dict[str, bytes]:
    """URL (sem o esquema e o host) → corpo da resposta."""
    def dados(lista: Any) -> bytes:
        return json.dumps({"dados": lista, "links": []}).encode("utf-8")

    mapa = {f"/arquivos/{caminho}": corpo for caminho, corpo in arquivos_da_camara().items()}
    mapa["/api/v2/deputados?ordem=ASC&ordenarPor=nome&itens=100"] = dados(
        [{"id": i, "nome": nome, "siglaPartido": partido, "siglaUf": uf} for i, nome, partido, uf in DEPUTADOS]
    )
    for dep, *_resto in DEPUTADOS:
        mapa[f"/api/v2/deputados/{dep}/historico"] = dados(historico(dep))
    mapa["/api/v2/legislaturas/57/mesa"] = dados(
        [{"id": 9, "nome": "Íris Mota", "titulo": "Presidente", "dataInicio": "2025-02-01", "dataFim": None}, {"id": 8, "nome": "Hugo Leal", "titulo": "1º Vice-Presidente", "dataInicio": "2025-02-01", "dataFim": None}]
    )
    s = "/dadosabertos"
    mapa[f"{s}/votacao?dataInicio=2026-01-01&dataFim={HOJE}"] = json.dumps(votacoes_do_senado()).encode("utf-8")
    mapa[f"{s}/plenario/votacao/orientacaoBancada/20260101/{HOJE.replace('-', '')}"] = json.dumps(
        {"votacoes": [{"sequencialVotacao": 501, "dataInicioVotacao": "2026-05-12T17:10:00", "descricaoVotacao": "Projeto de Lei nº 20", "orientacoesLideranca": [{"partido": "Governo", "voto": "SIM"}, {"partido": "PL", "voto": "NÃO"}]}]}
    ).encode("utf-8")
    mapa[f"{s}/processo/7001"] = json.dumps({"id": 7001, "classificacoes": [{"descricaoHierarquia": "Economia e Desenvolvimento / Tributos"}]}).encode("utf-8")
    mapa[f"{s}/processo/7002"] = json.dumps({"id": 7002, "classificacoes": []}).encode("utf-8")
    mapa[f"{s}/senador/lista/atual"] = json.dumps(
        {"ListaParlamentarEmExercicio": {"Parlamentares": {"Parlamentar": [
            {"IdentificacaoParlamentar": {"CodigoParlamentar": str(c), "NomeParlamentar": nome, "SiglaPartidoParlamentar": partido, "UfParlamentar": uf}, "Mandato": {"DescricaoParticipacao": "Titular"}}
            for c, nome, partido, uf in SENADORES
        ]}}}
    ).encode("utf-8")
    mapa[f"{s}/processo?tramitando=S"] = json.dumps([p for p in processos_do_senado() if p["tramitando"] == "Sim"]).encode("utf-8")
    for codigo, nome, *_resto in SENADORES:
        meus = [p for p in processos_do_senado() if nome in p["autoria"]]
        mapa[f"{s}/processo?codigoParlamentarAutor={codigo}"] = json.dumps(meus).encode("utf-8")
    mapa[f"{s}/processo/relatoria?tramitando=S"] = json.dumps(
        [{"codigoParlamentar": 103, "identificacaoProcesso": "PL 21/2026", "codigoMateria": 160003, "ementaProcesso": "Cria o cadastro de obras paradas.", "dataDesignacao": "2026-02-01 10:00:00", "dataDestituicao": None, "siglaColegiado": "CI", "autoriaProcesso": "Senadora Rita Alves"}]
    ).encode("utf-8")
    return mapa


class FetchFalso:
    """``fetch`` de teste: responde pelo caminho da URL e anota o que foi pedido. Uma URL
    que não está no mapa (ou está em ``fora_do_ar``) falha como a rede falharia."""

    def __init__(self, mapa: dict[str, bytes] | None = None) -> None:
        self.mapa = respostas() if mapa is None else mapa
        self.pedidos: list[str] = []
        self.fora_do_ar: set[str] = set()

    def __call__(self, url: str, **_kwargs: Any) -> Response:
        self.pedidos.append(url)
        caminho = "/" + url.split("://", 1)[-1].split("/", 1)[-1]
        if caminho not in self.mapa or any(trecho in url for trecho in self.fora_do_ar):
            raise FetchError(url, "HTTP 404", status=404)
        return Response(url=url, status=200, content=self.mapa[caminho], headers={"content-type": "application/json"})
