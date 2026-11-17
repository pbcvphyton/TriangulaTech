"""A leitura dos projetos de lei pela régua "Projeto de país" (qijournal.congresso.leitura):
a conta de cada leitura, as duas fontes (regras e IA), a guarda, a importação e o que falta
mandar à IA."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qijournal.__main__ import main
from qijournal.congresso import base, cli, dados, fontes, leitor_regras, leitura, projetos, regras
from qijournal.config import ROOT
from tests.fixtures.congresso import fabrica


@pytest.fixture(scope="module")
def regua() -> regras.ProjetoDePais:
    """A régua de verdade, de config/congresso.yaml."""
    r = regras.carregar(ROOT).projeto_de_pais
    assert r is not None
    return r


def _lida(codigos: str, **campos) -> leitura.Leitura:
    """``"fiscal geral ajuste neutro neutro afrouxa"`` → a leitura (a, e, m, ct, is, co)."""
    return leitura.Leitura(codigos=tuple(codigos.split()), **campos)


# ── a fórmula ────────────────────────────────────────────────────────────────

# As doze ementas da seção 7 da régua: matéria, códigos (alavanca, escopo, mecanismo, custo,
# isonomia, controle), in, cf, peso e faixa da tabela (None: sem leitura).
DOZE = [
    ("PL 268/2025", "outro local simbolico neutro neutro neutro", 0, 3, 0, "mínimo"),
    ("PL 6424/2025", "social grupo diretriz nd neutro neutro", 0, 3, 4, "mínimo"),
    ("PL 6437/2025", "justica grupo pena prisional neutro neutro", 0, 3, 10, "pontual"),
    ("PL 6488/2025", "social grupo conduta neutro neutro neutro", 0, 2, 10, "pontual"),
    ("PL 3816/2024", "social grupo servico gasto neutro neutro", 1, 2, 12, "pontual"),
    ("PLP 236/2025", "fiscal grupo beneficio gasto profissao neutro", 1, 3, 16, "pontual"),
    ("PL 518/2023", "infra grupo beneficio repassa pessoal neutro", 0, 3, 16, "pontual"),
    ("PL 3268/2023", "produtividade grupo ajuste neutro neutro neutro", 0, 2, 24, "médio alcance"),
    ("PLP 92/2025", "fiscal geral ajuste neutro neutro afrouxa", 0, 3, 60, "estruturante"),
    ("PLP 128/2025", "fiscal geral ajuste corta iguala aperta", 0, 3, 60, "estruturante"),
    ("PEC 63/2023", "fiscal geral sistema nd neutro neutro", 0, 3, 100, "estruturante"),
    ("PLP 29/2025", "fiscal nd nd nd nd nd", 0, 3, None, None),
]


@pytest.mark.parametrize(("materia", "codigos", "alerta", "cf", "peso", "faixa"), DOZE, ids=[d[0] for d in DOZE])
def test_o_peso_e_a_faixa_das_doze_ementas_da_regua(regua, materia: str, codigos: str, alerta: int, cf: int, peso: int | None, faixa: str | None):
    """Peso = 100 × nível × escopo × mecanismo. PL 6437: 100 × 0,6 × 0,4 × 0,4 = 9,6 → 10
    (pontual); PL 3268: 100 × 1,0 × 0,4 × 0,6 = 24; PEC 63: 100 × 1 × 1 × 1 = 100."""
    lida = _lida(codigos, iniciativa=alerta, confianca=cf)
    a = leitura.avaliar(regua, lida, tipo=materia.split()[0], origem="parlamentar")
    assert leitura.peso(regua, lida) == peso
    if peso is None:
        assert not a.lida and a.motivo == "nd" and a.faixa == "" and a.conta == ""
        return
    assert a.lida and a.peso == peso
    assert regua.faixa(a.faixa_da_ementa).rotulo == faixa
    # Os dois que acendem o alerta de iniciativa na tabela: PL 3816/2024 e PLP 236/2025.
    assert (leitura.INICIATIVA in a.alertas) == (materia in ("PL 3816/2024", "PLP 236/2025"))


def test_as_96_combinacoes_caem_nas_faixas_que_a_regua_diz(regua):
    """3 níveis × 4 escopos × 8 mecanismos: 3 estruturantes, 22 de médio alcance, 22 pontuais
    e 49 mínimas, em 25 valores de peso (seção 3 da régua)."""
    contagem: dict[str, int] = {}
    pesos = set()
    um_de_cada_nivel = {c.nivel: c.id for c in reversed(regua.alavanca.codigos)}
    assert len(um_de_cada_nivel) == 3
    for alavanca in um_de_cada_nivel.values():
        for escopo in regua.campo("escopo").ids:
            for mecanismo in regua.campo("mecanismo").ids:
                a = leitura.avaliar(regua, _lida(f"{alavanca} {escopo} {mecanismo} neutro neutro neutro", r=leitura.DUAS))
                contagem[a.faixa] = contagem.get(a.faixa, 0) + 1
                pesos.add(a.peso)
                assert len(a.conta) <= 120  # o cartão do projeto reserva 120 caracteres para a conta
    assert contagem == {"estruturante": 3, "medio": 22, "pontual": 22, "minimo": 49}
    assert len(pesos) == 25 and min(pesos) == 0 and max(pesos) == 100


def test_os_pares_espelho_caem_na_mesma_faixa(regua):
    """O teste de aceite da régua: regular e desregular, gastar e cortar, saúde e pena pesam
    igual. Cada lado traz alavanca, escopo e mecanismo."""
    assert len(regua.pares_espelho) == 8
    for par in regua.pares_espelho:
        lados = [leitura.avaliar(regua, _lida(" ".join(lado.codigos) + " nd nd nd")) for lado in (par.a, par.b)]
        assert lados[0].lida and lados[1].lida, par.id
        assert lados[0].faixa_da_ementa == lados[1].faixa_da_ementa, par.id
        assert lados[0].faixa == lados[1].faixa, par.id
    # O último par (saúde ou pena) cai nos dois lados em 100 × 0,6 × 0,4 × 0,4 = 9,6 → 10.
    assert leitura.peso(regua, _lida("social grupo beneficio nd nd nd")) == leitura.peso(regua, _lida("justica grupo pena nd nd nd")) == 10


def _regua_pequena(**extra) -> regras.ProjetoDePais:
    """Uma régua de mentira, para a conta de cabeça: dois níveis (1,0 e 0,5), escopo de 1,0,
    0,5 e 0,1, mecanismo de 1,0, 0,5 e 0,45, e uma marca só. Faixas: baixa (0), média (10) e
    alta (50, só no nível "dentro", com segunda leitura a partir de 50)."""
    return regras.de_dict(
        {
            "projeto_de_pais": {
                "nivel": {"dentro": 1.0, "beira": 0.5},
                "campos": {
                    "onde": {"chave": "a", "codigos": [{"id": "casa", "nivel": "dentro", "rotulo": "Casa"}, {"id": "rua", "nivel": "beira", "rotulo": "Rua"}]},
                    "quem": {"chave": "e", "codigos": [{"id": "todos", "peso": 1.0, "rotulo": "Todos"}, {"id": "metade", "peso": 0.5}, {"id": "um", "peso": 0.1}]},
                    "como": {"chave": "m", "codigos": [{"id": "muda", "peso": 1.0, "rotulo": "Muda a regra"}, {"id": "ajusta", "peso": 0.5}, {"id": "quase", "peso": 0.45}]},
                    "caixa": {"chave": "ct", "marca": "contas", "codigos": [{"id": "gasta", "valor": -1}, {"id": "nada", "valor": 0}, {"id": "poupa", "valor": 1}]},
                },
                "faixas": [{"id": "baixa", "de": 0}, {"id": "media", "de": 10}, {"id": "alta", "de": 50, "so_nivel": "dentro"}],
                "segunda_leitura": {"peso_minimo": 50},
                **extra,
            }
        }
    ).projeto_de_pais


def test_o_peso_arredonda_meio_para_cima():
    """100 × 0,5 × 0,5 × 0,1 = 2,5 → 3 (e não 2, como daria o arredondamento do Python);
    100 × 0,5 × 0,1 × 0,45 = 2,25 → 2; 100 × 1,0 × 0,1 × 0,45 = 4,5 → 5."""
    r = _regua_pequena()
    assert round(2.5) == 2  # o arredondamento padrão iria para o par
    assert leitura.peso(r, _lida("rua metade ajusta nada")) == 13  # 12,5 → 13
    assert leitura.peso(r, _lida("rua um ajusta nada")) == 3  # 2,5 → 3
    assert leitura.peso(r, _lida("rua um quase nada")) == 2  # 2,25 → 2
    assert leitura.peso(r, _lida("casa um quase nada")) == 5  # 4,5 → 5
    assert leitura.conta_por_extenso(r, _lida("rua um ajusta nada")) == "100 × 0,5 (beira) × 0,1 (um) × 0,5 (ajusta) = 2,5, arredondado para 3"
    assert leitura.conta_por_extenso(r, _lida("casa todos muda nada")) == "100 × 1,0 (dentro) × 1,0 (todos) × 1,0 (muda a regra) = 100"


def test_na_regua_de_verdade_o_meio_sobe(regua):
    """Fora do objetivo, para todos, política declaratória: 100 × 0,3 × 1,0 × 0,15 = 4,5 → 5
    (em ponto flutuante a conta dá 4,4999… e cairia em 4)."""
    lida = _lida("outro geral diretriz nd neutro neutro")
    assert leitura.peso(regua, lida) == 5
    assert leitura.conta_por_extenso(regua, lida) == "100 × 0,3 (fora do objetivo) × 1,0 (todos) × 0,15 (política declaratória) = 4,5, arredondado para 5"
    assert leitura.conta_por_extenso(regua, _lida("infra setor sistema nd neutro neutro")) == "100 × 1,0 (núcleo) × 0,6 (um segmento inteiro) × 1,0 (regra de sistema) = 60"


def test_60_no_entorno_e_medio_alcance(regua):
    """Regra de sistema para todos na saúde: 100 × 0,6 × 1,0 × 1,0 = 60, mas a faixa do topo
    é só do núcleo. Fica em médio alcance com uma leitura ou com duas, sem o alerta de "a
    confirmar"; ainda assim, peso 60 pede a segunda leitura."""
    uma = _lida("social geral sistema nd neutro neutro")
    for r in (leitura.UMA, leitura.DUAS):
        a = leitura.avaliar(regua, leitura.Leitura(uma.codigos, r=r))
        assert (a.peso, a.faixa, a.faixa_da_ementa, a.nivel) == (60, "medio", "medio", "entorno")
        assert leitura.A_CONFIRMAR not in a.alertas
    assert leitura.pede_segunda(regua, uma) == (leitura.PESO_ALTO,)
    # No núcleo, os mesmos 60 são a faixa do topo.
    assert leitura.avaliar(regua, _lida("infra setor sistema nd neutro neutro", r=leitura.DUAS)).faixa == "estruturante"


@pytest.mark.parametrize("codigos", ["nd geral sistema neutro neutro neutro", "fiscal nd sistema neutro neutro neutro", "fiscal geral nd neutro neutro neutro"])
def test_nd_num_campo_de_peso_deixa_sem_leitura(regua, codigos: str):
    a = leitura.avaliar(regua, _lida(codigos, r=leitura.DUAS))
    assert not a.lida and a.motivo == "nd" and a.peso is None and a.faixa == "" and a.alertas == () and a.marcas == {}


def test_nd_numa_marca_nao_tira_a_leitura(regua):
    """O "nd" no custo deixa a marca de contas sem valor; o peso (100) e as outras marcas ficam."""
    a = leitura.avaliar(regua, _lida("fiscal geral sistema nd ramo aperta", r=leitura.DUAS))
    assert a.lida and a.peso == 100
    assert a.marcas == {"contas": None, "isonomia": -1, "controle": 1}
    assert a.sinais == {"contas": "nd", "isonomia": "excecao", "controle": "aperta"}


def test_com_uma_leitura_o_peso_60_nao_conta_como_estruturante(regua):
    """Ajuste fiscal para todos: 100 × 1,0 × 1,0 × 0,6 = 60, no núcleo. Com uma leitura
    (r = 1) conta como médio alcance e leva o alerta; com duas na mesma faixa (r = 2) ou com
    a terceira que decidiu (r = 3), é estruturante; instável (r = 0), fica sem leitura."""
    codigos = "fiscal geral ajuste corta iguala aperta"
    uma = leitura.avaliar(regua, _lida(codigos, r=leitura.UMA))
    assert (uma.peso, uma.faixa, uma.faixa_da_ementa) == (60, "medio", "estruturante")
    assert uma.alertas == (leitura.A_CONFIRMAR,)
    for r in (leitura.DUAS, leitura.TRES):
        confirmada = leitura.avaliar(regua, _lida(codigos, r=r))
        assert (confirmada.faixa, confirmada.faixa_da_ementa, confirmada.alertas) == ("estruturante", "estruturante", ())
    instavel = leitura.avaliar(regua, _lida(codigos, r=leitura.INSTAVEL))
    assert not instavel.lida and instavel.motivo == "instavel"
    assert not leitura.avaliar(regua, None).lida  # ainda não lida


def test_as_tres_marcas_valem_cada_uma_por_si(regua):
    """Cada marca leva o valor do seu código; nada é somado nem multiplicado pelo peso. Os
    demais campos saem em pares, na ordem do YAML (a alavanca vai à parte: é o eixo)."""
    a = leitura.avaliar(regua, _lida("fiscal grupo beneficio renuncia ramo neutro"))
    assert a.marcas == {"contas": -1, "isonomia": -1, "controle": 0}
    assert a.sinais == {"contas": "onera", "isonomia": "excecao", "controle": "neutro"}
    assert a.alavanca == "fiscal" and a.nivel == "nucleo"
    assert a.cod == (("escopo", "grupo"), ("mecanismo", "beneficio"), ("custo", "renuncia"), ("isonomia", "ramo"), ("controle", "neutro"))
    # Valor 0 por escolha editorial: investir, custo prisional, fonte dita e condição da pessoa.
    assert leitura.marcas(regua, _lida("infra setor sistema investe pessoal neutro")) == {"contas": 0, "isonomia": 0, "controle": 0}
    assert leitura.marcas(regua, _lida("fiscal geral ajuste arrecada iguala afrouxa")) == {"contas": 1, "isonomia": 1, "controle": -1}
    # A forma do ponto e o texto de cada valor vêm do YAML.
    contas = regua.marca("contas")
    assert [(v.id, v.valor, v.forma) for v in contas.valores] == [("poupa", 1, "cheio"), ("neutro", 0, "vazado"), ("onera", -1, "losango"), ("nd", None, "vazado")]
    assert contas.campo == "custo" and contas.de(None).rotulo == regua.nd


def test_os_alertas(regua):
    """Iniciativa: só com in = 1, origem parlamentar e PL ou PLP. Dúvida: confiança 1 com um
    leitor só."""
    marcou = _lida("estado grupo servico gasto profissao neutro", iniciativa=1, confianca=3)
    assert leitura.alertas(regua, marcou, tipo="PL", origem="parlamentar") == (leitura.INICIATIVA,)
    assert leitura.alertas(regua, marcou, tipo="PLP", origem="parlamentar") == (leitura.INICIATIVA,)
    assert leitura.alertas(regua, marcou, tipo="PEC", origem="parlamentar") == ()
    assert leitura.alertas(regua, marcou, tipo="PL", origem="executivo") == ()
    assert leitura.alertas(regua, marcou) == ()  # sem saber o tipo e a origem, não acende
    em_duvida = _lida("estado grupo servico gasto profissao neutro", confianca=1, duvida="a")
    assert leitura.alertas(regua, em_duvida) == (leitura.DUVIDA,)
    assert leitura.alertas(regua, leitura.Leitura(em_duvida.codigos, confianca=1, r=leitura.DUAS)) == ()
    assert set(regua.alertas) == {leitura.INICIATIVA, leitura.A_CONFIRMAR, leitura.DUVIDA}  # os três têm texto no YAML


def test_o_que_pede_segunda_leitura(regua):
    """Só a leitura de um leitor (r = 1): peso 60 ou mais, confiança 1, ou matéria votada."""
    comum = _lida("justica grupo pena prisional neutro neutro", confianca=3)  # peso 10
    assert leitura.pede_segunda(regua, comum) == ()
    assert leitura.pede_segunda(regua, comum, votada=True) == (leitura.VOTADA,)
    assert leitura.pede_segunda(regua, _lida("justica grupo pena prisional neutro neutro", confianca=1)) == (leitura.POUCA_CONFIANCA,)
    pesada = _lida("fiscal geral sistema nd neutro neutro", confianca=1)
    assert leitura.pede_segunda(regua, pesada, votada=True) == (leitura.PESO_ALTO, leitura.POUCA_CONFIANCA, leitura.VOTADA)
    assert leitura.pede_segunda(regua, leitura.Leitura(pesada.codigos, confianca=1, r=leitura.DUAS), votada=True) == ()
    # A votada que ficou sem leitura ("nd") com um leitor só também é relida.
    assert leitura.pede_segunda(regua, _lida("fiscal nd nd nd nd nd", confianca=3), votada=True) == (leitura.VOTADA,)


def test_conciliar_duas_e_tres_leituras(regua):
    """Mesma faixa: valem os códigos da primeira, r = 2. Faixas diferentes: sem a terceira,
    instável (r = 0); com a terceira, vale a faixa em que dois caíram (r = 3)."""
    pontual = _lida("justica grupo pena prisional neutro neutro", porque="primeira")  # 10
    outra_pontual = _lida("social grupo servico gasto neutro neutro", porque="segunda")  # 12
    media = _lida("produtividade grupo ajuste neutro neutro neutro", porque="media")  # 24
    minima = _lida("outro local simbolico neutro neutro neutro", porque="minima")  # 0
    assert leitura.conciliar(regua, pontual, outra_pontual) == leitura.Leitura(pontual.codigos, porque="primeira", r=leitura.DUAS)
    assert leitura.conciliar(regua, pontual, media).r == leitura.INSTAVEL
    assert leitura.conciliar(regua, pontual, media, outra_pontual) == leitura.Leitura(pontual.codigos, porque="primeira", r=leitura.TRES)
    assert leitura.conciliar(regua, pontual, media, media) == leitura.Leitura(media.codigos, porque="media", r=leitura.TRES)
    assert leitura.conciliar(regua, pontual, media, minima).r == leitura.INSTAVEL
    # Duas leituras que deixam sem leitura concordam: fica sem leitura, mas confirmada.
    nd = _lida("fiscal nd nd nd nd nd")
    assert leitura.conciliar(regua, nd, _lida("nd nd nd nd nd nd")).r == leitura.DUAS


def test_conciliar_na_materia_votada_a_marca_do_caixa_em_disputa_fica_nd(regua):
    """Na matéria votada, a leitura só confirma a marca do caixa que os leitores da faixa que
    vale disseram igual (a regra da leitura inicial): se divergem, o custo fica "nd", com a
    anotação, e a matéria sai do voto fiscal. Fora das votadas, valem os códigos do primeiro
    leitor da faixa, como sempre. As outras marcas e a alavanca são as dele nos dois casos."""
    gasta = _lida("fiscal geral ajuste gasto profissao afrouxa", porque="Cria despesa.")  # 60; caixa −1
    corta = _lida("fiscal geral ajuste corta neutro aperta", porque="Contém despesa.")  # 60; caixa +1
    renuncia = _lida("infra setor sistema renuncia neutro neutro", porque="Cria renúncia.")  # 60; outro código, a mesma marca −1
    sem_marca = _lida("fiscal geral ajuste nd neutro afrouxa", porque="Não dá para dizer.")  # 60; caixa "nd"
    media = _lida("produtividade grupo ajuste corta neutro neutro", porque="Média.")  # 24; caixa +1
    em_disputa = leitura.Leitura(("fiscal", "geral", "ajuste", "nd", "profissao", "afrouxa"), porque=f"Cria despesa. {leitura.DISPUTA_DE_CONTAS}", r=leitura.DUAS)
    assert leitura.conciliar(regua, gasta, corta) == leitura.Leitura(gasta.codigos, porque="Cria despesa.", r=leitura.DUAS)  # fora das votadas
    assert leitura.conciliar(regua, gasta, corta, votada=True) == em_disputa
    assert leitura.contas_em_disputa(em_disputa) and leitura.marcas(regua, em_disputa) == {"contas": None, "isonomia": -1, "controle": -1}
    # Códigos diferentes com a mesma marca: não há disputa.
    assert leitura.conciliar(regua, gasta, renuncia, votada=True) == leitura.Leitura(gasta.codigos, porque="Cria despesa.", r=leitura.DUAS)
    # Um leitor viu a marca e o outro não: disputa, também quando o "nd" é o do primeiro (a de controle não leva a matéria ao voto fiscal).
    assert leitura.conciliar(regua, sem_marca, corta, votada=True) == leitura.Leitura(sem_marca.codigos, porque=f"Não dá para dizer. {leitura.DISPUTA_DE_CONTAS}", r=leitura.DUAS)
    assert not leitura.contas_em_disputa(leitura.conciliar(regua, sem_marca, _lida("infra setor sistema nd neutro neutro"), votada=True))
    # Com a terceira: só contam os leitores que caíram na faixa que vale.
    assert leitura.conciliar(regua, gasta, media, corta, votada=True) == leitura.Leitura(em_disputa.codigos, porque=em_disputa.porque, r=leitura.TRES)
    assert leitura.conciliar(regua, media, gasta, renuncia, votada=True) == leitura.Leitura(gasta.codigos, porque="Cria despesa.", r=leitura.TRES)  # a marca da média (+1) não entra
    assert leitura.conciliar(regua, media, gasta, corta, votada=True) == leitura.Leitura(em_disputa.codigos, porque=em_disputa.porque, r=leitura.TRES)
    # Leitura instável não ganha anotação: já está fora de toda conta.
    assert leitura.conciliar(regua, gasta, media, _lida("outro local simbolico neutro neutro neutro"), votada=True) == leitura.Leitura(gasta.codigos, porque="Cria despesa.", r=leitura.INSTAVEL)
    # A justificativa comprida é cortada para a anotação caber inteira: a guarda corta o que passa do limite, e a anotação cortada deixaria de tirar a matéria do voto fiscal.
    comprida = _lida("fiscal geral ajuste gasto profissao afrouxa", porque=("Cria despesa obrigatória sem dizer a fonte. " * 5)[: leitura.PORQUE_MAX].rstrip())
    anotada = leitura.conciliar(regua, comprida, corta, votada=True)
    assert len(comprida.porque) > leitura.PORQUE_MAX - len(leitura.DISPUTA_DE_CONTAS) and len(anotada.porque) <= leitura.PORQUE_MAX
    assert anotada.porque.endswith(f" {leitura.DISPUTA_DE_CONTAS}") and anotada.porque.startswith("Cria despesa obrigatória sem dizer a fonte. Cria")
    assert leitura.de_linha(regua, anotada.linha()) == anotada and leitura.contas_em_disputa(leitura.de_linha(regua, anotada.linha()))
    assert leitura.com_contas_em_disputa(regua, anotada) == anotada  # anotar de novo não repete a anotação


# ── a régua no arquivo ───────────────────────────────────────────────────────


def test_a_regua_do_config_como_as_contas_a_leem(regua):
    assert regua.chaves == ("a", "e", "m", "ct", "is", "co")
    assert regua.alavanca.id == "alavanca" and len(regua.alavanca.codigos) == 10
    assert [c.id for c in regua.campos_de_peso] == ["escopo", "mecanismo"]
    assert [(c.id, c.marca) for c in regua.campos_de_marca] == [("custo", "contas"), ("isonomia", "isonomia"), ("controle", "controle")]
    assert regua.niveis == (("nucleo", 1.0, "núcleo"), ("entorno", 0.6, "entorno"), ("fora", 0.3, "fora do objetivo"))
    assert [(f.id, f.de, f.so_nivel) for f in regua.faixas] == [("minimo", 0, ""), ("pontual", 10, ""), ("medio", 20, ""), ("estruturante", 60, "nucleo")]
    fiscal = regua.campo("a").codigo("fiscal")
    assert (fiscal.nivel, fiscal.rotulo) == ("nucleo", "Contas públicas e tributos") and fiscal.definicao.startswith("regra fiscal")
    assert regua.campo("mecanismo").codigo("diretriz").peso == 0.15 and regua.campo("custo").codigo("corta").valor == 1
    assert regua.campo("e") is regua.campo("escopo") and regua.campo("nada") is None
    lim = regua.limiares
    assert (lim.perfil_minimo, lim.saldo_minimo, lim.voto_peso, lim.voto_fechado, lim.pessoas_minimo) == (10, 5, 20, 0.8, 50)
    assert (lim.longe_do_partido, lim.teto_pontos) == (20, 600)
    assert (regua.cruzar.zonas, regua.cruzar.permutacoes, regua.cruzar.reamostras, regua.cruzar.semente) == ((30, 70), 1000, 2000, 57)
    assert regua.segunda_leitura.peso_minimo == 60 and regua.alerta_iniciativa.tipos == ("PL", "PLP")
    assert regua.leitura.versao == 1 and regua.leitura.tipos == ("PL", "PLP", "PEC", "PLS", "PLC") and regua.leitura.tipos_votadas == ("MPV", "PLV")
    assert regua.leitura.estado(leitura.DUAS) == "lido na ementa, por dois leitores" and set(dict(regua.leitura.estados)) == set(leitura.ESTADOS)
    assert [(s.codigo, s.padrao, s.alternativa) for s in regua.sensibilidade] == [("arrecada", 1, 0), ("investe", 0, -1), ("prisional", 0, -1), ("vincula", -1, 0)]
    # De onde vem cada leitura, o selo da tela e o corte das candidatas à IA.
    assert regua.fontes == {leitura.REGRA: "lido por regras", leitura.IA: "lido por IA", leitura.IA2: "lido por IA, duas vezes"}
    assert regua.rotulo_da_fonte(leitura.IA2) == "lido por IA, duas vezes" and regua.selo == "Leitura por IA, com régua pública"
    assert regua.leitura.candidatas_peso == 60 and regua.peso_das_candidatas == 60


def test_sem_o_bloco_no_config_nao_ha_regua_e_nada_quebra(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    assert regras.de_dict({}).projeto_de_pais is None
    sem_bloco = fabrica.regras_de_teste()
    assert sem_bloco.projeto_de_pais is None
    # Sem régua, o universo é vazio e os comandos saem com erro, sem gravar nada.
    coletor = fontes.Coletor(tmp_path / "cache", fetch=fabrica.FetchFalso(), pausa=0.0)
    assert leitura.coletar(sem_bloco, tmp_path / "data" / "congresso", coletor, fabrica.HOJE, projetos_desde=2026) == {}
    monkeypatch.setattr(cli.regras_mod, "carregar", lambda _root: sem_bloco)
    entrada = tmp_path / "lidos.jsonl"
    entrada.write_text('{"casa":"sf","id":"PL-1-2026","a":"fiscal","e":"geral","m":"ajuste","ct":"nd","is":"nd","co":"nd","in":0,"cf":3,"du":"","pq":"x","r":1}\n', encoding="utf-8")
    assert main(["congresso", "leitura", "importar", "--dir", str(tmp_path), str(entrada)]) == 1
    assert main(["congresso", "leitura", "exportar", "--dir", str(tmp_path), "--cache", str(tmp_path / "cache"), "--saida", str(tmp_path / "saida")]) == 1
    assert not (tmp_path / "data").exists() and not (tmp_path / "saida").exists()
    # Quem faz as contas pede o leitor e recebe "não há": a visão não existe.
    assert leitura.abrir(ROOT, tmp_path / "data" / "congresso", sem_bloco) is None


def test_bloco_sem_fontes_selo_nem_corte_das_candidatas_tem_padrao_seguro():
    pequena = _regua_pequena()
    assert pequena.fontes == {} and pequena.rotulo_da_fonte(leitura.REGRA) == leitura.REGRA and pequena.selo == ""
    assert pequena.leitura.candidatas_peso is None and pequena.peso_das_candidatas == pequena.segunda_leitura.peso_minimo
    assert _regua_pequena(leitura={"candidatas_peso": 36}).peso_das_candidatas == 36


@pytest.mark.parametrize(
    ("troca", "erro"),
    [
        ({"arredonda": "para_baixo"}, "arredonda"),
        ({"faixas": [{"id": "media", "de": 10}]}, "faixas"),
        ({"nivel": {}}, "nivel"),
        ({"campos": {"onde": {"chave": "a", "codigos": [{"id": "casa", "nivel": "sotao"}]}}}, "nível desconhecido"),
        ({"campos": {"onde": {"chave": "a", "codigos": [{"id": "nd", "nivel": "dentro"}]}}}, "reservado"),
    ],
)
def test_bloco_fora_do_formato_e_erro_na_leitura_do_arquivo(troca: dict, erro: str):
    """Como nas outras réguas do arquivo: valor inválido é erro ao ler, não régua pela metade."""
    with pytest.raises(ValueError, match=erro):
        _regua_pequena(**troca)


# ── as instruções ao leitor ──────────────────────────────────────────────────


def test_as_instrucoes_ao_leitor_citam_exatamente_os_codigos_do_yaml(regua):
    """Cada campo das instruções lista os códigos do YAML, na mesma ordem, e o "nd"."""
    texto = (ROOT / "config" / leitura.INSTRUCOES).read_text(encoding="utf-8")
    citados = leitura.codigos_das_instrucoes(texto, regua.chaves)
    assert citados == {campo.chave: [*campo.ids, regras.ND] for campo in regua.campos}
    assert sum(len(v) for v in citados.values()) == 41 + 6  # 10 + 4 + 8 + 10 + 6 + 3 códigos, e um "nd" em cada campo
    # A linha de exemplo das instruções passa na validação.
    exemplo = json.loads(next(linha for linha in texto.splitlines() if linha.startswith('{"i":')))
    lida, motivo = leitura.validar(regua, exemplo)
    assert motivo == "" and lida == leitura.Leitura(
        ("fiscal", "grupo", "beneficio", "gasto", "profissao", "neutro"), 1, 3, "", "Aposentadoria especial para uma categoria: despesa permanente; fonte não aparece na ementa.", leitura.UMA
    )


def test_os_exemplos_resolvidos_das_instrucoes_usam_so_os_codigos_do_yaml(regua):
    """As instruções em uso trazem os "erros comuns" e 50 exemplos resolvidos (o bloco cego e,
    logo abaixo, a saída certa): cada saída passa na validação, com o id do seu bloco, e a
    lista de códigos de cada campo continua sendo lida só da seção do campo."""
    linhas = (ROOT / "config" / leitura.INSTRUCOES).read_text(encoding="utf-8").splitlines()
    assert "ERROS COMUNS (leia antes de começar)" in linhas
    comeco = linhas.index("EXEMPLOS RESOLVIDOS (entrada e a saída certa; siga o mesmo raciocínio)")
    assert comeco > linhas.index("SAÍDA")  # os exemplos vêm depois das regras e do formato da saída
    blocos = [linha.split("]")[0][1:] for linha in linhas[comeco:] if linha.startswith("[ex")]
    saidas = [json.loads(linha) for linha in linhas[comeco:] if linha.startswith('{"i":')]
    assert blocos == [s["i"] for s in saidas] == [f"ex{n:02d}" for n in range(1, 51)]
    for saida in saidas:
        lida, motivo = leitura.validar(regua, saida)
        assert lida is not None, f"{saida['i']}: {motivo}"
        assert lida.r == leitura.UMA and 0 < len(lida.porque) <= 120
        assert (lida.confianca == 3) == (lida.duvida == "")  # com cf 1 ou 2, "du" diz o campo
    # Nenhuma linha dos exemplos é lida como lista de códigos de um campo.
    citados = leitura.codigos_das_instrucoes("\n".join(linhas), regua.chaves)
    assert citados == leitura.codigos_das_instrucoes("\n".join(linhas[:comeco]), regua.chaves)


def test_codigos_das_instrucoes_le_so_a_lista_de_cada_campo():
    texto = "Regras\n\na = alavanca (onde mexe)\n  casa = dentro de casa,\n    com continuação\n  rua = na rua\n  nd\n  Desempate: casa = rua não conta.\n  outra = linha depois do desempate\n\nin = alerta\n  fora = não é campo\n"
    assert leitura.codigos_das_instrucoes(texto, ["a", "e"]) == {"a": ["casa", "rua", "nd"]}


# ── validar ──────────────────────────────────────────────────────────────────


def _registro(**troca) -> dict:
    return {"a": "fiscal", "e": "grupo", "m": "beneficio", "ct": "gasto", "is": "profissao", "co": "neutro", "in": 1, "cf": 3, "du": "", "pq": "Porquê.", "r": 1, **troca}


def test_validar_aceita_os_codigos_do_yaml_e_recusa_o_resto(regua):
    lida, motivo = leitura.validar(regua, _registro(du="e, x ,m", pq="  Dois   espaços.  "))
    assert motivo == "" and lida == leitura.Leitura(("fiscal", "grupo", "beneficio", "gasto", "profissao", "neutro"), 1, 3, "e,m", "Dois espaços.", 1)
    assert leitura.validar(regua, _registro(a="nd", e="nd", m="nd", ct="nd", co="nd", **{"is": "nd"}))[0] is not None  # todo campo aceita "nd"
    sem_r = {k: v for k, v in _registro().items() if k != "r"}
    assert leitura.validar(regua, sem_r)[0].r == leitura.UMA  # sem "r", é uma leitura
    assert len(leitura.validar(regua, _registro(pq="palavra " * 40))[0].porque) <= leitura.PORQUE_MAX + 1
    for troca, trecho in [
        ({"a": "fiscall"}, '"a"'),
        ({"m": "geral"}, '"m"'),  # código de outro campo
        ({"ct": None}, '"ct"'),
        ({"co": 1}, '"co"'),
        ({"in": 2}, '"in"'),
        ({"cf": 0}, '"cf"'),
        ({"cf": "alta"}, '"cf"'),
        ({"r": 4}, '"r"'),
    ]:
        lida, motivo = leitura.validar(regua, _registro(**troca))
        assert lida is None and trecho in motivo, troca


# ── a guarda ─────────────────────────────────────────────────────────────────


def test_a_chave_de_cada_proposicao():
    assert leitura.chave(projetos.Proposicao(id=2491234, casa="cd", sigla="PL", numero=123, ano=2025)) == "2491234"
    assert leitura.chave(projetos.Proposicao(id=160001, casa="sf", sigla="PL", numero=123, ano=2025)) == "PL-123-2025"
    assert leitura.chave_da_materia("cd", "PL 123/2025", 2491234) == "2491234" and leitura.chave_da_materia("cd", "PL 123/2025", None) == ""
    assert leitura.chave_da_materia("sf", "PEC 66/2023 (fase 2)", 7001) == "PEC-66-2023" and leitura.chave_da_materia("sf", "REQ 5", 1) == ""
    assert leitura.ano_da_chave("sf", "PEC-66-2023") == 2023 and leitura.ano_da_chave("cd", "2491234") is None
    assert leitura.chave_valida("cd", "2491234") and leitura.chave_valida("sf", "PL-1-2026")
    assert not leitura.chave_valida("cd", "PL-1-2026") and not leitura.chave_valida("sf", "2491234") and not leitura.chave_valida("xx", "1")


def test_a_guarda_grava_ordenado_uma_proposicao_por_linha_e_rele_igual(tmp_path: Path, regua):
    um = _lida("fiscal grupo beneficio gasto profissao neutro", iniciativa=1, confianca=3, porque="Aposentadoria especial para uma categoria.")
    dois = _lida("outro local simbolico neutro neutro neutro", confianca=3, porque='Título de "capital" a um município.', r=leitura.DUAS)
    guarda = leitura.Guarda(regua)
    # Fora de ordem de propósito; "999" vem antes de "1000" (números, não texto).
    assert guarda.registrar("cd", 2025, "1000", dois) == leitura.NOVA
    assert guarda.registrar("cd", 2025, "999", um) == leitura.NOVA
    assert guarda.registrar("sf", 2023, "PL-10-2023", um) == leitura.NOVA
    assert guarda.registrar("sf", 2023, "PL-9-2023", dois) == leitura.NOVA
    assert guarda.registrar("cd", 2024, "5", um) == leitura.NOVA
    assert guarda.registrar("cd", 2024, "5", um) == leitura.IGUAL and guarda.registrar("cd", 2024, "5", dois) == leitura.TROCADA
    pasta = tmp_path / "leitura"
    gravados = guarda.gravar(pasta)
    assert [c.name for c in gravados] == ["cd-2024.json", "cd-2025.json", "sf-2023.json"]
    assert (pasta / "cd-2025.json").read_text(encoding="utf-8") == (
        '{"v":1,"regua":1,"casa":"cd","ano":2025,"itens":{\n'
        '"999":["fiscal","grupo","beneficio","gasto","profissao","neutro",1,3,"","Aposentadoria especial para uma categoria.",1],\n'
        '"1000":["outro","local","simbolico","neutro","neutro","neutro",0,3,"","Título de \\"capital\\" a um município.",2]\n'
        "}}\n"
    )
    assert (pasta / "sf-2023.json").read_text(encoding="utf-8").splitlines()[1].startswith('"PL-9-2023":')
    json.loads((pasta / "sf-2023.json").read_text(encoding="utf-8"))  # o arquivo inteiro é JSON
    # Relê igual; gravar de novo não regrava nada.
    relida = leitura.Guarda.ler(pasta, regua)
    assert len(relida) == 5 and relida.ignorados == [] and relida.invalidas == 0
    assert relida.de("cd", "999") == um and relida.de("cd", "1000") == dois and relida.de("sf", "PL-9-2023") == dois and relida.de("cd", "5") == dois
    assert relida.de("cd", "123") is None and relida.ano_de("cd", "999") == 2025
    assert relida.da(projetos.Proposicao(id=999, casa="cd", sigla="PL", numero=1, ano=2025)) == um
    assert set(relida.da_casa("sf")) == {"PL-9-2023", "PL-10-2023"}
    assert relida.gravar(pasta) == []
    # Pasta que não existe: guarda vazia.
    assert len(leitura.Guarda.ler(tmp_path / "nada", regua)) == 0


def test_quando_as_instrucoes_mudam_de_versao_as_leituras_antigas_deixam_de_valer(tmp_path: Path):
    """``regua`` no arquivo é a versão das instruções: outra versão, o arquivo fica de fora (e
    é substituído quando chega leitura nova do mesmo ano). Mudar peso ou corte não muda a versão."""
    v1, v2 = _regua_pequena(leitura={"versao": 1}), _regua_pequena(leitura={"versao": 2})
    pasta = tmp_path / "leitura"
    antiga = leitura.Guarda(v1)
    antiga.registrar("cd", 2025, "1", _lida("casa todos muda nada"))
    antiga.registrar("cd", 2024, "2", _lida("casa todos muda nada"))
    antiga.gravar(pasta)
    assert len(leitura.Guarda.ler(pasta, v1)) == 2
    nova = leitura.Guarda.ler(pasta, v2)
    assert len(nova) == 0 and nova.ignorados == ["cd-2024.json", "cd-2025.json"]
    nova.registrar("cd", 2025, "7", _lida("rua um quase gasta"))
    assert [c.name for c in nova.gravar(pasta)] == ["cd-2025.json"]
    depois = leitura.Guarda.ler(pasta, v2)
    assert set(depois.da_casa("cd")) == {"7"} and depois.ignorados == ["cd-2024.json"]
    # Linha com código que a régua não tem mais é contada e fica de fora.
    (pasta / "cd-2023.json").write_text('{"v":1,"regua":2,"casa":"cd","ano":2023,"itens":{\n"3":["sotao","todos","muda","nada",0,3,"","x",1],\n"4":["casa","todos","muda","nada",0,3,"","x",1]\n}}\n', encoding="utf-8")
    com_invalida = leitura.Guarda.ler(pasta, v2)
    assert com_invalida.invalidas == 1 and com_invalida.de("cd", "4") is not None and com_invalida.de("cd", "3") is None


# ── importar ─────────────────────────────────────────────────────────────────


def _jsonl(caminho: Path, *registros: dict | str) -> Path:
    caminho.write_text("".join((r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)) + "\n" for r in registros), encoding="utf-8")
    return caminho


def test_importar_valida_recusa_substitui_e_e_idempotente(tmp_path: Path, regua):
    pasta = tmp_path / "data" / "congresso" / "leitura"
    entrada = _jsonl(
        tmp_path / "lidos.jsonl",
        {"casa": "cd", "id": "2491234", "ano": 2025, **_registro()},
        {"casa": "cd", "id": 2491300, "t": "PL 9/2025", **_registro(a="outro", e="local", m="simbolico", ct="neutro", **{"is": "neutro", "in": 0})},
        {"casa": "sf", "id": "PL-123-2023", **_registro(r=2)},
        {"casa": "cd", "id": "77", "ano": 2025, **_registro(a="fiscall")},  # código inválido
        {"casa": "cd", "id": "78", "ano": 2025, **_registro(cf=9)},  # confiança inválida
        {"casa": "sf", "id": "123", **_registro()},  # no Senado, a chave é SIGLA-NUMERO-ANO
        {"casa": "cd", "id": "79", **_registro()},  # Câmara sem o ano da proposição
        "isto não é JSON",
        "",
    )
    resultado = leitura.importar(pasta, regua, [entrada])
    assert (resultado.linhas, resultado.aceitas, resultado.recusadas) == (8, 3, 5)
    assert (resultado.novas, resultado.trocadas, resultado.iguais) == (3, 0, 0)
    assert sorted(c.name for c in resultado.gravados) == ["cd-2025.json", "sf-2023.json"]
    assert sum(resultado.motivos.values()) == 5 and resultado.motivos['código inválido em "a"'] == 1
    assert resultado.exemplos[0].startswith("lidos.jsonl:4: código inválido")
    guarda = leitura.Guarda.ler(pasta, regua)
    assert set(guarda.da_casa("cd")) == {"2491234", "2491300"} and set(guarda.da_casa("sf")) == {"PL-123-2023"}
    assert guarda.de("sf", "PL-123-2023").r == 2 and guarda.de("cd", "77") is None
    # De novo, igual: nada muda no disco.
    antes = {c.name: c.read_text(encoding="utf-8") for c in pasta.iterdir()}
    repetida = leitura.importar(pasta, regua, [entrada])
    assert (repetida.aceitas, repetida.novas, repetida.trocadas, repetida.iguais, repetida.gravados) == (3, 0, 0, 3, [])
    assert {c.name: c.read_text(encoding="utf-8") for c in pasta.iterdir()} == antes
    # A linha nova substitui a antiga da mesma proposição (o ano já está na guarda) e as outras ficam.
    troca = _jsonl(tmp_path / "segunda.jsonl", {"casa": "cd", "id": "2491234", **_registro(r=2, pq="Confirmada.")}, {"casa": "cd", "id": "2491400", "ano": 2026, **_registro()})
    trocada = leitura.importar(pasta, regua, [troca])
    assert (trocada.novas, trocada.trocadas, trocada.iguais) == (1, 1, 0)
    assert sorted(c.name for c in trocada.gravados) == ["cd-2025.json", "cd-2026.json"]
    guarda = leitura.Guarda.ler(pasta, regua)
    assert guarda.de("cd", "2491234").r == 2 and guarda.de("cd", "2491234").porque == "Confirmada." and len(guarda) == 4
    assert (pasta / "cd-2025.json").read_text(encoding="utf-8").count("\n") == 4  # cabeça, duas proposições e o fecho


def test_importar_acha_o_ano_da_camara_no_mapa_do_lote(tmp_path: Path, regua):
    """A linha da Câmara não traz o ano da proposição; o mapa do lote ([id opaco, casa, id,
    título]) traz, no título. No mesmo arquivo de entrada, vale a última linha da proposição."""
    lotes = tmp_path / "lotes"
    lotes.mkdir()
    (lotes / "lote-0001.map.json").write_text(json.dumps([["p0001", "cd", 2491234, "PL 5856/2026"], ["p0002", "sf", "PL-88-2023", "PL 88/2023"]]), encoding="utf-8")
    assert leitura.anos_dos_mapas([lotes]) == leitura.anos_dos_mapas([tmp_path]) == {("cd", "2491234"): 2026, ("sf", "PL-88-2023"): 2023}
    entrada = _jsonl(tmp_path / "lidos.jsonl", {"casa": "cd", "id": "2491234", **_registro(pq="Primeira.")}, {"casa": "cd", "id": "2491234", **_registro(pq="Última.")})
    pasta = tmp_path / "leitura"
    sem_mapa = leitura.importar(pasta, regua, [entrada])
    assert (sem_mapa.aceitas, sem_mapa.recusadas, sem_mapa.gravados) == (0, 2, []) and not pasta.exists()
    com_mapa = leitura.importar(pasta, regua, [entrada], anos=leitura.anos_dos_mapas([lotes]))
    assert (com_mapa.aceitas, com_mapa.novas, com_mapa.recusadas) == (2, 1, 0) and [c.name for c in com_mapa.gravados] == ["cd-2026.json"]
    assert leitura.Guarda.ler(pasta, regua).de("cd", "2491234").porque == "Última."


def test_o_comando_importar(tmp_path: Path, regua):
    """``congresso leitura importar --dir DIR arquivo.jsonl``: grava em DIR/data/congresso/leitura/;
    os mapas ao lado do arquivo dão o ano da Câmara; só linha recusada é erro."""
    (tmp_path / "lote-0001.map.json").write_text(json.dumps([["p0001", "cd", 2491234, "PL 5856/2026"]]), encoding="utf-8")
    entrada = _jsonl(tmp_path / "lidos.jsonl", {"casa": "cd", "id": "2491234", **_registro()}, {"casa": "sf", "id": "PEC-66-2023", **_registro()}, {"casa": "cd", "id": "5", **_registro(e="tudo")})
    publicado = tmp_path / "site"
    assert main(["congresso", "leitura", "importar", "--dir", str(publicado), str(entrada)]) == 0
    pasta = publicado / "data" / "congresso" / "leitura"
    assert sorted(c.name for c in pasta.iterdir()) == ["cd-2026.json", "sf-2023.json"]
    assert len(leitura.Guarda.ler(pasta, regua)) == 2
    so_recusas = _jsonl(tmp_path / "ruim.jsonl", {"casa": "cd", "id": "5", "ano": 2025, **_registro(e="tudo")})
    assert main(["congresso", "leitura", "importar", "--dir", str(publicado), str(so_recusas)]) == 1
    assert main(["congresso", "leitura", "importar", "--dir", str(publicado), str(tmp_path / "nao-existe.jsonl")]) == 1


# ── o universo e os lotes ────────────────────────────────────────────────────


def _prop(pid: int, sigla: str, numero: int, ano: int, *, casa: str = "cd", estado: str = projetos.TRAMITANDO, primeiro: int | None = None, apresentada: str = "", **extra) -> projetos.Proposicao:
    return projetos.Proposicao(
        id=pid, casa=casa, sigla=sigla, numero=numero, ano=ano, estado=estado, primeiro=primeiro, apresentada=apresentada or f"{ano}-03-01", ementa=extra.pop("ementa", f"Ementa do {sigla} {numero}."), **extra
    )


def _votos(casa: str, votacoes: list[tuple[str, str, str, int | None, str]], **extra) -> base.AnoVotos:
    """Cada votação: (matéria, tipo, órgão, id da proposição, ementa do registro)."""
    return base.AnoVotos(
        casa=casa,
        ano=2026,
        pessoas=[base.Pessoa(1, "Fulano", "SP", [(0, "PT")])],
        votacoes=[
            base.Votacao(id=f"v{k}", data=f"2026-03-{k + 1:02d}", orgao=orgao, descricao="Aprovado o Projeto.", tipo=tipo, votos="S", prop=prop, prop_id=pid, ementa=ementa, **(extra if k == 0 else {}))
            for k, (prop, tipo, orgao, pid, ementa) in enumerate(votacoes)
        ],
    )


def test_o_universo_da_camara(regua):
    """Legislatura desde 01/02/2023. Entram: o PL 1 (de deputado, da legislatura, já
    arquivado: perfil), o PL 2 (de 2020, tramitando: espera), o PLP 3 (os dois), a MPV 6 e a
    PEC 9 (votadas no texto principal: voto; a PEC não está na lista e sai com a ementa do
    registro da votação). Ficam fora: a apensada, o PDL, o PL do Executivo já encerrado, a
    votada só em destaque, a de comissão e a de requerimento."""
    props = [
        _prop(1, "PL", 1, 2026, estado=projetos.ENCERRADA, primeiro=11),
        _prop(2, "PL", 2, 2020, primeiro=12, inteira="Ementa inteira do PL 2, bem mais longa.", explicacao="Explica o PL 2.", chaves="Alteração, Código Penal, pena.", temas=["seguranca", "politica", "economia"]),
        _prop(3, "PLP", 3, 2026, primeiro=11),
        _prop(4, "PL", 4, 2026, estado=projetos.APENSADA),
        _prop(5, "PDL", 5, 2026, primeiro=11),
        _prop(6, "MPV", 6, 2026, estado=projetos.NORMA),
        _prop(7, "PL", 7, 2026, estado=projetos.ENCERRADA),
        _prop(8, "PL", 8, 2022, estado=projetos.ENCERRADA, primeiro=11),  # de deputado, mas de antes da legislatura e já encerrado
    ]
    votos = _votos(
        "cd",
        [
            ("MPV 6/2026", base.MERITO, base.PLENARIO, 6, "Ementa da MPV no registro."),
            ("PL 1/2026", base.DESTAQUE, base.PLENARIO, 1, ""),
            ("PL 7/2026", base.MERITO, "CCJC", 7, ""),
            ("PEC 9/2015", base.MERITO, base.PLENARIO, 99, "Altera o sistema tributário."),
            ("REQ 5/2026", base.MERITO, base.PLENARIO, 55, ""),
            ("PL 4/2026", base.URGENCIA, base.PLENARIO, 4, ""),
            ("PL 20/2026", base.MERITO, base.PLENARIO, None, "Sem id no registro."),
        ],
    )
    itens = leitura.universo(regua, "cd", tramitam=props, autorais=props, anos=[votos], inicio="2023-02-01")
    assert {k: sorted(i.universos) for k, i in itens.items()} == {"1": ["perfil"], "2": ["espera"], "3": ["espera", "perfil"], "6": ["voto"], "99": ["voto"]}
    dois = itens["2"]
    assert (dois.casa, dois.titulo, dois.tipo, dois.ano) == ("cd", "PL 2/2020", "PL", 2020)
    assert (dois.ementa, dois.explicacao, dois.palavras, dois.temas) == ("Ementa inteira do PL 2, bem mais longa.", "Explica o PL 2.", "Alteração, Código Penal, pena", ("seguranca", "politica"))
    pec = itens["99"]
    assert (pec.titulo, pec.tipo, pec.ano, pec.ementa) == ("PEC 9/2015", "PEC", 2015, "Altera o sistema tributário.")
    assert itens["6"].ementa == "Ementa do MPV 6."  # a votada que está na lista sai com o registro da proposição
    assert leitura.contar(itens.values()) == {"cd": {"total": 5, "perfil": 2, "espera": 2, "voto": 2, "sem_ementa": 0}}
    # Com a ficha da matéria (materias-cd.json), a votada fora da lista ganha o texto da ficha.
    com_ficha = leitura.universo(regua, "cd", anos=[votos], fichas={"PEC 9/2015": {"e": "Ementa inteira da PEC.", "ex": "Explicação.", "kw": ["Reforma", "Tributo"], "tm": ["economia"]}})
    assert (com_ficha["99"].ementa, com_ficha["99"].explicacao, com_ficha["99"].palavras, com_ficha["99"].temas) == ("Ementa inteira da PEC.", "Explicação.", "Reforma, Tributo", ("economia",))


def test_a_votacao_do_texto_principal_e_as_que_nao_sao():
    """Do texto principal: a que a descrição nomeia (substitutivo, projeto, PEC em cada turno),
    a que não descreve nada e a que repete a ementa (registro antigo do Senado). Não são: a
    redação final, a mudança feita pela outra Casa (na descrição ou no título) e, no registro
    que não diz o que foi votado, a que descreve outra coisa (um destaque)."""

    def v(descricao: str, prop: str = "PEC 6/2019", ementa: str = "Modifica o sistema de previdência social e estabelece regras de transição.", **extra) -> base.Votacao:
        return base.Votacao(id="v", data="2021-03-04", orgao=base.PLENARIO, descricao=descricao, tipo=base.MERITO, votos="S", prop=prop, prop_id=1, ementa=ementa, **extra)

    do_texto = [
        "Aprovada a Subemenda Substitutiva Global ao Projeto de Lei nº 1, de 2025, ressalvados os destaques.",
        "Aprovada, em segundo turno, a Proposta de Emenda à Constituição nº 6, de 2019.",
        "Votação nominal do Projeto de Lei Complementar nº 73, de 2025, nos termos dos pareceres.",
        "Votação nominal.",
        "Votação nominal, em 1º turno, ressalvado o destaque.",
        "(Modifica o sistema de previdência social e estabelece regras de transição e disposições transitórias.)",
    ]
    outra_coisa = [
        "Aprovada a Redação Final assinada pelo relator.",
        "Rejeitada a Emenda do Senado Federal nº 1. Sim: 170; não: 262; total: 432.",
        "Aprovadas as Emendas do Senado Federal ao Projeto de Lei de Conversão nº 3, de 2021.",
        "Aprovado o Substitutivo do Senado Federal ao Projeto de Lei nº 1.023-D, de 2011.",
        "Votação nominal do Substitutivo da Câmara dos Deputados ao PLP nº 22/2025, nos termos do parecer.",
        "Idade mínima para mulheres.",
        "(Até o limite de quarenta e quatro bilhões de reais.)",
    ]
    assert [d for d in do_texto if not leitura.do_texto_principal(v(d))] == []
    assert [d for d in outra_coisa if leitura.do_texto_principal(v(d))] == []
    assert not leitura.do_texto_principal(v("Votação nominal.", prop="PL 4554/2020 (Substitutivo-CD)"))  # o Senado anota no título
    assert leitura.do_texto_principal(v("Votação nominal.", prop="PEC 66/2023 (fase 2)"))  # a fase não muda a matéria
    assert not leitura.do_texto_principal(v("Votação nominal.", secreta=True))
    assert not leitura.do_texto_principal(base.Votacao(id="v", data="2021-03-04", orgao="CCJC", descricao="Aprovado o Projeto.", tipo=base.MERITO, votos="S", prop="PL 1/2021"))
    assert not leitura.do_texto_principal(base.Votacao(id="v", data="2021-03-04", orgao=base.PLENARIO, descricao="Aprovado o Projeto.", tipo=base.DESTAQUE, votos="S", prop="PL 1/2021"))


def test_o_universo_do_senado(regua):
    """No Senado a chave é SIGLA-NUMERO-ANO. Entram: o que tramita (o PLS antigo também), o
    de autoria principal de senador na legislatura e a matéria votada (a fase ao lado do
    número não muda a matéria). A coautoria e a votação secreta ficam fora."""
    tramitam = [_prop(900, "PLS", 10, 2015, casa="sf"), _prop(901, "PL", 11, 2026, casa="sf", primeiro=None), _prop(902, "PRS", 3, 2026, casa="sf")]
    autorais = [
        _prop(901, "PL", 11, 2026, casa="sf", primeiro=101),
        _prop(903, "PL", 12, 2026, casa="sf", primeiro=None),  # assina junto, não primeiro
        _prop(904, "PEC", 2, 2024, casa="sf", primeiro=102, estado=projetos.APROVADA),
        _prop(905, "PL", 13, 2021, casa="sf", primeiro=102, estado=projetos.NORMA),  # de antes da legislatura
    ]
    votos = _votos("sf", [("PEC 66/2023 (fase 2)", base.MERITO, base.PLENARIO, 7001, "Limita o pagamento de precatórios."), ("PL 12/2026", base.MERITO, base.PLENARIO, 7002, "")])
    secreta = _votos("sf", [("PL 13/2021", base.MERITO, base.PLENARIO, 7003, "")], secreta=True)
    itens = leitura.universo(regua, "sf", tramitam=tramitam, autorais=autorais, anos=[votos, secreta], inicio="2023-02-01")
    assert {k: sorted(i.universos) for k, i in itens.items()} == {
        "PL-11-2026": ["espera", "perfil"],
        "PEC-2-2024": ["perfil"],
        "PLS-10-2015": ["espera"],
        "PEC-66-2023": ["voto"],
        "PL-12-2026": ["voto"],
    }
    assert itens["PEC-66-2023"].ementa == "Limita o pagamento de precatórios." and itens["PEC-66-2023"].titulo == "PEC 66/2023"
    assert itens["PL-12-2026"].ementa == "Ementa do PL 12."  # está entre as de autoria: vale o registro da proposição


def test_o_que_falta_ler_o_que_pede_segunda_e_os_lotes_cegos(tmp_path: Path, regua):
    props = [
        _prop(1, "PL", 1, 2026, primeiro=11, ementa="Aumenta a pena do furto."),
        _prop(2, "PLP", 2, 2026, primeiro=12, ementa="Limita a despesa primária.", explicacao="Institui limite para a despesa.", chaves="Regime fiscal, limite, despesa.", temas=["economia"]),
        _prop(3, "PL", 3, 2026, primeiro=11, ementa="Denomina rodovia."),
        _prop(4, "PL", 4, 2026, primeiro=11, ementa=""),  # sem ementa no registro: não há o que ler
        _prop(5, "MPV", 5, 2026, estado=projetos.NORMA, ementa="Abre crédito extraordinário."),
    ]
    votos = _votos("cd", [("MPV 5/2026", base.MERITO, base.PLENARIO, 5, ""), ("PL 3/2026", base.MERITO, base.PLENARIO, 3, "")])
    itens = leitura.universo(regua, "cd", tramitam=props, autorais=props, anos=[votos], inicio="2023-02-01")
    assert sorted(itens) == ["1", "2", "3", "4", "5"]
    guarda = leitura.Guarda(regua)
    assert [i.chave for i in leitura.faltam(itens, guarda)] == ["1", "2", "3", "4", "5"]
    guarda.registrar("cd", 2026, "1", _lida("justica grupo pena prisional neutro neutro", confianca=3))  # peso 10, um leitor: não pede segunda
    guarda.registrar("cd", 2026, "2", _lida("fiscal geral sistema corta neutro aperta", confianca=3))  # peso 100, um leitor
    guarda.registrar("cd", 2026, "3", _lida("outro local simbolico neutro neutro neutro", confianca=3))  # votada, um leitor
    assert [i.chave for i in leitura.faltam(itens, guarda)] == ["4", "5"]
    assert [(i.chave, motivos) for i, motivos in leitura.para_segunda(regua, itens, guarda)] == [("2", (leitura.PESO_ALTO,)), ("3", (leitura.VOTADA,))]
    guarda.registrar("cd", 2026, "2", _lida("fiscal geral sistema corta neutro aperta", confianca=3, r=leitura.DUAS))
    assert [i.chave for i, _m in leitura.para_segunda(regua, itens, guarda)] == ["3"]

    # Os lotes: o texto que o leitor recebe não traz número, autor, partido nem origem.
    saida = tmp_path / "saida"
    saida.mkdir()
    (saida / "lote-0009.txt").write_text("sobra de uma exportação anterior", encoding="utf-8")
    (saida / "anotacoes.txt").write_text("não é lote", encoding="utf-8")
    nomes = {"economia": "Economia, tributos e orçamento"}
    todos = [itens[k] for k in ("1", "2", "3", "4", "5")]
    assert leitura.gravar_lotes(saida, todos, nomes, tamanho=3) == 2  # quatro com ementa, de três em três
    assert sorted(c.name for c in saida.iterdir()) == ["anotacoes.txt", "lote-0001.map.json", "lote-0001.txt", "lote-0002.map.json", "lote-0002.txt"]
    mapas = [json.loads((saida / f"lote-000{n}.map.json").read_text(encoding="utf-8")) for n in (1, 2)]
    assert [len(m) for m in mapas] == [3, 1] and [linha[0] for linha in mapas[0]] == ["p0001", "p0002", "p0003"] and mapas[1][0][0] == "p0001"
    assert sorted(linha[1:] for m in mapas for linha in m) == [["cd", 1, "PL 1/2026"], ["cd", 2, "PLP 2/2026"], ["cd", 3, "PL 3/2026"], ["cd", 5, "MPV 5/2026"]]
    texto = (saida / "lote-0001.txt").read_text(encoding="utf-8") + (saida / "lote-0002.txt").read_text(encoding="utf-8")
    assert "2026" not in texto and "Fulano" not in texto and "parlamentar" not in texto
    blocos = (saida / "lote-0001.txt").read_text(encoding="utf-8").rstrip("\n").split("\n\n")
    assert len(blocos) == 3 and all(b.startswith(f"[p000{k}] ") for k, b in enumerate(blocos, 1))
    assert leitura.bloco("p0007", itens["2"], nomes) == "[p0007] PLP\nE: Limita a despesa primária.\nX: Institui limite para a despesa.\nK: Regime fiscal, limite, despesa\nT: Economia, tributos e orçamento"
    assert leitura.bloco("p0008", itens["3"], nomes) == "[p0008] PL\nE: Denomina rodovia."
    # A mesma lista sai sempre na mesma ordem; a exportação seguinte apaga os lotes de antes.
    assert [i.chave for i in leitura.embaralhar(todos)] == [i.chave for i in leitura.embaralhar(reversed(todos))]
    assert leitura.gravar_lotes(saida, [itens["1"]], nomes, nome="segunda", prefixo="s") == 1
    assert json.loads((saida / "segunda-0001.map.json").read_text(encoding="utf-8")) == [["s0001", "cd", 1, "PL 1/2026"]]
    # Em levas: a leva 0 sai primeiro, e dentro de cada leva vale o sorteio.
    assert leitura.gravar_lotes(saida, todos, nomes, tamanho=2, nome="segunda", prefixo="s", leva=lambda i: int(i.chave not in ("3", "5"))) == 2
    primeiro, segundo = (json.loads((saida / f"segunda-000{n}.map.json").read_text(encoding="utf-8")) for n in (1, 2))
    assert sorted(linha[2] for linha in primeiro) == [3, 5] and sorted(linha[2] for linha in segundo) == [1, 2]
    assert leitura.gravar_lotes(saida, [itens["1"]], nomes, nome="segunda", prefixo="s") == 1
    assert leitura.gravar_lotes(saida, [], nomes) == 0
    assert sorted(c.name for c in saida.iterdir()) == ["anotacoes.txt", "segunda-0001.map.json", "segunda-0001.txt"]


# ── as duas fontes: regras e IA ──────────────────────────────────────────────


def _item(chave: str = "1", *, casa: str = "cd", tipo: str = "PL", ementa: str = "Ementa.", universos: tuple[str, ...] = ()) -> leitura.Item:
    return leitura.Item(casa=casa, chave=chave, titulo=f"{tipo} {chave}/2026", tipo=tipo, ano=2026, ementa=ementa, universos=set(universos))


def _por_regra(codigos: str, **campos) -> leitura.Leitura:
    return leitura.Leitura(codigos=tuple(codigos.split()), fonte=leitura.REGRA, **campos)


def test_peso_60_de_fonte_regra_nao_conta_como_estruturante_e_leva_o_alerta(regua):
    """Só duas leituras de IA confirmam a faixa do topo. O leitor de regras que põe o projeto
    em 60 no núcleo o deixa em médio alcance, com o alerta, diga o "r" que disser."""
    codigos = "fiscal geral ajuste corta iguala aperta"  # 100 × 1,0 × 1,0 × 0,6 = 60, no núcleo
    for r in leitura.ESTADOS:
        regra = leitura.avaliar(regua, _por_regra(codigos, r=r, confianca=3))
        assert regra.lida and (regra.peso, regra.faixa, regra.faixa_da_ementa) == (60, "medio", "estruturante")
        assert regra.alertas == (leitura.A_CONFIRMAR,) and (regra.fonte, regra.fo) == (leitura.REGRA, "regra")
        assert not _por_regra(codigos, r=r).confirmada
    uma, duas = leitura.avaliar(regua, _lida(codigos, r=leitura.UMA)), leitura.avaliar(regua, _lida(codigos, r=leitura.DUAS))
    assert (uma.faixa, uma.alertas, uma.fonte, uma.fo) == ("medio", (leitura.A_CONFIRMAR,), leitura.IA, "ia")
    assert (duas.faixa, duas.alertas, duas.fonte, duas.fo) == ("estruturante", (), leitura.IA, "ia2")
    # Abaixo do topo, a fonte não muda a conta: o mesmo peso, a mesma faixa, as mesmas marcas.
    comum = "justica grupo pena prisional neutro neutro"
    a, b = leitura.avaliar(regua, _por_regra(comum, confianca=3)), leitura.avaliar(regua, _lida(comum, confianca=3))
    assert (a.peso, a.faixa, a.marcas, a.alertas, a.conta) == (b.peso, b.faixa, b.marcas, b.alertas, b.conta) and (a.fo, b.fo) == ("regra", "ia")
    # A leitura de regras não pede "segunda": pede a primeira de IA.
    assert leitura.pede_segunda(regua, _por_regra(codigos, confianca=1), votada=True) == ()
    # Quem ninguém leu não tem fonte.
    assert (leitura.avaliar(regua, None).fonte, leitura.avaliar(regua, None).fo) == ("", "")


def test_a_leitura_de_ia_vale_sobre_a_de_regra(regua):
    """Uma função só para as contas: a de IA, da guarda, se houver; senão, a de regras."""
    guarda = leitura.Guarda(regua)
    guarda.registrar("cd", 2026, "1", _lida("justica grupo pena prisional neutro neutro", porque="IA."))
    guarda.registrar("cd", 2026, "2", _lida("fiscal nd nd nd nd nd", porque="A ementa não diz."))  # a IA leu e ficou sem leitura
    guarda.registrar("cd", 2026, "3", _lida("fiscal geral ajuste corta iguala aperta", r=leitura.INSTAVEL))
    vistos = []

    def regras_(item: leitura.Item) -> leitura.Leitura | None:
        vistos.append(item.chave)
        return None if not item.ementa else _por_regra("fiscal geral sistema corta neutro aperta", porque="regra: reforma", r=leitura.DUAS)

    da_ia = leitura.leitura_de(regua, _item("1"), guarda, regras_)
    assert (da_ia.fonte, da_ia.porque) == (leitura.IA, "IA.") and vistos == []  # com leitura de IA, o leitor de regras nem é chamado
    # A IA vale mesmo quando deixa sem leitura: "nd" e instável não caem para a regra.
    assert leitura.leitura_de(regua, _item("2"), guarda, regras_).porque == "A ementa não diz." and not leitura.avaliar(regua, leitura.leitura_de(regua, _item("2"), guarda, regras_)).lida
    assert leitura.avaliar(regua, leitura.leitura_de(regua, _item("3"), guarda, regras_)).motivo == "instavel" and vistos == []
    # Sem leitura de IA: a de regras, sempre com fonte "regra" e uma leitura só.
    da_regra = leitura.leitura_de(regua, _item("9"), guarda, regras_)
    assert (da_regra.fonte, da_regra.r, da_regra.porque, da_regra.fo) == (leitura.REGRA, leitura.UMA, "regra: reforma", "regra") and vistos == ["9"]
    assert leitura.avaliar(regua, da_regra).faixa == "medio"  # 100 pela regra: espera a IA
    # Ninguém leu: sem ementa (o leitor de regras devolve None), sem leitor, ou com o leitor que ainda não lê.
    assert leitura.leitura_de(regua, _item("9", ementa=""), guarda, regras_) is None
    assert leitura.leitura_de(regua, _item("9"), guarda) is None and leitura.leitura_de(regua, _item("9")) is None
    assert leitura.leitura_de(regua, _item("9"), guarda, leitura.sem_leitor) is None
    # Vale também para a Proposicao (a chave sai dela; o leitor de regras recebe o que a IA receberia).
    prop = _prop(1, "PL", 7, 2026, ementa="Aumenta a pena do furto.")
    assert leitura.leitura_de(regua, prop, guarda, regras_).porque == "IA."
    assert leitura.leitura_de(regua, _prop(8, "PL", 8, 2026), guarda, regras_).fonte == leitura.REGRA and vistos[-1] == "8"
    do_senado = _prop(160001, "PL", 20, 2026, casa="sf")
    guarda.registrar("sf", 2026, "PL-20-2026", _lida("fiscal setor ajuste renuncia neutro neutro", r=leitura.DUAS))
    assert leitura.leitura_de(regua, do_senado, guarda, regras_).confirmada
    # Código que o YAML não tem, vindo das regras, é erro de quem as escreveu.
    with pytest.raises(ValueError, match="leitor de regras: código inválido"):
        leitura.leitura_de(regua, _item("9"), guarda, lambda _item: _por_regra("fiscall geral sistema corta neutro aperta"))
    with pytest.raises(ValueError, match="leitor de regras: 3 código"):
        leitura.leitura_de(regua, _item("9"), guarda, lambda _item: _por_regra("fiscal geral sistema"))


def test_o_leitor_junta_as_duas_fontes_para_as_contas(regua):
    guarda = leitura.Guarda(regua)
    guarda.registrar("cd", 2026, "1", _lida("estado grupo servico gasto profissao neutro", iniciativa=1, confianca=3, r=leitura.DUAS))
    leitor = leitura.Leitor(regua, guarda, lambda item: _por_regra("outro local simbolico neutro neutro neutro", confianca=3, porque="regra: denomina"))
    com_ia = leitor.avaliar(_prop(1, "PL", 1, 2026, origem="parlamentar"))
    assert (com_ia.fo, com_ia.peso, com_ia.alertas) == ("ia2", 20, (leitura.INICIATIVA,))  # o tipo e a origem vêm da proposição
    assert leitor.avaliar(_prop(1, "PL", 1, 2026, origem="executivo")).alertas == ()
    assert leitor.avaliar(_item("1"), origem="parlamentar").alertas == (leitura.INICIATIVA,) and leitor.avaliar(_item("1")).alertas == ()
    so_regra = leitor.avaliar(_prop(2, "PL", 2, 2026))
    assert (so_regra.fo, so_regra.peso, so_regra.faixa) == ("regra", 0, "minimo") and leitor.de(_item("2")).porque == "regra: denomina"
    assert leitura.Leitor(regua, guarda).de(_item("2")) is None  # sem leitor de regras, só a IA lê


def test_so_leitura_de_ia_vai_a_guarda(tmp_path: Path, regua):
    guarda = leitura.Guarda(regua)
    with pytest.raises(ValueError, match="só a leitura de IA vai à guarda"):
        guarda.registrar("cd", 2026, "1", _por_regra("fiscal geral ajuste corta iguala aperta"))
    assert len(guarda) == 0 and guarda.gravar(tmp_path) == []
    # O que se lê da guarda e o que se importa é de IA; validar monta a de regras se pedirem.
    assert leitura.de_linha(regua, _lida("fiscal geral ajuste corta iguala aperta").linha()).fonte == leitura.IA
    assert leitura.validar(regua, _registro())[0].fonte == leitura.IA
    regra, motivo = leitura.validar(regua, _registro(r=2), fonte=leitura.REGRA)
    assert motivo == "" and (regra.fonte, regra.r) == (leitura.REGRA, leitura.UMA)
    assert leitura.validar(regua, _registro(), fonte="palpite") == (None, "fonte desconhecida: 'palpite'")
    assert leitura.conferir(regua, regra) == "" and leitura.conferir(regua, _lida("fiscal geral ajuste corta iguala nada")).startswith('código inválido em "co"')
    assert leitura.conferir(regua, _lida("fiscal geral ajuste corta iguala aperta", confianca=7)) != ""


def test_o_ponto_de_encaixe_do_leitor_de_regras(tmp_path: Path, regua, monkeypatch: pytest.MonkeyPatch):
    """``leitor_regras.carregar(regua, root)`` devolve a função ``(Item) -> Leitura | None``."""
    todas = regras.carregar(ROOT)
    leitor = leitura.abrir(ROOT, tmp_path / "data" / "congresso", todas)
    assert leitor is not None and len(leitor.guarda) == 0 and leitor.regras is not None
    # O leitor de regras devolve None (não leu) ou uma leitura de fonte "regra" com códigos do YAML.
    lida = leitor.regras(_item("9", ementa="Denomina rodovia."))
    assert lida is None or (lida.fonte == leitura.REGRA and leitura.conferir(regua, lida) == "")
    assert leitor.regras(_item("9", ementa="")) is None  # sem ementa, não há o que ler
    recebidos = []

    def carregar(regua_, root):
        recebidos.append((regua_, root))
        return lambda item: _por_regra("fiscal geral sistema corta neutro aperta", porque="regra: reforma")

    monkeypatch.setattr(leitor_regras, "carregar", carregar)
    leitor = leitura.abrir(ROOT, tmp_path / "data" / "congresso", todas)
    assert recebidos == [(regua, ROOT)] and leitor.de(_item("9")).porque == "regra: reforma"
    # Acessório: se as regras não carregam, vira aviso e só a IA lê.
    monkeypatch.setattr(leitor_regras, "carregar", lambda _regua, _root: (_ for _ in ()).throw(ValueError("regra torta")))
    avisos: list[str] = []
    leitor = leitura.abrir(ROOT, tmp_path / "data" / "congresso", todas, avisos=avisos)
    assert leitor.regras is None and leitor.de(_item("9")) is None and avisos == ["Leitura: leitor de regras indisponível (regra torta)"]


def test_as_candidatas_e_o_que_vai_a_ia(regua):
    """Vai à IA o que ela ainda não leu e pesa: a matéria votada e a candidata ao topo (a
    regra a põe em 60 ou mais, no núcleo). O resto fica com a leitura de regras."""
    assert leitura.candidata(regua, _por_regra("fiscal geral ajuste corta iguala aperta"))  # 60, núcleo
    assert leitura.candidata(regua, _por_regra("educacao geral sistema investe neutro neutro"))  # 100
    assert not leitura.candidata(regua, _por_regra("justica geral sistema prisional neutro neutro"))  # 60 no entorno: nunca chega ao topo
    assert not leitura.candidata(regua, _por_regra("fiscal setor ajuste corta neutro neutro"))  # 36
    assert not leitura.candidata(regua, _por_regra("fiscal nd nd nd nd nd")) and not leitura.candidata(regua, None)
    # O corte é do config (leitura.candidatas_peso): com 36, o ajuste de um setor do núcleo também vai.
    larga = regras.ProjetoDePais(**{**regua.__dict__, "leitura": regras.LeituraDaRegua(**{**regua.leitura.__dict__, "candidatas_peso": 36})})
    assert leitura.candidata(larga, _por_regra("fiscal setor ajuste corta neutro neutro")) and not leitura.candidata(larga, _por_regra("social setor sistema gasto neutro neutro"))
    assert leitura.pede_ia(regua, None) == () and leitura.pede_ia(regua, None, votada=True) == (leitura.VOTADA,)
    assert leitura.pede_ia(regua, _por_regra("fiscal geral ajuste corta iguala aperta"), votada=True) == (leitura.VOTADA, leitura.CANDIDATA)
    assert leitura.pede_ia(regua, _por_regra("outro local simbolico neutro neutro neutro")) == ()

    itens = {
        "1": _item("1", ementa="Denomina rodovia.", universos=(leitura.PERFIL,)),
        "2": _item("2", tipo="PLP", ementa="Reforma o sistema tributário.", universos=(leitura.PERFIL, leitura.ESPERA)),
        "3": _item("3", ementa="Denomina ponte.", universos=(leitura.VOTO,)),
        "4": _item("4", tipo="PEC", ementa="Reforma a previdência.", universos=(leitura.ESPERA, leitura.VOTO)),
        "5": _item("5", tipo="PLP", ementa="Reforma o ensino.", universos=(leitura.ESPERA,)),
    }

    def regras_(item: leitura.Item) -> leitura.Leitura:
        return _por_regra("fiscal geral sistema corta neutro aperta" if "Reforma" in item.ementa else "outro local simbolico neutro neutro neutro", confianca=3)

    guarda = leitura.Guarda(regua)
    assert [(i.chave, m) for i, m in leitura.para_ia(regua, itens, guarda, regras_)] == [
        ("2", (leitura.CANDIDATA,)), ("3", (leitura.VOTADA,)), ("4", (leitura.VOTADA, leitura.CANDIDATA)), ("5", (leitura.CANDIDATA,)),
    ]  # fmt: skip
    assert [i.chave for i, _m in leitura.para_ia(regua, itens, guarda)] == ["3", "4"]  # sem leitor de regras, só as votadas
    # Lida pela IA, sai da fila da primeira leitura (mesmo que tenha ficado sem leitura) e, se pesa, entra na da segunda.
    guarda.registrar("cd", 2026, "2", _lida("fiscal geral sistema corta neutro aperta", confianca=3))
    guarda.registrar("cd", 2026, "3", _lida("outro nd nd nd nd nd", confianca=3))
    guarda.registrar("cd", 2026, "5", _lida("educacao setor ajuste neutro neutro neutro", confianca=3))  # a IA discordou da regra: 36
    assert [i.chave for i, _m in leitura.para_ia(regua, itens, guarda, regras_)] == ["4"]
    assert [(i.chave, m) for i, m in leitura.para_segunda(regua, itens, guarda)] == [("2", (leitura.PESO_ALTO,)), ("3", (leitura.VOTADA,))]
    # A cobertura: quantas por regra, quantas por IA, quantas confirmadas, quantas sem leitura.
    guarda.registrar("cd", 2026, "2", _lida("fiscal geral sistema corta neutro aperta", confianca=3, r=leitura.DUAS))
    leitor = leitura.Leitor(regua, guarda, regras_)
    assert leitura.cobertura(regua, (leitor.de(i) for i in itens.values())) == {"total": 5, "lidos": 4, "regra": 2, "ia": 2, "confirmados": 1, "sem_leitura": 1}
    assert leitura.cobertura(regua, [None, _lida("fiscal geral ajuste corta iguala aperta", r=leitura.INSTAVEL)]) == {"total": 2, "lidos": 0, "regra": 0, "ia": 0, "confirmados": 0, "sem_leitura": 2}
    assert leitura.cobertura(regua, []) == {"total": 0, "lidos": 0, "regra": 0, "ia": 0, "confirmados": 0, "sem_leitura": 0}


def test_o_ano_da_camara_tambem_vem_das_materias_votadas_ja_publicadas(tmp_path: Path, regua):
    pasta = tmp_path / "data" / "congresso"
    assert leitura.anos_das_materias(pasta) == {}  # sem o arquivo, nada
    pasta.mkdir(parents=True)
    (pasta / "materias-cd.json").write_text(json.dumps({"v": 1, "materias": {"PL 10/2024": {"id": 1010, "e": "x"}, "REQ 5/2026": {"id": 77}, "sem número": {"id": 5}, "PL 9/2025": {}}}), encoding="utf-8")
    assert leitura.anos_das_materias(pasta) == {("cd", "1010"): 2024, ("cd", "77"): 2026}
    entrada = _jsonl(tmp_path / "lidos.jsonl", {"casa": "cd", "id": "1010", **_registro()}, {"casa": "cd", "id": "4242", **_registro()})
    assert main(["congresso", "leitura", "importar", "--dir", str(tmp_path), str(entrada)]) == 0  # uma entra (2024), a outra não tem de onde tirar o ano
    assert [c.name for c in (pasta / "leitura").iterdir()] == ["cd-2024.json"] and len(leitura.Guarda.ler(pasta / "leitura", regua)) == 1


# ── de ponta a ponta, com o Congresso de mentira ─────────────────────────────


@pytest.fixture
def publicado(tmp_path: Path) -> Path:
    """A pasta publicada com o Congresso de mentira já montado (tests/fixtures/congresso)."""
    data_dir = tmp_path / "data"
    dados.atualizar(tmp_path, data_dir, tmp_path / "cache", dados.Opcoes(hoje=fabrica.HOJE, projetos_desde=2026), fetch=fabrica.FetchFalso(), regras=fabrica.regras_de_teste())
    return tmp_path


def test_exportar_e_importar_de_ponta_a_ponta(publicado: Path, regua, monkeypatch: pytest.MonkeyPatch):
    """Câmara de mentira (2026): o universo tem o PL 10 e o PL 11 (de deputado, tramitando e
    votados no texto principal), o PL 12 (do Executivo, virou norma: só pelo voto), a PEC 1 e
    o PL 14 (de deputado, tramitando) e o PL 13 (de deputado, arquivado: só perfil). O PL 15
    anda junto com outro e fica fora. No Senado: o PL 20 (da senadora 101, votado) e o que
    tramita. À IA vão só as votadas e as candidatas pela leitura de regras (aqui, a PEC)."""
    monkeypatch.setattr(cli, "Coletor", lambda cache: fontes.Coletor(cache, fetch=fabrica.FetchFalso(), pausa=0.0))
    saida = publicado / "saida"
    comando = ["congresso", "leitura", "exportar", "--dir", str(publicado), "--cache", str(publicado / "cache"), "--saida", str(saida), "--lote", "4", "--now", f"{fabrica.HOJE}T12:00:00"]

    def mapas_de(nome: str) -> list[list]:
        return [linha for arq in sorted(saida.glob(f"{nome}-*.map.json")) for linha in json.loads(arq.read_text(encoding="utf-8"))]

    # Com um leitor de regras que não lê nada, não há candidatas: saem só as votadas.
    monkeypatch.setattr(leitor_regras, "carregar", lambda _regua, _root: leitura.sem_leitor)
    assert main(comando) == 0
    assert sorted(linha[2:] for linha in mapas_de("lote") if linha[1] == "cd") == [[1010, "PL 10/2026"], [1011, "PL 11/2026"], [1012, "PL 12/2026"]]
    assert [linha[2] for linha in mapas_de("lote") if linha[1] == "sf"] == ["PL-20-2026"]

    # Com --tudo, sai todo o universo que a IA ainda não leu: primeiro as que precisam dela.
    assert main([*comando, "--tudo"]) == 0
    assert sorted(linha[2] for linha in mapas_de("lote") if linha[1] == "cd") == [1010, 1011, 1012, 1013, 1014, 1015]
    assert sorted(map(str, (linha[2] for linha in mapas_de("lote")[:4]))) == ["1010", "1011", "1012", "PL-20-2026"]

    # Com um leitor de regras que põe toda PEC no topo, a PEC entra como candidata.
    monkeypatch.setattr(
        leitor_regras, "carregar", lambda _regua, _root: lambda item: _por_regra("fiscal geral sistema nd neutro neutro" if item.tipo == "PEC" else "outro grupo conduta neutro neutro neutro", confianca=3)
    )
    assert main(comando) == 0
    mapas = mapas_de("lote")
    assert sorted(linha[2:] for linha in mapas if linha[1] == "cd") == [[1010, "PL 10/2026"], [1011, "PL 11/2026"], [1012, "PL 12/2026"], [1013, "PEC 1/2026"]]
    assert [linha[2] for linha in mapas if linha[1] == "sf"] == ["PL-20-2026"]  # o PL 13 e o PL 14 da Câmara e o que só tramita no Senado ficam com a regra
    assert len(list(saida.glob("lote-*.txt"))) == -(-len(mapas) // 4) == 2
    texto = "".join(arq.read_text(encoding="utf-8") for arq in sorted(saida.glob("lote-*.txt")))
    assert "E: Altera a tabela do imposto de renda." in texto and "X: Corrige a tabela do imposto de renda pela inflação" in texto
    assert "Ana Lima" not in texto and "PL 10" not in texto and "1010" not in texto

    # Um leitor responde pelo id opaco; a volta é pelo mapa. Depois de importar, o que foi lido não sai mais.
    lidos = []
    for casa, ident, _titulo in (linha[1:] for linha in mapas):
        lidos.append({"casa": casa, "id": str(ident), **_registro(a="fiscal", e="geral", m="sistema" if ident == 1010 else "ajuste", pq="Lido no teste.")})
    entrada = _jsonl(publicado / "lidos.jsonl", *lidos)
    assert main(["congresso", "leitura", "importar", "--dir", str(publicado), "--mapa", str(saida), str(entrada)]) == 0
    guarda = leitura.Guarda.ler(publicado / "data" / "congresso" / "leitura", regua)
    assert len(guarda) == len(mapas) == 5 and guarda.ano_de("cd", "1010") == 2026 and guarda.de("sf", "PL-20-2026") is not None
    assert main([*comando, "--segunda"]) == 0
    assert list(saida.glob("lote-*")) == []  # nada mais espera a primeira leitura de IA
    segundas = mapas_de("segunda")
    # Todas têm peso 60 ou 100 com um leitor só: todas esperam a segunda leitura.
    assert sorted(map(str, (linha[2] for linha in segundas))) == sorted(map(str, (linha[2] for linha in mapas)))
    assert all(linha[0].startswith("s") for linha in segundas)
    # Confirmadas (r = 2), saem da fila; a leitura que as contas veem é a de IA, e a do resto, a de regras.
    confirmadas = _jsonl(publicado / "confirmadas.jsonl", *({**linha, "r": 2} for linha in lidos))
    assert main(["congresso", "leitura", "importar", "--dir", str(publicado), str(confirmadas)]) == 0  # o ano já está na guarda
    assert main([*comando, "--segunda"]) == 0 and list(saida.glob("lote-*")) + list(saida.glob("segunda-*")) == []
    leitor = leitura.abrir(publicado, publicado / "data" / "congresso", regras.carregar(ROOT))
    assert leitor.avaliar(_item("1010")).fo == "ia2" and leitor.avaliar(_item("1010")).faixa == "estruturante"
    assert leitor.avaliar(_item("1015")).fo == "regra" and leitor.avaliar(_item("1015")).peso == 5
