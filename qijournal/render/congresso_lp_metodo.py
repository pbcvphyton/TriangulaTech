"""A tela da visão por parlamentar: o bloco "A leitura dos projetos" do Como ler (o modelo
``congresso_lp_metodo.html.j2``, incluído no fim da seção "Como ler" que já existe).

Como nos outros módulos da tela, aqui só se **apresenta**:

- **a régua** sai inteira de ``config/congresso.yaml`` (``projeto_de_pais``): o objetivo, os seis
  campos com os códigos, os valores e as definições, os níveis, as faixas e as três marcas. Este
  módulo não escreve nome de faixa, alavanca, marca nem código;
- **a cobertura e a auditoria** saem de ``meta.json`` (``leitura_projetos``). A auditoria é
  escrita por fora (``data/congresso/leitura/auditoria.json``) e só repassada: o que vier a
  menos simplesmente não aparece, e nenhum número é inventado aqui;
- **o que a régua não mede**, os cinco erros de leitura e os limites são o texto da régua
  (seção 6), escritos com os rótulos do config.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from . import congresso as pagina

CONSTANCIA = "mede a constância entre leitores de IA; não foi conferida contra codificação humana"
# Como a tela chama cada leitura da auditoria (as chaves de ``auditoria.leitores``).
LEITURAS = (("primeira", "Primeira leitura"), ("segunda", "Segunda leitura"), ("terceira", "Terceira leitura"))
# O que vale quando a auditoria não traz os limites.
LIMITES = (
    "A leitura é da ementa (e, quando há, da ementa detalhada e das palavras-chave), não do texto integral.",
    "Não houve codificação por pessoas para servir de padrão: os números medem se leitores de IA diferentes chegam à mesma faixa.",
    "O topo da régua só é procurado entre o que a primeira leitura pôs lá: projeto que ela deixou logo abaixo não é relido.",
    "Na matéria votada, lê-se a ementa do registro, não o substitutivo que foi a voto.",
)


def _num(valor: Any) -> str:
    return pagina._num(valor)


def _fator(valor: float | None) -> str:
    """Um fator do peso, com vírgula e sem zeros à direita ("1", "0,6", "0,15")."""
    if valor is None:
        return "—"
    return f"{float(valor):.2f}".rstrip("0").rstrip(".").replace(".", ",")


def _sinal(valor: int | None) -> str:
    return "—" if valor is None else "+1" if valor > 0 else "−1" if valor < 0 else "0"


def _pct(valor: Any) -> str:
    """Percentual com até uma casa ("96,3%", "82%")."""
    try:
        return f"{float(valor):.1f}".replace(".", ",").removesuffix(",0") + "%"
    except (TypeError, ValueError):
        return "—"


def _kappa(valor: Any) -> str:
    try:
        return f"{float(valor):.2f}".replace(".", ",")
    except (TypeError, ValueError):
        return "—"


def _n(bloco: Any, chave: str) -> int | None:
    valor = bloco.get(chave) if isinstance(bloco, Mapping) else None
    return int(valor) if isinstance(valor, int | float) and not isinstance(valor, bool) else None


def _lista(itens: list[str]) -> str:
    """"a, b e c"."""
    return itens[0] if len(itens) == 1 else ", ".join(itens[:-1]) + " e " + itens[-1] if itens else ""


# ── a régua, do config ───────────────────────────────────────────────────────


def _regua(regua: Any) -> dict[str, Any]:
    """Os seis campos (o papel de cada um na conta, os códigos com o que valem e a definição), a
    fórmula por extenso com um exemplo, as faixas e as três marcas."""
    niveis = list(regua.niveis)  # (id, peso, rótulo), do maior para o menor
    de_peso = list(regua.campos_de_peso)
    marcas = {m.id: m for m in regua.marcas}
    def vale(campo: Any, codigo: Any) -> str:
        """O que um código vale na conta: o nível (na alavanca), o fator (nos campos de peso) ou o sinal (nos de marca)."""
        if campo.de_nivel:
            return next((rot for ident, _peso, rot in niveis if ident == codigo.nivel), "")
        return "× " + _fator(codigo.peso) if campo.de_peso else _sinal(codigo.valor)

    campos = []
    for campo in regua.campos:
        if campo.de_nivel:
            papel = "dá o nível: " + "; ".join(f"{rot} × {_fator(peso)}" for _id, peso, rot in niveis)
        elif campo.de_peso:
            papel = "multiplica o peso"
        else:
            marca = marcas.get(campo.marca)
            papel = f"dá a marca “{marca.rotulo}”" if marca else "não entra na conta"
        campos.append({"rotulo": campo.rotulo, "papel": papel, "codigos": [{"rotulo": c.rotulo, "vale": vale(campo, c), "definicao": c.definicao} for c in campo.codigos]})

    fatores = " × ".join(["o nível de “" + regua.alavanca.rotulo + "”", *(f"“{c.rotulo}”" for c in de_peso)])
    exemplo = ""
    if niveis and all(c.codigos for c in de_peso):
        # um exemplo tirado do próprio config: o nível mais alto, o segundo código do primeiro campo de peso e o primeiro dos demais
        escolhidos = [c.codigos[1 if i == 0 and len(c.codigos) > 1 else 0] for i, c in enumerate(de_peso)]
        produto = 100.0 * niveis[0][1]
        for c in escolhidos:
            produto *= c.peso or 0.0
        exemplo = f"100 × {_fator(niveis[0][1])} ({niveis[0][2]}) × " + " × ".join(f"{_fator(c.peso)} ({c.rotulo})" for c in escolhidos) + f" = {_num(int(produto + 0.5))}"

    faixas = []
    for i, f in enumerate(regua.faixas):
        ate = regua.faixas[i + 1].de - 1 if i + 1 < len(regua.faixas) else None
        faixas.append(
            {
                "rotulo": f.rotulo,
                "pesos": f"{f.de} a {ate}" if ate is not None else f"{f.de} ou mais",
                "nota": "; ".join(x for x in (f"só em alavanca do {regua.rotulo_do_nivel(f.so_nivel)}" if f.so_nivel else "", f.descricao) if x),
            }
        )
    return {
        "campos": campos,
        "n_codigos": sum(len(c["codigos"]) for c in campos),
        "formula": f"Peso = 100 × {fatores}, arredondado para o inteiro mais próximo (o meio sobe).",
        "exemplo": exemplo,
        "faixas": faixas,
        "marcas": [{"rotulo": m.rotulo, "valores": [{"n": _sinal(v.valor), "rotulo": v.rotulo, "forma": v.forma or "vazado"} for v in m.valores if v.valor is not None]} for m in regua.marcas],
        "nd": regua.nd,
    }


# ── a auditoria, de meta.json ────────────────────────────────────────────────


def _nome_do_campo(regua: Any, ident: str) -> str:
    campo = regua.campo(ident)
    if campo is not None:
        return campo.rotulo
    marca = regua.marca(ident)
    return f"A marca “{marca.rotulo}”" if marca is not None else ident


def _concordancia(bloco: Any, regua: Any) -> list[dict[str, str]]:
    """As linhas de uma tabela de concordância: a faixa e cada campo, com a % de leituras iguais
    e o kappa."""
    if not isinstance(bloco, Mapping):
        return []
    linhas = []
    faixa = bloco.get("faixa")
    if isinstance(faixa, Mapping):
        linhas.append({"nome": "A faixa do peso", "igual": _pct(faixa.get("igual_pct")), "kappa": _kappa(faixa.get("kappa")), "forte": True})
    for ident, x in (bloco.get("por_campo") or {}).items():
        if isinstance(x, Mapping):
            linhas.append({"nome": _nome_do_campo(regua, str(ident)), "igual": _pct(x.get("igual_pct")), "kappa": _kappa(x.get("kappa")), "forte": False})
    return linhas


def _auditoria(a: Any, regua: Any, R: Any) -> dict[str, Any] | None:
    """O que a auditoria publicada traz, em texto: quem leu, o estado das leituras, as perguntas
    de controle, o piloto e a segunda leitura. Cada parte só sai se o dado veio."""
    if not isinstance(a, Mapping) or not a:
        return None
    leitores = a.get("leitores") if isinstance(a.get("leitores"), Mapping) else {}
    quem = [{"nome": nome, "texto": str(leitores[chave])} for chave, nome in LEITURAS if leitores.get(chave)]

    estado = a.get("estado_das_leituras")
    estados = []
    if isinstance(estado, Mapping):
        for chave, texto in (
            ("uma_leitura", "têm uma leitura só"),
            ("duas_na_mesma_faixa", "têm duas leituras na mesma faixa"),
            ("decidida_pelo_terceiro", "foram decididas pelo terceiro leitor"),
            ("instavel", "ficaram instáveis (os leitores não se entenderam): sem leitura, fora das contas"),
        ):
            if _n(estado, chave) is not None:
                estados.append(f"{_num(estado[chave])} {texto}")

    blocos = []
    c = a.get("controles")
    if isinstance(c, Mapping) and _n(c, "perguntas"):
        partes = [str(c.get("o_que_e") or "").strip()]
        if _n(c, "mesma_faixa") is not None:
            partes.append(f"Resultado: {_num(c['mesma_faixa'])} de {_num(c['perguntas'])} na mesma faixa ({_pct(c.get('mesma_faixa_pct'))})" + (f", em {_num(c['lotes'])} lotes" if _n(c, "lotes") else "") + ".")
        if c.get("campos_iguais_pct") is not None:
            partes.append(f"Campo a campo, {_pct(c['campos_iguais_pct'])} dos códigos iguais.")
        if _n(c, "lotes_relidos") is not None:
            partes.append(f"{_num(c['lotes_relidos'])} {'lote foi relido' if c['lotes_relidos'] == 1 else 'lotes foram relidos'}.")
        blocos.append({"titulo": "As perguntas de controle", "texto": " ".join(x for x in partes if x), "tabela": [], "depois": ""})

    p = a.get("piloto")
    if isinstance(p, Mapping) and (p.get("faixa") or p.get("por_campo")):
        partes = [str(p.get("o_que_e") or "").strip()]
        if _n(p, "amostra"):
            partes.append(f"Amostra: {_num(p['amostra'])} ementas" + (f" ({p['composicao']})" if p.get("composicao") else "") + ".")
        espelho = p.get("pares_espelho")
        if isinstance(espelho, Mapping) and _n(espelho, "total"):
            partes.append(
                f"Pares-espelho (gastar e cortar, regular e desregular: os dois lados têm de cair na mesma faixa): {_num(espelho.get('na_mesma_faixa'))} de {_num(espelho['total'])}"
                + (f"; {espelho['nota']}" if espelho.get("nota") else "")
                + "."
            )
        depois = p.get("depois_do_ajuste_das_instrucoes")
        depois_txt = ""
        if isinstance(depois, Mapping) and isinstance(depois.get("faixa"), Mapping):
            depois_txt = (
                f"Depois do ajuste das instruções, em {_num(depois.get('amostra'))} ementas" + (f" ({depois['composicao']})" if depois.get("composicao") else "")
                + f": mesma faixa em {_pct(depois['faixa'].get('igual_pct'))} (kappa {_kappa(depois['faixa'].get('kappa'))})."
            )  # fmt: skip
        blocos.append({"titulo": "O piloto: dois leitores nas mesmas ementas", "texto": " ".join(x for x in partes if x), "tabela": _concordancia(p, regua), "depois": depois_txt})

    s = a.get("segunda_leitura")
    if isinstance(s, Mapping) and _n(s, "lidas"):
        partes = [f"O leitor intermediário releu {_num(s['lidas'])} proposições sem ver a primeira leitura"]
        topo, votadas = s.get("topo"), s.get("votadas")
        if isinstance(topo, Mapping) and isinstance(votadas, Mapping) and _n(topo, "n") is not None and _n(votadas, "n") is not None:
            partes[0] += f": {_num(topo['n'])} que a primeira pôs no topo da régua e {_num(votadas['n'])} matérias votadas"
        partes[0] += "."
        if _n(s, "mesma_faixa") is not None:
            partes.append(f"Caiu na mesma faixa em {_num(s['mesma_faixa'])} ({_pct(s.get('mesma_faixa_pct'))}).")
        if isinstance(topo, Mapping) and _n(topo, "n") and _n(topo, "mesma_faixa") is not None:
            partes.append(
                f"No topo, só {_num(topo['mesma_faixa'])} de {_num(topo['n'])}: a primeira leitura põe no topo bem mais do que a segunda confirma. É por isso que só o confirmado leva o nome “{R.topo}”."
            )
        if isinstance(votadas, Mapping) and _n(votadas, "n") and _n(votadas, "mesma_faixa") is not None:
            partes.append(f"Nas votadas, {_num(votadas['mesma_faixa'])} de {_num(votadas['n'])}.")
        if _n(s, "decididas_pelo_terceiro") is not None:
            partes.append(f"Onde as duas divergiram, o leitor mais forte decidiu {_num(s['decididas_pelo_terceiro'])}" + (f"; {_num(s['instaveis'])} ficaram instáveis, sem leitura" if _n(s, "instaveis") is not None else "") + ".")
        if _n(s, "votadas_com_marca_de_contas_em_disputa"):
            partes.append(f"Em {_num(s['votadas_com_marca_de_contas_em_disputa'])} matérias votadas, os leitores divergiram na marca do caixa: elas ficam fora do voto fiscal.")
        if _n(s, "esperam_o_terceiro"):
            partes.append(f"{_num(s['esperam_o_terceiro'])} ainda esperam o terceiro leitor: aparecem como “a confirmar”.")
        blocos.append({"titulo": "A segunda e a terceira leituras", "texto": " ".join(partes), "tabela": [], "depois": ""})

    limites = [str(x) for x in a.get("limites") or [] if x] if isinstance(a.get("limites"), list) else []
    return {
        "data": pagina._data(a.get("data"), ano=True) if a.get("data") else "",
        "lidas": _num(a["lidas"]) if _n(a, "lidas") else "",
        "quem": quem,
        "estados": _lista(estados),
        "blocos": blocos,
        "limites": limites,
    }


# ── tudo junto ───────────────────────────────────────────────────────────────


def _cobertura(casas: list[tuple[str, str]], cobertura: Any) -> list[dict[str, str]]:
    """Uma linha por Casa: quantas proposições as contas pedem, quantas têm leitura, quantas
    ficaram sem, quantas têm a faixa confirmada, e a mesma conta por uso (o perfil de quem
    propõe, o que espera e as matérias votadas)."""
    linhas = []
    for ident, nome in casas:
        c = cobertura.get(ident) if isinstance(cobertura, Mapping) else None
        if not isinstance(c, Mapping) or not c.get("total"):
            continue

        def uso(chave: str, c: Mapping[str, Any] = c) -> str:
            u = c.get(chave)
            return f"{_num(u.get('lidos'))} de {_num(u.get('total'))}" if isinstance(u, Mapping) and u.get("total") else "—"

        linhas.append(
            {
                "casa": nome,
                "total": _num(c.get("total")),
                "lidos": _num(c.get("lidos")),
                "pct": _pct(100.0 * (c.get("lidos") or 0) / c["total"]),
                "sem": _num(c.get("sem_leitura")),
                "confirmados": _num(c.get("confirmados")),
                "perfil": uso("perfil"),
                "espera": uso("espera"),
                "voto": uso("voto"),
            }
        )
    return linhas


def metodo(R: Any, regua: Any, meta: Mapping[str, Any], casas: list[tuple[str, str]], desde: int | None, cruzar: Mapping[str, Any] | None) -> dict[str, Any]:
    """O bloco "A leitura dos projetos" do Como ler. ``R``: os rótulos da tela; ``regua``: o
    ``projeto_de_pais`` do config; ``meta``: ``meta.json`` → ``leitura_projetos``; ``casas``:
    ``(id, nome)`` das Casas que têm a visão; ``cruzar``: os parâmetros do cruzamento (de uma
    Casa qualquer: são os mesmos)."""
    m, cortes = R.m, R.m["cortes"]
    nivel = {ident: rot for ident, rot in m["niveis"]}
    por_nivel: dict[str, list[str]] = {}
    for _ident, rotulo, n in m["eixos"]:
        por_nivel.setdefault(n, []).append(rotulo)
    ordem = [ident for ident, _rot in m["niveis"]]
    nucleo = por_nivel.get(ordem[0], []) if ordem else []
    do_nucleo = nivel.get(ordem[0], "") if ordem else ""  # como o config chama o nível mais alto
    # os outros níveis, com as alavancas de cada um (se a única alavanca tem o nome do nível, basta o nome)
    demais = [nivel[n] if [r.lower() for r in por_nivel[n]] == [nivel[n].lower()] else f"{nivel[n]}: {_lista(por_nivel[n])}" for n in ordem[1:] if por_nivel.get(n)]
    baixas = [rot for ident, rot, _de in m["faixas"] if ident in m["grupos"]["baixas"]]
    caixa = next((rot for ident, rot, _v in m["marcas"] if ident == m["papeis"].get("caixa")), "")
    excecao = next((v[1] for ident, _rot, valores in m["marcas"] if ident == m["papeis"].get("excecao") for v in valores if v[3] == -1), "")
    quase = round(100 * float(cortes.get("quase_unanime") or 0.9))
    fechado = round(100 * float(cortes.get("partido_fechado") or 0.8))
    par = cruzar or {}
    auditoria = _auditoria(meta.get("auditoria"), regua, R)

    premissa = (
        f"Esta parte da página mede cada projeto em relação a um objetivo declarado: {m.get('objetivo') or '—'}. "
        f"O objetivo e as alavancas do {do_nucleo} ({_lista(nucleo)}) são escolha editorial do dono do jornal, escrita em config/congresso.yaml. "
        "O método é o mesmo nos dois sentidos: gastar e cortar, regular e desregular caem na mesma faixa. A premissa, não: "
        + (f"o que fica fora do {do_nucleo} pesa menos por escolha, não por valer menos (" + "; ".join(demais) + "). " if demais else "")
        + "Quem parte de outro objetivo chega a outra régua."
    )

    itens = [
        (
            "O que é lido",
            ("Projetos de lei, de lei complementar e propostas de emenda à Constituição; nas matérias votadas, também as medidas provisórias. A IA lê a ementa oficial (e, quando há, a ementa detalhada e as palavras-chave da "
            "indexação), sem ver número, autor, partido nem origem, e marca os seis campos da régua. É leitura, não registro: cada cartão de projeto traz os códigos, a conta do peso, a justificativa e o link para contestar."),
        ),
        (
            "O peso (0 a 100)",
            ("Quanto o texto mexe em regra de sistema nas alavancas do objetivo. Multiplica de propósito: quase todo projeto cita um tema nobre e vale “para todos”; só pesa quando os três fatores coincidem. "
            "Tem poucos valores: separa prateleiras, não posições. Não diz se o projeto é bom nem se seria aprovado."),
        ),
        (
            f"“{R.topo.capitalize()}” e “a confirmar”",
            (f"Só leva o nome “{R.topo}” o projeto que dois leitores, um sem ver o outro, puseram nessa faixa, ou que o terceiro decidiu. Com uma leitura só, ele aparece como “a confirmar” e, nas contas, vale como {R.meio}. "
            "O que compara pessoas conta só o confirmado."),
        ),
        (
            "As três marcas",
            (f"{_lista([rot for _i, rot, _v in m['marcas']])}: cada uma vale +1, 0 ou −1 pelo código lido, ou “{m['nd']}”. Andam ao lado do peso: nunca são somadas entre si nem multiplicadas por ele. "
            f"A marca −1 de “{caixa}” diz que a ementa enuncia despesa, renúncia ou carimbo. Não diz que falta fonte, não mede o tamanho nem o mérito."),
        ),
        (
            "Parado, e em que porta",
            f"Sem andamento oficial há mais de {_num(cortes.get('dias_parado') or 365)} dias. A porta e quem está com a matéria vêm da situação oficial, como em “A fila”. O motivo de não andar, o registro não diz.",
        ),
        (
            "O perfil de cada um",
            f"PL, PLP e PEC de autoria principal (quem assina primeiro), apresentados na legislatura, com leitura. Ementa igual à de proposição anterior e projeto de bancada ficam fora. Percentuais só com {cortes['min_proj']} lidos ou mais. "
            f"Saldo de {caixa.lower()}: (projetos de marca +1 − projetos de marca −1) ÷ projetos com marca +1 ou −1, de −100 a +100, com {cortes['min_saldo']} ou mais. "
            + (f"Taxa de exceção: a parte dos lidos com a marca “{excecao}”." if excecao else ""),
        ),
        (
            "A faixa do acaso",
            (f"Com poucos projetos, qualquer parte sai alta ou baixa por acaso. A faixa cinza do gráfico de “Quem propõe” é o que a diferença para a taxa da Casa pode ser só por acaso: binomial exata, {round(100 * (1 - float(cortes.get('funil_alfa') or 0.01)))}% de confiança, "
            "já contando todas as pessoas comparadas."),
        ),
        (
            "Quando pesa",
            (f"Nas votações nominais do texto principal no Plenário{', desde ' + str(desde) if desde else ''} (uma por matéria; na PEC, o último turno), fora as quase unânimes ({quase}% ou mais do mesmo lado): a presença e o voto com o partido "
            f"nas de matéria de peso {cortes['voto_peso']} ou mais, ao lado das demais. Não depende de marca."),
        ),
        (
            "Voto fiscal",
            (f"Sim em matéria de marca +1, Não em matéria de marca −1, só onde dois leitores chegaram à mesma marca. Em % dos votos dados, com {cortes['min_votos']} votos ou mais. Diz de que lado do caixa a pessoa votou, não se votou certo. "
            f"Quando quase todas as matérias são de uma marca só, a medida se confunde com apoiar ou não o governo, e a tela avisa, com os números. Com menos de {cortes['min_pessoas']} pessoas medidas, a página diz “não dá para responder por pessoa”."),
        ),
        (
            "Contra o próprio partido",
            f"Voto diferente do da maioria do partido quando ela reuniu {fechado}% ou mais do partido, como na dissidência que a página já conta. Em contagem, sem teste: com tão poucas votações, nenhum teste teria força.",
        ),
        (
            "Quanto o partido explica",
            "Da variação entre pessoas em cada medida, a parte entre partidos, descontado o que qualquer divisão em tantos grupos explicaria. "
            + (f"O traço do acaso sai embaralhando os partidos entre as pessoas {_num(par.get('permutacoes'))} vezes; o intervalo, reamostrando as pessoas {_num(par.get('reamostras'))} vezes. " if par.get("permutacoes") and par.get("reamostras") else "")
            + "Barra cujo intervalo encosta no acaso sai em cinza: não é “é a pessoa”, é “não dá para separar”.",
        ),
        (
            "O que anda junto",
            "Correlação de postos (Spearman) entre as pessoas da Casa e, ao lado, a mesma conta dentro de cada partido. Só vira afirmação o que passa do acaso"
            + (f" ({round(100 * (1 - float(par['alfa_pares'])))}%, contando todos os pares)" if par.get("alfa_pares") else "")
            + ". Andar junto não é causa.",
        ),
    ]  # fmt: skip

    erros = [
        ("Peso como qualidade.", f"Peso é quanto mexe. Projeto {R.topo} pode afrouxar uma regra."),
        (
            "Marca negativa como “ruim”, como “sem fonte” ou como ideologia.",
            "A marca diz que a ementa enuncia despesa, renúncia, carimbo ou exceção. Não diz que falta fonte, não mede tamanho (isenção de taxa de concurso e aposentadoria especial levam o mesmo código) nem mérito.",
        ),
        (
            f"{_lista(['“' + b.capitalize() + '”' if i == 0 else '“' + b + '”' for i, b in enumerate(baixas)]).replace(' e ', ' ou ')} como inútil, ou perfil assim como defeito.",
            f"É o padrão em todos os partidos: compare com a Casa. E a faixa depende da premissa: o que fica fora do {do_nucleo} pesa menos por escolha do dono.",
        ),
        ("Volume ou ponta como mérito, e “parado” como culpa do autor.", "Protocolar ementa ambiciosa é fácil; a porta é de outro; a apensada segue a principal."),
        ("“Voto fiscal” como voto certo ou como independência.", "É o lado do caixa. Sem votar contra o próprio partido, ou contra dois governos, o que se mede é o partido."),
    ]

    return {
        "premissa": premissa,
        "itens": itens,
        "regua": _regua(regua),
        "cobertura": _cobertura(casas, meta.get("cobertura")),
        "auditoria": auditoria,
        "constancia": CONSTANCIA,
        "nao_mede": (
            "O texto integral da grande maioria (o jabuti sob “e dá outras providências”, a fonte prevista no articulado, o que o substitutivo mudou), o custo em reais, a constitucionalidade, o efeito real, "
            "o mérito social ou moral, a chance de aprovação e o trabalho fora da autoria: relatoria, emenda, negociação, emendas ao orçamento. E depende de como o autor redige: ementa vaga fica sem leitura; ementa franca leva as marcas."
        ),
        "erros": erros,
        "limites": (auditoria or {}).get("limites") or list(LIMITES),
        "versao": meta.get("versao_da_regua"),
        "data": pagina._data(meta.get("data"), ano=True) if meta.get("data") else "",
    }
