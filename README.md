# Triangula — jornal diário

Jornal diário em português, montado e publicado automaticamente todo dia de
manhã: mercados, economia, direito e regulação (STF, STJ, TJs…), política,
geopolítica, tecnologia, mercado imobiliário, esporte, natureza e meio ambiente
e cultura — com cotações, clima, um resumo "Em 1 minuto", a análise de como cada
veículo cobriu cada assunto e a lista completa de todas as notícias do dia.

- **Edição do dia:** <https://pbcvphyton.github.io/TriangulaTech/>
- **Edições anteriores:** <https://pbcvphyton.github.io/TriangulaTech/edicoes/>
- **Instagram:** posts e stories gerados da edição e publicados sozinhos ([guia](docs/instagram.md))

Nada precisa ser feito à mão: o GitHub gera a edição sozinho a partir das
**05:07 (horário de Brasília)**, publica o site e dispara o e-mail.

---

## Como funciona

```
  GitHub Actions — todo dia a partir das 05:07 (Brasília; de hora em hora + disparo da rotina)
        │
        ▼
  0. Congresso ── (Triangula) dados abertos da Câmara e do Senado: votações nominais,
        │      proposições e relatorias viram a aba Congresso (edicoes/congresso.html)
        │      e as fichas de quem a edição citar. Sem IA. Acessório: se falhar, a
        │      edição sai com o que já existe em data/congresso/
        ▼
  1. Coleta ── ~100 feeds (Valor, Folha, Estadão, FT, WSJ, NYT, JOTA, STF, STJ, ge,
        │      g1 Natureza…), cotações (Yahoo Finance, CoinGecko, Banco Central) e
        │      clima de São Paulo (Open-Meteo). Nenhuma editoria é descartada.
        ▼
  2. Edição ── com IA, compilação por editoria: TODAS as notícias em blocos
        │      (Economia & Mercados, Política & Justiça, Empresas/Tecnologia/
        │      Imobiliário, Mundo & Natureza, Esporte/Cultura); em cada bloco a IA
        │      une o que é o mesmo assunto, interpreta o foco de cada veículo e o
        │      lado que ele seguiu, e redige; o fechamento escolhe as matérias.
        │      Se uma IA estourar o limite, a seguinte assume (Gemini → Gemini
        │      Flash-Lite → AIML → SenseNova → Mistral → Kimi → Claude). Sem IA:
        │      edição automática.
        ▼
  2b. Termômetro ── (Triangula) os textos compilados nas matérias passam pelo
        │      Termômetro de Viés Editorial v1.1: a mesma cadeia de IAs aponta os
        │      sinais com citação literal e o cálculo de referência dá o T de cada um
        ▼
  3. Páginas ── capa (index.html), cópia no arquivo (edicoes/), todas as notícias
        │      do dia (edicoes/AAAA-MM-DD-todas.html), aba Analítico Político
        │      (edicoes/analitico.html), Pesquisa analítica (edicoes/pesquisa.html,
        │      com data/busca/), e-mail em HTML e texto
        │
        ▼
  4. Commit no repositório ──► GitHub Pages publica o site
        │
        ▼
  5. E-mail ── rotina diária do Claude (Gmail) e/ou envio direto por SMTP
```

Garantias importantes:

- **Uma fonte fora do ar não derruba a edição.** Cada feed, cotação e cidade é
  coletado de forma independente.
- **Se quase tudo falhar, nada é publicado.** Abaixo do mínimo de notícias
  (`min_articles`), de feeds ok (`min_sources_ok` e `min_sources_ratio`, a
  fração dos feeds) ou de veículos em português (`min_pt_sources_ok`), a
  execução para com erro e a edição anterior continua no ar — inclusive o
  `latest.json`, então a rotina de e-mail não reenvia nada.
- **Sem IA, o jornal sai do mesmo jeito**, no modo automático (títulos e resumos
  dos próprios veículos, sem tradução).

---

## Onde ler

| O quê | Endereço |
|---|---|
| Edição do dia | <https://pbcvphyton.github.io/TriangulaTech/> |
| Arquivo (edições anteriores) | <https://pbcvphyton.github.io/TriangulaTech/edicoes/> |
| Uma edição específica | `https://pbcvphyton.github.io/TriangulaTech/edicoes/AAAA-MM-DD.html` |
| Todas as notícias de um dia | `https://pbcvphyton.github.io/TriangulaTech/edicoes/AAAA-MM-DD-todas.html` |
| Analítico Político (como a imprensa cobre a política: manchetes, enquadramento e termômetro) | <https://pbcvphyton.github.io/TriangulaTech/edicoes/analitico.html> |
| Pesquisa analítica (busca em todas as edições) | <https://pbcvphyton.github.io/TriangulaTech/edicoes/pesquisa.html> |
| Congresso (o que cada deputado e senador vota, propõe e deixa parado) | <https://pbcvphyton.github.io/TriangulaTech/edicoes/congresso.html> |
| O dossiê de um parlamentar | `https://pbcvphyton.github.io/TriangulaTech/edicoes/congresso.html#p=cd-<id>` (deputado) ou `#p=sf-<código>` (senador) |

Cada matéria tem endereço próprio (`…#s-<id>`) e, clicada, abre na tela inteira
(no computador, no tablet e no celular). Os links do e-mail apontam para a
cópia arquivada (`edicoes/AAAA-MM-DD.html#s-<id>`), que continua válida depois que a
capa muda no dia seguinte. O
arquivo guarda as edições dos últimos 400 dias (`archive_keep_days` em
`config/site.yaml`).

> **Primeira vez?** Em *Settings → Pages*, escolha *Deploy from a branch*,
> branch `main`, pasta `/ (root)`.

---

## A home: três andares, cada um com teto

A home (`index.html`) é a primeira página de um jornal, não o jornal inteiro: uma
manchete, uma lista numerada e um resumo de como a mídia cobriu o dia. O que é
detalhe fica a um clique, na janela de cada matéria e na cópia arquivada da edição.
É uma capa só no código, que se rearruma pela largura da tela (não há uma versão
para computador e outra para celular). De cima para baixo:

1. **Cabeçalho.** Marca, data e hora da edição e os botões (Analítico, Congresso,
   Tribunais, Edições, Buscar, Escuro). No celular (até 640 px), o logo e uma
   fileira só de botões: as abas com o nome escrito, as ferramentas com o ícone
   (o nome segue para o leitor de tela); o lema desce para o rodapé.
2. **Linha de indicadores.** As cotações, sem rolagem automática e sem botão de
   pausa; quem quer ver as demais arrasta para o lado. O clima da primeira cidade
   configurada é o último item da linha (some no celular).
3. **Manchete.** Editoria, título (abre a janela de leitura), linha fina, a foto,
   quando há (ao lado do texto; no celular, uma miniatura), e uma linha de
   serviço: quantos veículos, há quanto tempo e, se o assunto teve veículos dos
   dois lados, a marca de divergência. Nenhum parágrafo do corpo. O título nunca
   é cortado: a letra é que diminui com o comprimento (a classe sai de `web.py`).
4. **Em 1 minuto.** Uma lista numerada com 5 chamadas: as 3 secundárias e os 2
   primeiros destaques (se faltar, as próximas matérias na ordem das seções).
   Cada matéria aparece uma única vez neste andar (manchete e 5 chamadas). A
   linha fina de cada chamada, em uma linha, só aparece em tela larga.
5. **Agenda da semana** (reservado: o bloco entra neste ponto do modelo).
6. **Como a mídia cobriu.** Uma linha de abertura, só para a marca com
   termômetro e quando ele rodou no dia ("Viés político das notícias de hoje:
   equilibrado. Ver a análise", sem número; só cresce quando algum texto sai da
   faixa de equilíbrio, e aí lista até 3), e duas colunas:
   - **Onde os veículos divergiram:** até 3 assuntos com veículos dos dois lados
     e 3 veículos ou mais, cada um com o título e os dois lados em palavras, com
     a conta ("3 veículos: critica a falta de ambição. 1 veículo destoou: destaca
     os limites do consenso. 11 sem lado."). Só se chama divergência quando o
     lado menor tem 2 veículos ou mais; com 1, ele "destoou". Sem nenhum assunto
     assim, uma frase diz que os veículos relataram tudo de forma semelhante.
   - **Mais recentes até HHhMM** (a hora da edição, porque a lista não se
     atualiza): 5 notícias no computador e 3 no celular, com hora, veículo e
     título, e o link para todas as notícias do dia.

   Edição em modo automático não tem cobertura comparada: o bloco fica só com
   "Mais recentes". Marca sem termômetro não tem a linha do viés.
7. **Todas as N matérias, por editoria.** O único bloco recolhido da home: uma
   linha quando fechado. Aberto, traz a busca do dia, as abas (a primeira é
   "Todas") e os cartões, só com editoria, título e linha fina. O atalho `/` e o
   link "Todas as N matérias" abrem o bloco.
8. **Rodapé.** A linha de método ("Compilada de N notícias de M veículos. Como
   foi feita"), as edições anteriores, o Instagram, o código-fonte, o estado das
   fontes (recolhido) e o lema.

**Onde mora o detalhe.** A cópia arquivada de cada edição
(`edicoes/AAAA-MM-DD.html`) é a mesma página com três gavetas fechadas depois do
bloco das matérias, com o conteúdo completo por dentro: `#cobertura` (a cobertura
comparada, assunto por assunto e veículo por veículo), `#termometro` (os painéis
do termômetro e cada texto analisado) e `#racional` (como a edição foi feita). A
home não carrega as gavetas: os links "Ver os N assuntos comparados", "Ver a
análise" e "Como foi feita" apontam para a cópia do dia. Quem chega com uma dessas
âncoras no endereço (da home, do Analítico, da Pesquisa ou de um e-mail antigo)
encontra a gaveta aberta; as âncoras das matérias (`#s-<id>`) abrem a janela de
leitura, como sempre.

**O que saiu da home e para onde foi.** O editorial do dia (continua na descrição
da página, no e-mail e no Instagram), o corpo da manchete, as listas de veículos e
as barras da cobertura (janela da matéria), o resumo em prosa (e-mail e
Instagram), "Mais para ler" e "Destaques" (dois deles em "Em 1 minuto", os demais
no bloco das matérias), o Radar (5 itens em "Mais recentes"; o resto em "Todas as
notícias do dia"), a navegação de editorias, a faixa do clima, a barra de
progresso de leitura e o botão de voltar ao topo (continuam nas outras páginas), o
racional, a cobertura completa e os painéis do termômetro (gavetas da cópia
arquivada). O JSON da edição não mudou: e-mail e Instagram saem iguais.

**Tetos.** São constantes de `qijournal/render/web.py` (`TETO_CHAMADAS` = 5,
`TETO_DIVERGENCIAS` = 3, `TETO_RECENTES` = 5, `RECENTES_NO_CELULAR` = 3) e de
`qijournal/render/termometro.py` (`VIES_FORA_MAX` = 3): o gerador corta por eles,
então a publicação diária nunca é barrada. `tests/test_home_orcamento.py` trava o
que dá para contar sem navegador, com três edições reais reduzidas (uma delas
automática): itens por bloco, até 1.000 palavras visíveis com tudo fechado, até 12
controles e 70 links à vista, `index.html` de até 450 KB, nenhum termo de bastidor
("blocos por editoria", "parte 1 de 2", "chamadas à IA") e nenhuma animação além
da janela da matéria abrindo. Nada se move sozinho na página da edição.

**Desenho.** Os blocos da home usam um sistema só, em `styles.css.j2`: uma régua
(`--fio`), cinco tamanhos de letra (`--f-man`, `--f-tit`, `--f-txt`, `--f-min`,
`--f-rot`), dois pesos (400 e 700) e o laranja só na marca de divergência. Um
bloco novo segue o mesmo molde: `<section class="bl">`, o título em
`<h2 class="rot">` e o link do pé em `<p class="mais">`.

**Medição** (edição de 23/11/2026, em 24/11/2026, com o Chromium; página com tudo
fechado, os três botões das abas no cabeçalho e uma foto de teste na manchete,
porque o ambiente de medição não carrega as fotos dos veículos):

| | Antes | Agora | Meta sem a agenda |
|---|---|---|---|
| Computador, 1440 × 900 | 5.630 px | 1.550 px | até 1.700 px |
| Celular, 390 × 844 | 12.683 px | 2.377 px | até 2.700 px |
| Topo do título da manchete no celular | 900 px | 188 px | até 240 px |
| Cabeçalho no celular (com as três abas) | 203 px | 116 px | até 120 px |
| Palavras visíveis | 3.289 | 612 | até 1.000 |
| Controles visíveis | 55 | 3 | até 12 |
| `index.html` | 858 KB | 281 KB | até 450 KB |

A manchete e as 5 chamadas cabem inteiras na primeira tela do computador (terminam
a 784 px do topo). Sem a foto da manchete, a página fica mais curta (1.420 px no
computador). A altura em pixels não entra nos testes automáticos: é medida à mão a
cada mudança do modelo, e os números novos entram nesta tabela.

---

## Ativar a edição por IA

Os editores por IA ficam numa **cadeia**, na ordem de `llm.providers` em
`config/site.yaml`. Cada um só entra se o segredo dele existir no GitHub:

| Ordem | Editor | Segredo | Custo e limite |
|---|---|---|---|
| 1 | [Gemini](https://aistudio.google.com/) (`gemini-flash-latest`) | `GEMINI_API_KEY` | grátis (Google AI Studio, modelos Flash); limites por minuto e por dia |
| 1b | Gemini Flash-Lite (`gemini-flash-lite-latest`) | `GEMINI_API_KEY` (a mesma) | grátis; refaz a chamada quando o Flash está sobrecarregado (503) |
| 2 | [AIML API](https://aimlapi.com/) (`openai/gpt-5-5`) | `AIMLAPI_KEY` | grátis; 10 requisições por hora |
| 3 | [SenseNova](https://platform.sensenova.ai/) (`sensenova-6.8-flash-lite`) | `SENSENOVA_API_KEY` | grátis (beta); 1.500 requisições a cada 5 horas |
| 4 | [Mistral](https://mistral.ai/) (`mistral-large-latest`) | `MISTRAL_API_KEY` | grátis (*Experiment*); poucas requisições por minuto |
| 5 | [Kimi](https://platform.kimi.ai/) (`kimi-k3`) | `MOONSHOT_API_KEY` | pago (recarga mínima de US$ 1) |
| 6 | [Claude](https://www.anthropic.com/) (`claude-opus-5-5`) | `ANTHROPIC_API_KEY` | pago |

O Gemini é o mesmo do jornal **TSA Tech** (`pbcvphyton/pbcvphyton-tsa-tech`), que
já sai com IA por ele: basta cadastrar aqui o segredo `GEMINI_API_KEY` com a
mesma chave (o GitHub não deixa um repositório ler o segredo de outro). Com a
mesma chave, os dois jornais dividem os limites diários do plano grátis do
Google; uma chave de outro projeto do Google AI Studio tem limites próprios.

**Quando uma IA estoura o limite, a seguinte assume**, chamada a chamada, até
toda a demanda ser compilada: se o editor da vez esgotar o teto de requisições
da edição (ex.: as 10 por hora da AIML), receber 429 que persiste, ficar sem
cota ou tiver a chave recusada, ele sai da cadeia e a mesma chamada é refeita
no seguinte. Outros erros (resposta cortada, JSON inválido) refazem só aquela
chamada no seguinte. Se nenhum conseguir, a edição sai no modo automático e a
execução no GitHub mostra um aviso amarelo explicando o motivo.

Para ativar um editor: crie a chave no site dele e, no GitHub, *Settings →
Secrets and variables → Actions → New repository secret*, com o nome da tabela
e a chave como valor. **Nunca** coloque a chave no código ou em arquivos do
repositório (ele é público). O modelo de cada um pode ser trocado pela variável
`QIJ_<NOME>_MODEL` (aba *Variables*): `QIJ_GEMINI_MODEL`, `QIJ_GEMINI_LITE_MODEL`,
`QIJ_AIML_MODEL`, `QIJ_SENSENOVA_MODEL`, `QIJ_MISTRAL_MODEL`, `QIJ_KIMI_MODEL` e
`QIJ_MODEL` (Claude). Os limites de cada
um (janela de contexto, saída máxima, teto de requisições, chamadas simultâneas)
ficam em `llm.apis` no `config/site.yaml`, com os padrões em
`qijournal/config.py` (`DEFAULT_APIS`).

### Compilação por editoria (modo "blocos")

Todas as notícias do dia (1.000 a 1.500) são divididas em **blocos por
editoria** (`llm.block_groups`), e cada bloco vai numa chamada:

1. Economia & Mercados · 2. Política & Justiça · 3. Empresas, Tecnologia &
   Imobiliário · 4. Mundo & Natureza · 5. Esporte, Cultura & Variedades

Em cada bloco, a IA lê todas as notícias dele, **une as que tratam do mesmo
assunto**, faz a **análise da cobertura** (abaixo) e **redige as matérias mais
importantes**. Uma **chamada de fechamento** recebe o que todos os blocos
produziram, junta assuntos que se repetiram entre blocos, escolhe as matérias da
edição (`target_stories`) e a manchete e escreve o editorial e o "Em 1 minuto".

O limite de cada chamada é respeitado: um bloco que não cabe na entrada
(janela de contexto do modelo menos a saída) ou na saída máxima do modelo é
dividido em partes de tamanho igual, até **9 chamadas no total** (`max_calls`,
contando o fechamento). Com a cadeia, os blocos são planejados pelo menor limite
entre os editores ativos, para que qualquer um deles consiga assumir qualquer
bloco. Uma edição típica usa 6 chamadas (5 blocos + fechamento).

O modo alternativo `etapas` (`llm.apis.<nome>.mode: etapas`) faz agrupamento,
pauta, redação e cobertura em chamadas separadas (10 a 15 por edição).

### Análise da cobertura (o analítico)

Para cada assunto coberto por dois ou mais veículos, a IA devolve:

- os **dois lados do debate**, como enfoques neutros ("Destaca o risco fiscal" ×
  "Destaca a arrecadação recorde") — ou nenhum, quando todos relatam o fato do
  mesmo jeito;
- para **cada veículo**, o lado que ele seguiu (A, B ou neutro) e a
  **interpretação do foco dele**: o que priorizou, que enquadramento deu (tom,
  personagens, dados que destacou ou deixou de lado em comparação com os
  outros) e por que isso o põe naquele lado;
- a **conclusão**: em que sentido a cobertura seguiu e quem destoou.

No site, um **medidor** mostra cada veículo como um trecho da barra (lado A à
esquerda, neutros no meio, lado B à direita): a barra cresce para o lado com
mais veículos, com o rótulo "Pende para: …". Ele aparece na janela da matéria
(com a conclusão e a **análise por veículo**) e na gaveta **Cobertura
comparada** da cópia arquivada da edição. Na home ficam só a marca de divergência
na chamada ("veículos divergem" ou "1 veículo destoou", quando o assunto teve
veículos dos dois lados) e o bloco **Onde os veículos divergiram**, com até 3
assuntos e os dois lados em palavras. No e-mail, cada matéria ganha a barra e a
conclusão. A análise usa só o que os veículos publicaram; quando o trecho não
permite dizer, o veículo fica como neutro.

A gaveta **Cobertura comparada** (`edicoes/AAAA-MM-DD.html#cobertura`) reúne todos
os assuntos do dia vistos por dois ou
mais veículos — as matérias da edição com análise e os demais assuntos — em
**abas por editoria**, no mesmo desenho das abas das matérias (Brasil · Economia,
Mercados & Finanças, Política, Esporte…, e "Todas"): cada assunto entra na aba da
editoria dele, e cada aba mostra quantos assuntos tem e quantos dividiram os
veículos ("3 div."). Em cada aba:

- **Onde a mídia divergiu**: os assuntos com veículos dos dois lados, primeiro os
  mais disputados (mais veículos no lado menor; depois o mais equilibrado), com a
  divergência de cada um (2 × o lado menor ÷ (A + B): 100 = meio a meio) e o link
  para a matéria, quando é da edição;
- **Relataram de forma semelhante**: o resto, em lista curta (clicar abre a
  conclusão e a análise por veículo).

A home leva a ela pelo link "Ver os N assuntos comparados"; o Analítico e a
Pesquisa continuam apontando para a mesma âncora.

### Termômetro de Viés Editorial e Analítico Político (só Triangula)

Depois da edição, os textos compilados nas matérias do dia passam pelo
**Termômetro de Viés Editorial, versão 1.1** — a especificação em
`termometro-referencia-v1.3`, que é o pilar desta parte. Ele é **estritamente
adicional**: a análise da cobertura acima continua igual, e o termômetro aparece
em três lugares: na home, numa linha em palavras ("Viés político das notícias de
hoje: equilibrado", sem número; ver [A home](#a-home-três-andares-cada-um-com-teto));
na gaveta **Termômetro do dia** da cópia arquivada de cada edição
(`edicoes/AAAA-MM-DD.html#termometro`), com o painel completo; e na aba
**Analítico Político** (botão
*Analítico* no cabeçalho, ao lado de *Edições* e *Buscar*). A marca PBCV
Advogados não tem nada disso (`termometro.marcas` em `config/site.yaml`).

Como cada texto é analisado:

1. **Texto**: das matérias da edição e dos assuntos da **cobertura comparada**
   (política, jurídico, economia, mundo, mercados, natureza, tecnologia e
   imobiliário, nessa prioridade; esporte e variedades ficam de fora; as matérias
   antes dos assuntos), um veículo de cada matéria ou assunto por vez, e em
   rodízio também por veículo: o 1º texto de cada veículo vem antes do 2º de
   qualquer um, para o termômetro alcançar o maior número de veículos. Cada
   análise guarda a origem (`materia` ou `cobertura comparada`). O texto segue o
   formato da especificação: `P0` = título, `P1` = linha fina e o corpo extraído da
   página original (até 8.000 caracteres, com as falas curtas; o pedido avisa quando foi cortado). Texto
   com menos de 150 palavras não vai à IA (seria inconclusivo). Só português.
2. **IA**: o pré-processamento da referência (`preparar`: falas diretas entre
   aspas e termos do dicionário) vai no pedido; a mesma cadeia de IAs da edição
   (o que a edição já gastou do limite de cada uma continua contando) devolve a
   saída no formato v1.1 (triagem, atores, fontes, enquadramento, evidências com
   citação literal, teste de inversão e qualidade), 5 textos por chamada, até **60
   textos** e 15 chamadas por edição, em até 12 minutos (`termometro` em
   `config/site.yaml`; até out/2026 eram 20 textos). Cada edição tem de 140 a 380
   textos candidatos; até 180 páginas originais são consultadas atrás de corpo
   suficiente (cerca de 1 em 4 é curta demais).
3. **Cálculo**: cada saída é validada contra `config/saida_modelo.schema.json` e
   passa pelo cálculo de referência **sem nenhuma alteração**
   (`qijournal/termometro/referencia.py`, cópia literal): ele confere cada citação
   no texto, aplica a regra da voz, os pesos dos 11 critérios e dá **T** (−100 a
   +100), o rótulo, a confiança e a **qualidade da apuração Q** (0 a 100).
4. **Registro**: além do T e dos sinais que pontuaram (citação, critério, lado,
   contribuição, intensidade, certeza e o ator favorecido ou desfavorecido), cada
   análise guarda o que a IA já devolve e o cálculo confere: o checklist da
   qualidade item a item, o enquadramento (problema, causa, quem sofre,
   responsável, solução e valor moral), o teste de inversão, os atores, as fontes
   (com as palavras de fala direta e quem tem a última palavra) e quantos pontos de
   T cada critério somou. Análises anteriores a nov/2026 não têm esses detalhes.

Na cópia arquivada da edição, a gaveta **Termômetro do dia** é um painel com:

- **placar**: o T médio das notícias, o rótulo e o intervalo de 95%, ao lado da
  escala de −100 a +100 com as sete faixas (a do dia em destaque), o intervalo, o
  ponteiro da média e um traço por notícia;
- **indicadores**: textos analisados (veículos e editorias), notícias com T,
  qualidade média da apuração e sinais que pontuaram (à esquerda, à direita e de
  equilíbrio);
- **painéis**: a distribuição das notícias por faixa, onde estão os sinais (por
  critério e lado) e a qualidade da apuração (em quantas notícias cada boa prática
  aparece); e o T médio do dia **por veículo** e **por editoria**, com o intervalo;
- **cada texto analisado**: uma linha por texto (T, veículo, título, escala,
  rótulo, confiança e qualidade), com filtros (com sinais, fora do equilíbrio,
  opinião, veículo, editoria) e ordem (mais inclinados, da esquerda à direita, menor
  qualidade, mais sinais, veículo). Clicar abre o detalhe: o resumo do cálculo,
  quantos pontos cada critério somou, os sinais com a citação literal, o
  enquadramento, o teste de inversão, a qualidade item a item, quem fala e a ficha
  do texto, com o link para o original.

Opinião fica fora da média (aparece à parte). As médias do dia, por veículo e por
editoria saem da agregação da especificação (`agregacao.resumo`); o painel só conta
o que está guardado, sem recalcular nada.

A aba **Analítico Político** (`edicoes/analitico.html`) responde a uma pergunta só:
**como a imprensa cobre a política**. Ela se organiza pelo que os dados têm a dizer, não
pela ordem em que foram calculados: abre com a leitura, desce pelas três lentes e guarda
a base completa do termômetro, recolhida, no fim. Só vira afirmação o que passa do
acaso contando todas as comparações do mesmo tipo feitas na página (correção de Holm:
com 12 nomes ou dezenas de pares, uma ou outra diferença passaria do acaso só por
sorte); o resto aparece, mas quieto.

**A leitura** (`qijournal/render/achados.py`). A manchete é o achado de maior peso; logo
abaixo vêm a prova dela (os números e o link para onde ela está na página), a linha fina
(sempre o que o termômetro diz do texto) e **mais achados**, numerados, um por tipo:

| Achado | Entra quando | De onde sai |
|---|---|---|
| Tom | o tom das manchetes sobre um nome piorou ou melhorou nas últimas 7 edições (95%, contando todos os nomes comparados) | manchetes |
| Espaço | um nome ganhou ou perdeu espaço nas manchetes além do acaso (duas proporções, 99%, contando todos os nomes comparados) | manchetes |
| Veículos | um veículo é mais crítico ou mais favorável a um nome que os demais (99%, contando todos os pares veículo × nome) | manchetes |
| Distância | o tom dos dois nomes mais citados difere além do acaso (99%) | manchetes |
| Presença | sempre que há nomes: quem domina as manchetes (o que sobra quando nada passa do acaso) | manchetes |
| Lados | um par de veículos fica do mesmo lado, ou em lados opostos, além do acaso (99%, contando todos os pares); senão, quantos assuntos racharam | enquadramento |
| Texto | sempre: o veredito do termômetro (vai para a linha fina) | texto |
| Texto, os desvios | as notícias que saem do zero pendem para um lado além do acaso (teste de sinal, 99%) | texto |
| Texto, o mais inclinado | há texto fora de "Equilibrado" nas últimas 7 edições | texto |
| Tendência | a tendência mensal ou a janela recente do termômetro passou do acaso (95%, contando todos os testes de tendência) | base completa |
| Apuração | sempre que há nota de qualidade | texto |

O peso de cada achado é a força do efeito vezes a importância do nome (a raiz da parte
das manchetes dele sobre a do nome mais citado), sobre uma base por tipo; dá para refazer
a ordem à mão com os números mostrados. Ao lado ficam **as lentes** (com a base de cada
uma) e **o que mudou em sete edições**: todas as mudanças de espaço e de tom que
passaram do acaso contando todos os nomes comparados, da maior para a menor.

**1 · Manchetes — de quem a imprensa fala, e em que tom** (`qijournal/render/midia.py`):

- **Placar dos nomes**: os 12 nomes mais citados nas manchetes de Política nas edições
  dos últimos 30 dias (pessoas, partidos, instituições; saem das manchetes em português,
  e palavra comum com maiúscula, como "Eleições", ou selo como "VÍDEO" não conta). Uma
  linha por nome: a parte das manchetes, a curva das 30 edições (as últimas 7 em
  destaque), a variação (últimas 7 edições contra as 7 anteriores), o tom (quanto das
  manchetes brasileiras o põe em contexto desfavorável e favorável, e o saldo), o tom
  antes e agora e quem destoa. A variação de espaço e a de tom só ganham a marca escura
  quando passam do acaso contando todos os nomes comparados; a mudança de tom que passa
  só na comparação isolada fica com o selo claro. Os números são contados como no analítico do nome na Pesquisa
  (mesma regra de busca, todos os veículos, a notícia uma vez).
- **Dossiê do nome**: clicar na linha abre **quem fala de quem, e como**: por que um
  veículo fala tanto dele (manchetes do veículo contra as esperadas pelo que ele publica
  na editoria, quantas edições e o assunto que mais rendeu), o que costuma falar e se
  fala bem ou mal (desfavoráveis, favoráveis, o saldo do veículo contra o dos demais e
  quantas vezes o nome é alvo de crítica, acusação ou punição) e se há padrão (semana a
  semana, de que lado dos demais o veículo fica); frases automáticas com o que importa,
  as manchetes mais desfavorável e mais favorável com as palavras que pesaram, os
  assuntos da cobertura comparada que dividiram os veículos e o link para o analítico
  completo do nome na Pesquisa, com a mesma editoria e período. A caixa "Analisar outro
  nome" leva qualquer termo para lá.
- **Veículo por veículo**: a matriz veículo × nome, com o saldo do tom de cada veículo
  sobre cada nome; a cor acompanha o saldo, o contorno marca o veículo que difere dos
  demais na comparação isolada (99%, como na Pesquisa) e o ponto, o que tem menos de 10
  manchetes. Logo abaixo, a página diz o que continua de pé contando todas as
  comparações da tabela.

**2 · Enquadramento — onde a imprensa rachou** (`qijournal/render/lados.py`). Entram os
assuntos de Política que dois ou mais veículos cobriram, nas matérias da edição e nos
demais assuntos da cobertura comparada, das mesmas 30 edições (lidos de
`data/AAAA-MM-DD.json`):

- **os assuntos que mais dividiram** nas últimas 7 edições e no período: os dois
  enfoques, os veículos de cada um e a divergência, com o link para a matéria ou para a
  Cobertura comparada da edição;
- **quem fica com quem**: para cada par de veículos, em quantos assuntos divididos os
  dois tomaram o mesmo lado, contra o que o acaso daria;
- **quem destoa da maioria**: em quantos assuntos com maioria cada veículo ficou no lado
  menor, também contra o acaso.

O acaso não é "metade das vezes": num assunto rachado em 2 × 2, dois veículos só caem do
mesmo lado 1 vez em 3. Cada contagem é comparada com a esperada dado o placar de cada
assunto (os lados embaralhados entre os veículos que tomaram lado) e só vira afirmação
quando passa de 99% contando todos os pares (ou todos os veículos) comparados. Um
assunto acompanhado em várias edições conta uma vez por edição. Os lados são os enfoques de cada assunto, não "a favor" ou "contra"
alguém.

**3 · Texto — como as reportagens escrevem** (o termômetro):

- **o que o termômetro diz**, em linguagem clara (hoje, que o texto das notícias sai
  equilibrado: quantas ficaram "Equilibrado", quantas tiveram sinal de tratamento
  desigual e para que lado, e quantos sinais foram de equilíbrio), com o aviso quando a
  amostra está concentrada em poucos veículos; o placar da média geral e os indicadores;
- **os desvios**: o histograma das notícias com T diferente de zero (as que ficam
  exatamente em zero vão no centro, fora das colunas) e o teste de sinal: elas pendem
  para um lado além do que uma moeda daria?
- **os textos mais inclinados** das últimas sete edições, com o sinal que mais pesou em
  cada um (a citação literal e a explicação), os recordes do histórico e a opinião, à
  parte;
- **onde aparecem os sinais** (o mesmo painel do Termômetro do dia, por critério e lado,
  sobre todo o histórico) e o **tratamento desigual por veículo** (a parte das notícias
  de cada um com algum sinal à esquerda ou à direita);
- **qualidade da apuração**: quantas notícias em cada faixa (boa, regular, fraca), os
  veículos com amostra (30 ou mais notícias com T) à vista, os demais num "ver mais" e,
  nas análises que guardam o checklist, a qualidade item a item.

As parcelas não arredondam para "tudo ou nada": 452 de 453 aparece como 99,8%, não 100%.

**Base completa do termômetro**, bloco a bloco, cada um fechado e com o que tem dentro
escrito no título (`qijournal/termometro/agregacao.py`, conferida pelos testes da seção
14.7 da especificação): a distribuição por faixa, para onde estamos indo (tendência
mensal de T e de Q, a janela recente comparada ao histórico e quem puxou a mudança, por
editoria e por veículo), as séries semanais, os rankings por veículo e por editoria (T
ajustado; nas editorias, mínimo de 30 notícias; nos veículos entram todos os que têm
notícia com T, e os com menos de 30 aparecem esmaecidos, como amostra pequena — o
cálculo da agregação não muda), os recortes por eixo, a tabela de cada dia e a lista das
análises (clicar na linha abre o texto original, para conferir; com o script, a lista
ganha um filtro por texto e por T diferente de zero). Um bloco da base sobe sozinho
quando tem o que dizer: uma tendência ou um desvio que passa do acaso abre o bloco e
vira achado na leitura. A lista mostra as 1.200 análises mais recentes (`LISTA_MAX` em
`qijournal/render/analitico.py`), para a página não passar de alguns MB com 60 análises
por dia; as mais antigas ficam na Pesquisa analítica (tipo Termômetro, com CSV), e todos
os números da aba usam o histórico inteiro.

**Como ler**, no fim, reúne o método de cada lente. A navegação fica no alto e marca a
seção da vez; os achados e a matriz levam ao bloco da prova (abrindo-o, se estiver
fechado) e as marcas dos gráficos mostram a dica ao passar o mouse. Tudo isso é reforço:
sem script, a página continua completa. O termômetro e o cálculo da aba não mudam.

#### Tom das manchetes (`qijournal/render/tom.py`)

Lido por palavras, sem IA, com a mesma regra no servidor e no navegador
(`tom.js.j2`), em cada manchete brasileira que cita o nome:

1. palavras de contexto **desfavorável** (escândalo, investigação, prisão,
   derrota, crise, rejeição, crítica recebida…) ou **favorável** (vitória, apoio,
   aprovado, lidera, cresce, elogio…) contam para o nome **mais próximo** delas no
   título (até 6 palavras; no empate, o nome que vem depois, como em "fuga de
   Flávio"); depois de "não", "sem" ou "nem", não contam;
2. **ações contra alguém** ("critica", "acusa", "condena", "investiga",
   "suspende", "contra"…) põem o nome que vem depois como **alvo** (conta como
   desfavorável) e o que vem antes como quem age (não pesa no tom dele);
3. a manchete é desfavorável ao nome com mais palavras negativas que positivas,
   favorável no contrário, neutra sem nenhuma e mista no empate. **Saldo** =
   favoráveis − desfavoráveis, em pontos (−100 a +100).

"Mais crítico" e "mais favorável" só aparecem quando o veículo (10+ manchetes do
nome) difere dos demais além do que o acaso explica (z ≥ 2,58 com a variância dos
dois grupos juntos); a tendência do tom compara as últimas 7 edições com as 7
anteriores (20+ manchetes em cada, z ≥ 1,96); "padrão" é ficar do mesmo lado dos
demais em 3 de cada 4 semanas comparáveis (3+ manchetes do veículo e 10+ dos
demais). No Analítico, o espaço de um nome só "mudou" quando a parte das manchetes das
últimas 7 edições difere da das 7 anteriores além do acaso (duas proporções, z ≥ 2,58), e
o par vermelho × verde do tom foi conferido com um validador de paleta, no claro e no
escuro (separação para daltônicos e contraste): a cor nunca vai sozinha, o sinal do
saldo ou a posição (esquerda × direita) a acompanha. **Limite**: o tom mede o contexto em que o nome aparece nas manchetes,
não a opinião do veículo, e a leitura por palavras erra em parte delas (ironia,
negação, o nome errado na frase); por isso o que diz algo é a comparação entre
veículos sobre o mesmo nome, e as manchetes de exemplo mostram as palavras que
pesaram. O dicionário fica em `qijournal/render/tom.py` (`NEGATIVO`, `POSITIVO`,
`CONTRA`).

- **Histórico**: as análises de cada dia ficam em `data/termometro/AAAA-MM-DD.json`
  e **nunca são apagadas** (a limpeza do arquivo de edições não toca nessa pasta).
  Um texto já analisado não volta à IA: é reaproveitado e conta uma vez só.
- **Dicionário e âncoras**: `config/lexico.yaml` (vocabulário codificado; hoje com
  os termos do caso de referência) e `config/atores.yaml` (campo fixo de partidos e
  grupos; vazio — sem âncora, a IA usa a filiação e a posição que o texto informa).
- **Precisa de IA**: sem nenhum segredo de IA configurado (o mais simples é o
  `GEMINI_API_KEY`, o mesmo do TSA Tech), a edição sai com "Sem análise nesta
  edição: nenhuma IA disponível nesta execução".
- **Desligar** sem mexer no código: variável `QIJ_NO_TERMOMETRO` = `1` no GitHub
  (*Settings → Secrets and variables → Actions → Variables*).
- **Testes da especificação**: `tests/termometro/` guarda os scripts e casos como
  vieram (`testes.py`, `testes_ranking.py`); `tests/test_termometro_especificacao.py`
  roda os dois e exige `FALHAS: nenhuma`.

### Pesquisa analítica (busca em todas as edições)

O botão **Buscar** do cabeçalho (em todas as edições, no arquivo e no Analítico)
abre a **Pesquisa analítica** (`edicoes/pesquisa.html`). A caixa de busca da
edição continua filtrando as matérias do dia enquanto se digita; **Enter** (ou
*Todas as edições*) leva o termo para a Pesquisa.

- **O que entra**: as matérias (título, linha fina, texto e "por que importa"),
  todas as notícias coletadas (título, veículo e link), a cobertura comparada
  (assunto, conclusão e o foco de cada veículo) e as análises do Termômetro, de
  todas as edições do arquivo.
- **Como busca**: sem acento e sem diferenciar maiúsculas; todas as palavras
  precisam aparecer; `"frase exata"`; `-palavra` exclui; palavras de até 3 letras
  (STF, PT, IA) valem só inteiras, e as demais casam no começo da palavra ("juro"
  acha "juros"; "lula" não acha "célula"). A mesma notícia coletada em mais de uma
  edição aparece uma vez.
- **Filtros**: tipo, período das edições (com atalhos de 7 e 30 dias), editoria,
  veículo, ordem (recentes, relevantes, antigas) e "só nos títulos".
- **Resultados**: o cabeçalho diz quantos resultados de cada tipo; **as histórias**
  (os assuntos com mais veículos nos resultados, com as manchetes de cada um) vêm
  antes da lista; as contagens por edição, veículo e editoria (clicar filtra) ficam
  em "Refinar", recolhido.
- **Na prática**: cada resultado leva à matéria na edição (abre na tela inteira)
  ou à notícia original; a busca fica no endereço (`pesquisa.html?q=…&de=…`), que
  pode ser guardado ou enviado; **Baixar CSV** exporta todos os resultados.
- **Dados**: cada edição ganha `data/busca/AAAA-MM-DD.json`, compacto, gravado no
  `publish` (os dias que ainda não têm o arquivo são criados a partir de
  `data/AAAA-MM-DD.json`) e apagado junto com a edição (`archive_keep_days`). A
  página carrega só os dias do período pedido, das edições mais recentes para as
  mais antigas, e busca no próprio navegador.

#### A leitura da busca (o padrão do Analítico)

Ao fim de cada busca, a Pesquisa abre com **a leitura**, no mesmo padrão da aba
Analítico: a manchete é o achado de maior peso, com a prova (os números e o link
para onde ela está na página); a linha fina diz quanto o termo aparece e o que o
termômetro diz do texto; logo abaixo vêm os demais achados, numerados, um por tipo;
ao lado, as lentes e **o que mudou em sete edições** (espaço e tom). Depois vêm os
resultados e as três lentes; a base completa (as tabelas) e o **Como ler** ficam no
fim. `#midia` (`pesquisa.html?q=Lula#midia`) abre direto na leitura. Ela usa os
mesmos resultados e filtros e, como base, todas as notícias das edições do período
(precisa de todas: se alguma não carregar, pede para tentar de novo); a lista não
espera por ela.

Só vira afirmação o que passa do acaso contando as comparações da mesma família
(correção de Holm), com os limites da aba Analítico (`achados.py` e `lados.py`, no
manifesto da página):

| Achado | Entra quando |
|---|---|
| Tom | o tom das manchetes sobre o termo piorou ou melhorou nas últimas 7 edições (95%; um termo só e 20+ manchetes brasileiras com palavra de tom) |
| Espaço | a parte das manchetes que citam o termo mudou nas últimas 7 edições contra as 7 anteriores (duas proporções, 99%, e mais de 15%) |
| Pico | um dia tem uma parte das manchetes acima da dos outros dias do período (binomial, Holm a 99% sobre todas as edições; 5+ manchetes e 1,5 vez a parte dos outros dias); a história do dia vai junto |
| Veículos | um veículo põe o termo em contexto mais crítico ou mais favorável que os demais (99%, Holm sobre os veículos comparados) |
| Atenção | um veículo cita o termo bem mais ou bem menos que o esperado pela parte do termo nas manchetes brasileiras de cada editoria em que ele publica (Poisson exato, Holm a 99%; 1,5 vez o esperado com 5+ manchetes, ou metade; só com 20+ manchetes) |
| Exterior | os veículos de fora do Brasil dão ao termo uma parte bem diferente da dos brasileiros (99%; 10+ manchetes de cada lado e 1,5 vez de diferença) |
| Lados | um par de veículos fica do mesmo lado, ou em lados opostos, além do acaso (teste exato, Holm a 99%); senão, quantos assuntos racharam |
| Texto | as notícias com o termo que saem do zero no termômetro pendem para um lado (teste de sinal, 99%) |
| História, pauta e presença | contagem: a história com mais veículos, o nome que mais acompanha o termo e quanto ele aparece |

Com até 10 resultados, a página só descreve o que achou (e avisa quando eles se
espalham por editorias diferentes). Num período de 7 a 13 edições, as edições logo
antes dele completam as duas janelas de sete (só para essas contas). Quando a busca
reproduz a base de um nome do placar do Analítico (mesmo termo, sem diferenciar
maiúsculas e acentos, editoria e período, sem outros filtros), valem os vereditos de lá
no espaço, no tom e no tom de cada veículo; os demais achados (picos, atenção, histórias,
lados) são da Pesquisa, com as comparações dela. Com o tipo "Todas as notícias"
desmarcado, a leitura diz que a busca está filtrada (e oferece incluí-las), em vez de
medir espaço e tom.

- **1 · Manchetes**: o espaço edição a edição (com os picos marcados), o tom (o
  saldo, antes e agora, e as manchetes que pesaram), o **placar dos veículos**
  (espaço no que cada um publicou, contra o esperado, o tom e o tom contra o dos
  demais; clicar abre as manchetes do veículo) e **com quem aparece** (clicar
  refina a busca; "só" troca o termo).
- **2 · Enquadramento**: os assuntos da cobertura comparada que a busca achou (os
  mesmos do dossiê do nome no Analítico, com a divergência média) e os que mais
  dividiram (os dois enfoques e os veículos de cada lado), **quem fica com quem** e
  **quem destoa**, contra o acaso de cada placar.
- **3 · Texto**: os desvios do zero no termômetro, os textos mais inclinados e a
  qualidade da apuração das análises encontradas.

Na base completa ficam as tabelas por trás da leitura, inclusive **o que cada veículo
enfatiza** (o vocabulário distintivo, calculado quando o bloco abre; descritivo).

Tudo roda no navegador, sobre os arquivos de `data/busca/` (nada muda na edição
nem na IA): os fatos em `midia.js.j2`, os testes em `pesquisa_estat.js.j2`, o texto
em `pesquisa_achados.js.j2` e o desenho em `pesquisa_leitura.js.j2`. **Limites**: o
tom é lido por palavras (veja acima) e mede o contexto, não a opinião do veículo;
manchetes vêm em ondas (um assunto puxa outras) e os testes supõem manchetes
independentes; os lados são os enfoques de cada assunto, não "a favor" ou "contra"
a pessoa.

Embaixo da caixa de busca, os **nomes em destaque** da Política (os do placar do
Analítico) viram atalhos para o analítico de cada um (com a editoria e o período do
placar).

### Como a edição foi feita (racional) e lista completa do dia

- **Como a edição foi feita:** na home, o racional é uma linha do rodapé
  ("Compilada de 1.146 notícias de 42 veículos. Como foi feita"), sem contadores
  de produção. O link leva à gaveta **Como a edição foi feita** da cópia
  arquivada (`edicoes/AAAA-MM-DD.html#racional`), que diz como a edição foi
  compilada: quantas notícias e veículos, os blocos por editoria (notícias lidas,
  matérias redigidas e assuntos comparados em cada um) e quantos assuntos
  sobraram depois de unir as repetidas. O rodapé diz se a edição foi gerada por
  IA (e por qual) ou no modo automático. Na janela de cada matéria, a lista de
  fontes mostra de quantas notícias e veículos ela foi compilada e em que bloco.
- **Nenhuma notícia é descartada:** a página *Todas as notícias do dia*
  (`edicoes/AAAA-MM-DD-todas.html`, com link na capa, em "Mais recentes", e no
  e-mail) lista todas as
  notícias coletadas, por editoria e por assunto (as repetidas ficam juntas),
  com link para o original. Só ficam de fora da coleta publieditoriais,
  resultados de loteria, horóscopo e vídeos sem texto (`exclude_url_patterns` e
  `exclude_title_patterns` em `config/site.yaml`).

---

## Analítico do Congresso (aba Congresso, só Triangula)

O botão **Congresso** do cabeçalho (no celular, o único com o nome escrito: Buscar e o
botão do tema ficam só com o ícone) abre `edicoes/congresso.html`: o que cada
deputado e senador **vota, propõe e deixa parado**, lido dos registros oficiais
das duas Casas. Nas abas da **visão por partido** (as de sempre), **nenhuma IA entra
nas contas**: é contagem sobre voto nominal, proposição e relatoria, com as réguas à
vista em [`config/congresso.yaml`](config/congresso.yaml). A **visão por parlamentar**
é a exceção, e vem carimbada: ali o conteúdo de cada projeto de lei é **lido por IA,
com uma régua pública**, e cada número que depende dessa leitura leva o selo que diz
isso (ver [A visão por parlamentar](#a-visão-por-parlamentar-leitura-por-ia-com-régua-pública)).
Cobre todos os que estão em
exercício (513 deputados e 81 senadores) e quem passou pela legislatura, com o
histórico de votos desde 2019.

A página é feita de **abas**. No alto fica a Casa (Câmara ou Senado) e, logo abaixo,
o que ver dela; só a aba escolhida aparece, e cada uma tem endereço próprio
(`congresso.html#v=partidos`). Sem script, as abas saem todas em sequência.
Quando há leitura dos projetos, a linha da Casa ganha o interruptor **Ver por**
(Partido · Parlamentar), depois dos botões da Casa e da frase de ajuda, que não mudam
de lugar; no celular, ele ocupa uma linha só dele. As abas abaixo são as da visão por
partido; as da visão por parlamentar estão descritas mais adiante.

- **Resumo**: a leitura do dia (a manchete é o achado de maior peso, com o número e o
  link para a prova; abaixo, os demais), o mapa do que tem em cada aba e a caixa que
  abre o dossiê de qualquer nome.
- **Onde cada um está**: um ponto por parlamentar, partido a partido, na régua de −100
  (sempre com a esquerda) a +100 (sempre com a direita). O seletor **Ver por tema**
  leva cada ponto para o saldo da pessoa naquele assunto e abre, embaixo, **as votações
  do tema** numa tela só: a **fita** (um ponto por votação, no tempo), o **cartão da
  matéria em foco** (a ementa oficial, o que estava em votação, o placar com a obstrução
  e o quórum) e **quem votou, em pontos** (uma linha por partido, o Sim de um lado e o
  Não do outro, um ponto por parlamentar; dá para seguir um nome por todas as votações).
  É ali que se vê por que um voto conta como "de esquerda": não é o assunto, é como
  cada polo votou naquela votação.
- **Partidos**: a matriz **partido × tema** (com a marca "régua" nos partidos que
  definem os polos: eles ficam em −100 e +100 quase por definição), **quanto os
  partidos votam juntos** (quantas votações foram de consenso entre os polos e, par a
  par, em quantas das votações de conteúdo dois partidos ficaram do mesmo lado: uma
  conta que não depende da régua), **quando um lado vota a proposta do outro**, **onde
  cada polo racha** e as bancadas.
- **O que espera × o que passa** (a pauta): abre com os números do período: das
  aprovadas pelo Plenário em 12 meses, quantas tiveram **urgência**, quantas receberam o
  **parecer no próprio Plenário**, quantas passaram **sem votação nominal** e quantas
  tinham sinais fortes de relevância. Cada número é um botão que realça essas matérias
  no gráfico (nota de relevância × tempo desde o protocolo, com os quatro cantos
  nomeados e filtros por sinal, situação e tema). O ponto escolhido abre um **cartão**
  com a linha do tempo do atalho (protocolo, urgência, parecer em Plenário, aprovação,
  com os dias entre um passo e outro), os sinais da matéria e o **"enquanto isso"**:
  quantas matérias de nota maior esperam prontas no mesmo tema. Abaixo, a lista **vale
  olhar** (4 sinais ou mais), **a direção da Casa** por tema (parte das aprovadas ×
  parte das que esperam) e **quando vota** (um quadradinho por aprovada, semana a
  semana, com as semanas cheias e o calendário oficial anotado). Endereço:
  `#v=espera&c=cd&m=cd-PL-179-2026&s=urgencia,plenario&t=politica`.
- **A fila**: em que porta cada matéria parou (relator, parecer, pauta…), as comissões
  e os relatores com mais matérias na mão e as prontas fora da pauta.
- **Votações e matérias**: cada matéria votada é uma **folha de papel** com cara de
  documento oficial: a Casa, o tipo por extenso, o número, a ementa oficial, a autoria e
  a data, e o resultado em **carimbo** de texto ("Aprovado, o texto principal";
  "Retirado, o trecho destacado"). A **pilha** atrás da folha diz que houve mais de uma
  votação nominal; a faixa do alto tem a **cor do tema**, com o nome sempre escrito (13
  temas; cores em `congresso_temas.css.j2`, com o contraste conferido por teste). A mesa
  mostra as 10 matérias votadas por último, **uma em foco por vez** (miniaturas, ‹ ›,
  teclado; `#v=votacoes&c=cd&m=cd-PLP-281-2019&vt=<votação>`). Ao lado da folha, os
  dados: a descrição oficial, **o placar como a Mesa anuncia** (obstrução e abstenção à
  parte; na PEC e na lei complementar, a marca dos votos Sim exigidos e quantos
  faltaram), os lados do Governo e dos polos, os partidos e **quem saiu da linha**. A
  votação da capa é a do texto principal; as demais ficam em abas. A **busca** devolve
  folhas pequenas (número, apelido ou assunto), e a legenda das cores filtra por tema.
- **Votações e matérias** traz também **Nas comissões** (recolhido, no fim): as votações
  nominais das comissões, as últimas divididas e **quem mudou de voto entre a comissão
  e o Plenário** sobre o mesmo parecer.
- **Parlamentares**: as listas (quem menos vota, quem mais vota contra o partido, quem
  muda de lado conforme o assunto, **quem muda quando pesa a relevância**…), **no jornal ×
  no voto**, e a tabela de todos (com o saldo simples e o **pesado pela relevância**), com
  **Comparar dois**.
- **Frentes**: "assinar é fácil; e na hora do voto?". Um cartão por frente parlamentar de
  causa (a lista, com as duas pontas de cada tema, fica em `config/congresso.yaml`), que
  responde a uma pergunta: quem assina vota junto com quem coordena? Cada ponto é um
  deputado que assina, do lado em que vota no tema; ao lado, a mesma conta para **quem não
  assina** (a base de comparação) e os que mais votam diferente do coordenador. O cartão
  abre a **ficha da frente** (`#f=<id>`), com todos os que assinam, um a um. Só a Câmara
  registra quem assina.
- **Cota parlamentar**: um painel com período e, no fim, as listas de apuração. No alto, a
  frase **"Você está vendo"** diz a Casa, a janela (de início, a legislatura até o último mês
  fechado) e cada filtro, com um ✕ para tirar cada um; ela acompanha a rolagem enquanto o
  painel está na tela. Abaixo: **três números** (quanto no período, quanto por mês gasta o
  parlamentar do meio, a parte da maior despesa), **Quando** (uma coluna por mês; os últimos
  meses, que ainda recebem notas, mais claros), **Com o quê** (uma barra por despesa),
  **Quem** (um ponto por parlamentar em exercício, com a mediana e a faixa do meio), o
  **cartão em foco** (por mês com despesa, total em N de M meses, "Antes de comparar", as
  despesas, o mês a mês, os fornecedores), **as pontas** e **a tabela de todos**. Os filtros
  (período: legislatura, cada ano, últimos 12 meses, de/até; despesa; estado; partido; os
  10% que menos ou mais gastam) refazem tudo isso no navegador a partir de
  `cota-<casa>.json`; a pessoa em foco (clique no ponto, setas, "Achar um nome") muda só o
  cartão. O estado vai no endereço
  (`#v=cota&c=cd&de=2025-01&ate=2025-12&cat=divulgacao&uf=SP&pt=PL&g=menos&k=cd-204536`).
  Cada número leva a unidade e a janela; nenhum valor de pessoa aparece sem o total e os
  meses. O limite mensal por estado fica fora enquanto a tabela oficial não for conferida
  (`cota.tetos` em `config/congresso.yaml`, nulo). **Para conferir**, sempre da legislatura
  inteira: os fornecedores que mais recebem e de quantos gabinetes, quem pagou um quarto ou
  mais da própria cota a uma empresa só, **a mesma nota em mais de um gabinete** (com o
  link de cada nota) e o que a Casa recusou (glosa). Sem script, a aba mostra o estado
  padrão inteiro.
- **Escolha o seu candidato**: quem tem mandato pelo seu estado, um **cartão com a foto
  oficial** cada: de que lado vota (a régua), a presença e o voto com o partido (com a
  mediana da Casa), a **exceção** (o tema em que vota do lado oposto ao seu padrão), se
  **mudou de partido** na legislatura e o que propôs; o cartão abre o dossiê. Atalhos para
  o que é raro (mudou de partido, vota diferente do partido, vota pouco, muda de lado em
  algum tema) e filtros por tema, proposta, frente, cota (comparada dentro do estado),
  presença e governismo. Cada nome diz por que entrou, com o número; o endereço guarda a
  escolha (`#v=escolha&uf=SP&t=ambiente.e&at=troca`). Sem script, a lista sai inteira, por
  estado.
- **Como ler**: cada conta, por extenso, e a tabela **O que entra em cada tema**.

Três fichas abrem por cima da página, cada uma com endereço próprio:

- **O dossiê** (`#p=cd-204536`): com quem vota, governismo, presença, fidelidade, a
  posição **na Casa** em cada ranking, o saldo **por tema** (clicar num tema abre **as
  votações dele com o voto da pessoa**: cada matéria, o que estava em jogo, de que lado
  ela ficou; e a conta do saldo: "+71 em 14 votações: 12 com a direita e 2 com a
  esquerda; cada voto move 14 pontos"), o saldo **pesado pela relevância** e o das
  matérias de nota alta e de nota baixa, semestre a semestre, onde sai da linha, os
  últimos votos, **o que propõe** (de que tipo, sobre que temas, os termos do registro
  que mais marcam as propostas, as que viraram norma e as paradas), **as frentes** que
  assina (quantas, as que coordena e, nas de causa, quanto vota igual ao coordenador),
  **a cota** (ano a ano, com o quê, com quem, as maiores notas com o link e a glosa), **os
  votos nas comissões**, o que tem na mão e o que saiu no jornal.
- **O comparativo** (`#c=cd-204536,cd-209787`): quaisquer dois (pelos nomes em
  **Comparar dois**, marcando dois na tabela, pelo dossiê, ou trocando um dos lados),
  com os números e as posições, **o que cada um propõe**, o saldo **por tema** (o tema
  abre as votações com o voto dos dois, as divergentes à frente) e **votam juntos?**, no
  total e tema a tema (cada tema abre as matérias em que votaram igual e diferente).
- **A ficha da matéria** (`#m=cd-PL-2630-2020`): a ementa inteira e a explicação que a
  Casa escreveu, as palavras-chave do registro, o texto integral, autoria, situação e
  relator, a nota de relevância sinal a sinal, por que andou, e **cada votação nominal**
  com o que a Mesa pôs em votação, quem pediu o destaque, o que o Sim queria dizer, o
  placar, o lado de cada polo com a contagem de cada partido, o governo e os demais
  partidos. Abre de qualquer número de matéria da página.

Os rótulos e os números têm uma dica (o "i" ao lado, ou o tema tracejado) com a
definição curta.

**Nas matérias da edição**: quando o texto de uma matéria cita um deputado ou
senador em exercício, ela ganha o bloco **No Congresso**, com a ficha curta de cada
um (até `fichas_por_materia`, 4): o saldo, o governismo, a presença, as propostas
paradas, as relatorias sem parecer e o link para o dossiê. O nome só casa inteiro; nomes de uma palavra só (ou
feitos de prenomes comuns) precisam do cargo ("deputado", "senadora") ou da legenda
("(PL-SP)") ao lado, para não confundir com homônimos.

### A visão por parlamentar (leitura por IA, com régua pública)

O interruptor **Ver por**, ao lado da Casa, troca as abas: **Partido** (como votam: tudo
o que está acima, intacto) e **Parlamentar** (o que propõem). A visão por parlamentar é
feita da **leitura dos projetos de lei** de cada um e responde a quatro perguntas, uma
por aba. Ela só aparece quando há leituras em `data/congresso/leitura/` e o bloco
`projeto_de_pais` em `config/congresso.yaml`; sem um dos dois, a página sai como sempre
foi, sem o interruptor.

**A premissa é declarada.** A régua mede cada projeto em relação a um objetivo escrito
no config (`projeto_de_pais.objetivo`: infraestrutura, educação e economia que cresce,
com câmbio e custo de vida sob controle) e às alavancas do **núcleo** desse objetivo
(contas públicas e tributos, infraestrutura, educação, mercados e produtividade,
máquina pública). É escolha editorial: saúde, segurança, meio ambiente e regras
políticas ficam no entorno e pesam menos por escolha, não por valerem menos. O método é
o mesmo nos dois sentidos (gastar e cortar, regular e desregular caem na mesma faixa).

**A régua.** Um leitor de IA lê a **ementa** de cada PL, PLP e PEC (e das medidas
provisórias votadas), sem ver número, autor, partido nem origem, e marca seis campos
de códigos fechados: em que sistema mexe (a alavanca), quem a mudança alcança, o que o
projeto faz, o que a ementa faz com o caixa, para quem a regra é diferente e se aperta
ou afrouxa o controle. Daí saem:

- o **peso** (0 a 100) = 100 × nível da alavanca × alcance × mecanismo. Multiplica de
  propósito: só pesa quando os três coincidem. Separa prateleiras, não posições, e não
  diz se o projeto é bom;
- a **faixa**: mínimo (0 a 9), pontual (10 a 19), médio alcance (20 a 59) e
  estruturante (60 ou mais, só em alavanca do núcleo);
- três **marcas**, cada uma +1, 0 ou −1, lado a lado e nunca somadas nem multiplicadas
  pelo peso: contas, isonomia e controle. A marca −1 de contas diz que a ementa enuncia
  despesa, renúncia ou carimbo; não diz que falta fonte.

Os rótulos, os valores e os cortes ficam todos no config: nenhum nome de faixa,
alavanca, marca ou código está escrito no modelo nem no script da página.

**Quem leu.** Três leitores de IA, um sem ver o outro: o mais barato leu tudo, às
cegas; um intermediário releu o que o primeiro pôs no topo da régua e as matérias
votadas; o mais forte desempatou onde os dois divergiram. **"Estruturante" é palavra
reservada ao projeto confirmado por dois leitores**; o que só uma leitura pôs no topo
aparece como "a confirmar" e conta na faixa de baixo. A auditoria publicada em
`meta.json` (`leitura_projetos.auditoria`: as perguntas de controle escondidas em cada
lote, o piloto com dois leitores nas mesmas ementas, a segunda leitura) vai inteira no
Como ler, com a ressalva: mede a constância entre leitores de IA; não foi conferida
contra codificação humana.

As abas:

- **Os projetos** (`#v=projetos`): do que tramita, quantos estruturantes esperam,
  quantos estão parados há mais de um ano e em que porta. Uma faixa por porta, um
  ponto por projeto (quanto mais à direita, mais tempo sem andar; a forma do ponto é a
  marca de contas), com o **cartão do projeto**: a ementa, os seis códigos, a conta do
  peso por extenso, a justificativa do leitor, onde parou, com quem está, de quem é e o
  link **Contestar esta leitura**. Abaixo, os que mais pesam e não andam, as comissões e
  os relatores com mais deles na mão, e o que espera × o que virou norma, por faixa.
- **Quem propõe** (`#v=autores`): um ponto por parlamentar num funil (quantos projetos
  lidos × a parte deles que é estruturante confirmada, ou a parte na faixa mínima), com
  a **faixa do acaso** desenhada: dentro dela, a diferença para a Casa pode ser só
  acaso. O cartão de cada um traz a composição por faixa, as três marcas, o saldo de
  contas, a taxa de exceção, os três de maior peso, o que andou e todos os projetos com
  a leitura de cada um.
- **Na hora do voto** (`#v=decide`): só as votações nominais do texto principal no
  Plenário, uma por matéria. Abre com a resposta (de cada 100 votos, quantos seguiram o
  partido fechado), mostra de onde sai o universo passo a passo (quantas matérias,
  quantas ficam sem as quase unânimes, quantas de matéria de peso, quantas entram no
  voto fiscal e por que as outras não), **quando pesa** (presença e voto com o partido
  nas matérias de peso 20 ou mais, ao lado das demais) e as **faixas por partido**: um
  ponto por parlamentar, com a mediana e a metade do meio; nuvem apertada é o partido,
  ponto longe é a pessoa. O cartão de cada um lista os votos, um a um. A tabela das
  votações é a prova: a matéria, o peso, a marca, o resultado, o placar e quantos
  votaram contra o próprio partido.
- **Pessoa ou partido?** (`#v=cruzar`): o título é a resposta, gerada dos números
  ("No voto, o partido diz quase tudo. No que cada um propõe, ..."). Uma barra por
  medida diz **quanto o partido explica** da diferença entre as pessoas, com o
  intervalo de 95% e o traço do que daria por acaso; barra cujo intervalo encosta no
  acaso sai em cinza, com "não se distingue do acaso". Clicar numa barra abre a medida
  em faixas por partido. Abaixo, **o que anda junto** (pares de medidas, em frase, com
  a mesma conta dentro de cada partido) e a tabela das medianas por partido.
- **Como ler** ganha o bloco **A leitura dos projetos**: a premissa, a régua campo a
  campo (com a definição de cada código), quem leu e a auditoria (com a data dela), a
  leitura de todo dia (como é feita e, quando o registro existe, os números do período
  e quem respondeu), a cobertura, o que a régua não mede, os cinco erros de leitura e
  os limites. "O que o acaso explica" vem dito em palavras simples (acaso é sorte) na
  abertura e na dica do gráfico de "Quem propõe", na dica das barras de "Pessoa ou
  partido?" e no Como ler. A seção é das duas visões, e o endereço diz de qual:
  `#v=metodo` abre o Como ler da visão por partido, como sempre; `#cg-lp-metodo`, o da
  visão por parlamentar, direto neste bloco.

A pessoa em foco (`quem=cd-160976`) e a alavanca (`eixo=fiscal`) atravessam as quatro
abas, e o endereço reproduz o estado (`#v=decide&c=cd&med=fp&quem=cd-160976`). Seleção
realça: o que não passa fica esmaecido, não some. Os pontos vêm de arquivos pequenos
lidos quando a aba abre; sem script, cada aba traz os números, as tabelas e as listas.

**Onde não dá para concluir, a página diz.** Percentual por pessoa só com 10 projetos
lidos ou 10 votos dados; com menos de 50 pessoas medidas, "não dá para responder por
pessoa", e fica só o que há por partido (é o caso do voto fiscal no Senado). Quando
quase todas as matérias do voto fiscal são de uma marca só (na Câmara, em 17/11/2026,
14 de marca −1 contra 4 de marca +1), o aviso vai colado ao gráfico: a medida conta quase só quem
disse Não a matéria que pesa no caixa e se confunde com ser oposição.

### As contas

| Conta | Como é feita |
|---|---|
| Só voto nominal | só as votações de Plenário que registram o voto de cada um; a simbólica (a maioria) não diz quem votou o quê. As secretas do Senado (autoridades) contam para a presença, não para a posição |
| Tipo da votação | pela descrição oficial: texto principal, destaque/emenda, urgência, requerimento de procedimento, recurso, autoridade |
| Com quem vota (saldo) | só votações **de conteúdo** (texto principal e destaques) **divisivas**: a maioria do polo de esquerda votou de um jeito e a do polo de direita, de outro, com 75% de cada polo do mesmo lado, e nenhum partido de um polo votou fechado (75%) com o outro. Vai de −100 (sempre com a esquerda) a +100 (sempre com a direita); aparece com 10 votações ou mais. É comportamento, não rótulo |
| Rachas | votações em que os polos se opuseram mas um partido de um polo votou fechado com o outro: saem do saldo e aparecem à parte. Em geral são os dois extremos contra o governo por razões opostas (o PSOL contra uma regra fiscal por achá-la dura, o PL por ser oposição); no saldo, fariam o PSOL parecer menos de esquerda que o PT |
| Fora da linha, por assunto | cada voto contra o próprio partido fechado e cada voto com o outro polo (de quem tem saldo além de ±60) conta no tema da matéria |
| Muda de lado conforme o assunto | quem tem lado definido e, num tema com 10 votações esquerda × direita ou mais, fica do lado oposto (saldo do tema além de ±20 do outro lado), com o tema contra o resto dos votos da pessoa passando do acaso: binomial exata, Holm a 99% sobre todas as pessoas × temas da Casa (na lista) ou sobre os temas da pessoa (no dossiê) |
| Governismo | onde a liderança do Governo orientou Sim ou Não, a parte em que votou igual; contado à parte em cada governo (quem acompanha qualquer governo aparece assim). Obstrução conta como Não |
| Presença | votações em que podia votar e votou. Câmara: só os dias em exercício (histórico do mandato); os dados abertos não dizem se a ausência foi justificada. Senado: a ausência justificada (licença, missão, atividade parlamentar) fica fora da conta e aparece à parte. Quem preside a Casa só vota em caso de empate: fica fora da lista |
| Fidelidade e dissidência | votos iguais aos da maioria do partido do dia; dissidência é votar diferente quando 80% ou mais do partido votou junto |
| Surpresa | quem tem lado definido (saldo além de ±60) e, numa votação divisiva, ficou com o outro polo |
| Mudança | o último semestre contra os anteriores do mesmo governo: só com 15 pontos ou mais e 99% de confiança (duas proporções) |
| Temas | a área temática oficial da matéria votada (Câmara) ou a classe do processo (Senado), levadas a 13 macrotemas; a tabela "O que entra em cada tema", no Como ler, diz quais áreas caem em cada um |
| Por que um voto é "de esquerda" | não é o assunto: em cada votação, a esquerda é o lado da maioria (75% ou mais) de PT, PSOL e PCdoB, e a direita, o da maioria de PL e NOVO. Votar "com a esquerda" é votar igual a essa maioria ali (Sim numa, Não em outra). Os partidos dos polos ficam nas pontas por construção; a régua informa onde ficam os outros e cada pessoa |
| Quanto os partidos votam juntos | em todas as votações de conteúdo do Plenário, dois partidos votaram juntos quando a maioria de cada um ficou do mesmo lado; par com menos de 20 votações fica vazio. Consenso: os dois polos do mesmo lado. Não usa a régua dos polos |
| Quando um lado vota a proposta do outro | matérias de parlamentares de partidos de um polo, sem coautor do outro, com o texto principal votado nominalmente: o lado do outro polo na última dessas votações. O autor é o do registro original |
| O que se votou | na Câmara, a abertura da votação registrada pela Mesa diz quem pediu o destaque e qual trecho ou emenda. "O que o Sim queria dizer" sai da descrição oficial pela regra da Casa: texto principal, aprovar; destaque de emenda, aprovar a emenda; destaque para votação em separado, manter o trecho (o Não o retira) |
| A ficha da matéria | as votadas nominalmente no Plenário na legislatura e as de "o que espera × o que passa": a ementa que vale (e a original, se mudou), a ementa detalhada (Câmara) ou a explicação da ementa (Senado), as palavras-chave e o texto integral. A página não resume o projeto |
| O que cada um propõe | só a autoria principal (quem assina primeiro). Os termos que mais marcam as propostas são palavras-chave da indexação da Câmara, frequentes nas propostas da pessoa e raras no conjunto (termo em mais de 4% de todas as proposições fica de fora); o Senado não as traz nas listas |
| A fila | a situação oficial de cada proposição vira um estado (tramitando, apensada, aprovada, virou norma, encerrada) e uma porta (relator, parecer, pauta, Mesa, comissão…); "parada" é sem andamento há mais de 365 dias |
| Sinais da pauta | para cada aprovada pelo Plenário em 12 meses: com urgência (requerimento aprovado, no histórico); sem comissão (parecer dado em Plenário); relâmpago (até 30 dias do protocolo ao voto); urgência no dia; sem registro de voto (nenhuma votação nominal do texto principal); regras do próprio jogo (padrões na ementa: eleições, partidos, fundos de campanha, mandatos, imunidade, emendas, cadeiras, anistia a partidos, cargos das Casas); sinais fracos (nota abaixo de 30). No Senado, o registro não traz o caminho: só relâmpago, sem registro de voto, regras do próprio jogo e sinais fracos. Limiares, calendário e padrões no bloco `pauta` de `config/congresso.yaml` (`relampago_dias`, `sinais_vale_olhar`, `sinais_vale_olhar_senado`, `surto_vezes`, `ementa_ate`, `tema_minimo`, `calendario`, `proprio_jogo`) |
| Vale olhar | aprovadas com 4 sinais ou mais (Senado: 3), sem contar a urgência, que a maioria tem |
| Enquanto isso | prontas esperando no mesmo 1º tema com nota maior que a da aprovada: quantas e a mais antiga |
| A direção da Casa | por 1º tema, parte das aprovadas × parte das prontas esperando; só com tema em ao menos 50% das matérias de cada grupo |
| Semana cheia | semana (segunda a domingo) com 2 vezes a mediana das semanas com aprovação, ou mais; os recessos e a eleição são só anotados |
| A capa da folha | em "Votações e matérias", a votação que representa a matéria: a do texto principal (na PEC, o último turno); sem ela, a da redação final; depois, a de um destaque ou emenda; por fim, a de um requerimento |
| Nota de relevância (0 a 100) | sinais que dá para medir nos registros, com os pesos de `relevancia` em `config/congresso.yaml`: **projetos parecidos** anexados à matéria (até 40; 15 ou mais dão a nota cheia, em escala log; só na Câmara), **origem** (até 25: já aprovada pela outra Casa ou iniciativa popular, Executivo, comissão, Judiciário e órgãos de controle, parlamentar), **alcance** (até 20: PEC, lei complementar, lei) e **apoio** (até 15: autores de mais de um partido, nota cheia se dos dois polos). No Senado, sem os projetos parecidos, os outros pesos valem o todo. Matéria simbólica (dia nacional, denominação, título, rota turística…) não passa de 10. Não diz se a matéria é boa: diz quantos sinais de relevância pública ela tem |
| O que espera × o que passa | esperam: prontas para o Plenário, fora da pauta (PL, PLP e PEC; medidas provisórias ficam de fora, têm prazo na Constituição). Passam: aprovadas pelo Plenário nos últimos 12 meses (Câmara: pelo histórico oficial de cada matéria, `/proposicoes/{id}/tramitacoes`; Senado: pela lista de deliberações). Sinais fortes: nota 60 ou mais; fracos: abaixo de 30 |
| Por que andou | do histórico oficial (Câmara): a urgência aprovada antes da votação e quem a pediu, e o parecer dado direto no Plenário (no lugar das comissões) e por quem. Só o que o histórico registra |
| Rankings | entre os que estão em exercício na Casa; o 1º é quem tem o maior número; empate divide a posição ("413º de 513 · empatado com outros 100"); quem preside a Casa fica fora do de presença |
| Votam juntos? | nas votações de conteúdo abertas de Plenário da legislatura em que os dois votaram Sim ou Não (obstrução conta como Não): a parte em que votaram igual, e a mesma conta só nas que dividiram esquerda e direita |
| Onde cada um está, por tema | o saldo de cada um nas votações esquerda × direita do tema, com 5 ou mais; o contorno marca quem vira de lado no tema (o mesmo teste do dossiê). Tema com menos de 5 dessas votações na Casa aparece no seletor desabilitado, com o motivo |
| No jornal × no voto | o texto do jornal que cita a pessoa e uma proposição (pelo número ou pelo apelido de `apelidos` em `config/congresso.yaml`); numa matéria, os dois na mesma frase. Ao lado, os votos dela nessa proposição (o texto principal primeiro; na PEC, com o turno que a descrição oficial dá). A página não interpreta a fala: põe as duas coisas lado a lado |
| No jornal | os nomes procurados nas manchetes e no texto das matérias de `data/busca/` (as edições dos últimos 60 dias; fora esporte, natureza e variedades) |
| Saldo pesado pela relevância | o mesmo saldo, com cada voto pesando a nota de relevância da matéria (0 a 100); só as votações cuja matéria tem nota (PL, PLP, PEC; MP e PDL ficam de fora), com 10 ou mais. Ao lado, o saldo sem peso nas votações de nota igual ou acima da mediana da Casa e nas de nota abaixo (5 ou mais em cada metade; a mediana, e não as faixas fixas, porque na Câmara quase toda matéria votada tem nota baixa). "Quem muda quando pesa": o saldo pesado a 20 pontos ou mais do saldo sem peso nas mesmas votações (as que têm nota); vira achado só se a diferença entre as metades passar do acaso (99%). A nota mede sinais do registro, não a repercussão |
| Frentes | as frentes de causa de `frentes` em `config/congresso.yaml` (o começo do título oficial e o tema em que a causa se vota; "todas" para as sem tema), com as duas pontas de cada tema. Lado de cada membro em exercício: o saldo dele no tema (5 ou mais votações esquerda × direita; nas sem tema, o geral). Com o coordenador: nas votações esquerda × direita do tema, quanto o membro votou igual a ele (a frente não registra posição; o coordenador a registra; só vale o coordenador que a lista de membros marca). A mesma conta sai para quem está em exercício e NÃO assina: é a base de comparação. "Do lado oposto ao do coordenador" só aparece com o coordenador numa ponta da régua (saldo até −60 ou acima de +60). Com menos de 20 votações no tema (`limiares.frente_poucas`), o cartão avisa que são poucas. A Câmara não registra quando cada um assinou; o Senado não publica as frentes |
| Nas comissões | votações nominais das comissões (Câmara: os arquivos em lote; Senado: as 16 permanentes), fora das contas do Plenário. Na Câmara, o voto é no parecer (Sim = aprovar o parecer, que pode ser pela rejeição) e só aparece quem votou. "Mudou de voto": o Plenário votou o parecer de uma comissão; vale a última votação da comissão que aprovou esse parecer, e entra quem votou Sim ou Não nas duas. No Senado, o registro não traz o resultado nem se o item era terminativo |
| Cota parlamentar | os reembolsos da cota nota a nota (Câmara: arquivos anuais oficiais, com o link de cada nota; Senado: dados abertos administrativos). Valor = o reembolsado (nota − glosa − o que foi devolvido). Média por mês = total da legislatura ÷ meses fechados com despesa (o mês corrente fica fora da média; listas com 6 meses ou mais). Fornecedor pelo CNPJ; pessoa física sem o nome. "Um fornecedor só": 25% ou mais do próprio total, com R$ 50 mil ou mais. "A mesma nota": CNPJ, número, data e valor iguais em gabinetes diferentes (número que é só mês/ano fica de fora). Quem está em exercício há tempo (100 votações possíveis ou mais) e não pediu reembolso nenhum aparece à parte, com zero. Ranking e filtro da cota: 6 meses ou mais, como as listas. Fora da conta: na Câmara, as passagens da agência da Casa (SIGEPA), que o arquivo deixou de trazer em ago/2025 (fora de todos os anos; ao lado de cada um, o que teve delas em 2024); no Senado, o reembolso de moradia, que o próprio Senado não conta como cota |
| Onde cada um está, por tema | escolhido um tema: a fita (um ponto por votação que opôs os polos, no tempo; quadrado = texto principal, círculo = destaque ou emenda; cor = o lado que prevaleceu), o cartão da matéria em foco (a ementa oficial, o que se votou, o placar com a obstrução e o quórum) e o plenário em pontos (uma linha por partido do dia; Sim à esquerda do eixo, Não à direita; cor = o saldo da pessoa no tema; aro = contra o próprio partido fechado; vazado = obstrução ou abstenção). Realce "diferença pequena": menos de 30 votos na Câmara (`limiares.voto_apertado`; no Senado, a mesma proporção). A matéria é representada pelo texto principal com mais votos. Endereço: `#v=posicoes&c=cd&t=politica&m=cd-PL-2162-2023&vt=<id>&s=<casa-id de quem seguir>` |
| Escolha o seu candidato | os números da página, filtrados: lado no tema = saldo do tema até −20 (esquerda), acima de −20 até +20 (dividido), acima de +20 (direita), com 5 ou mais votações no tema; "propôs sobre" = autoria principal; cota = até a mediana ou o primeiro quartil do estado (Câmara, porque o teto muda por estado) ou da Casa (Senado). A ordem "mais perto do que escolhi" é a média do saldo no sentido escolhido. Exceção = quem tem lado no geral e, num tema com 5 votações ou mais, vota do lado oposto (o tema de mais votações). Mudou de partido = o partido anterior e o mês do primeiro registro de votação pelo partido atual; só com registro pelos dois partidos nesta legislatura, e fusão ou incorporação (`fusoes` em `config/congresso.yaml`: Patriota e PTB → PRD, PSC → Podemos, PROS → Solidariedade) não conta como troca. Atalhos: vota com a maioria do partido em menos de 80% das vezes (`limiares.escolha_fidelidade`); votou em menos de 60% das votações (`limiares.escolha_presenca`) |
| O placar | como a Mesa anuncia: Sim, Não, abstenção e, à parte, a obstrução (no lado de cada um ela vale como Não; no placar, não é voto Não). Nas votações de conteúdo de PEC e de projeto de lei complementar, o número de Sim que a Constituição exige (três quintos: 308 e 49; maioria absoluta: 257 e 41) vai ao lado do placar |
| Votação que não bate com o placar | a Câmara publica o placar na descrição da votação e, à parte, o voto de cada deputado; quando os dois diferem em mais de 20 votos no Sim ou no Não (`limiares.placar_tolerancia`; inclui a votação sem voto individual, como a secreta), a votação sai de todas as contas e fica listada em `meta.json` (`fora_do_placar`) e no "Como ler". A base gravada continua como veio da fonte |
| A leitura dos projetos (visão por parlamentar) | cada PL, PLP e PEC (e as medidas provisórias votadas) é lido por IA pela ementa, às cegas, em seis campos de códigos fechados (`projeto_de_pais.campos` em `config/congresso.yaml`); as instruções ao leitor estão em `config/leitura_projetos.txt`. Entram o que cada um propõe na legislatura, o que tramita e as matérias com votação nominal do texto principal. Sem leitura (a ementa não permite dizer um dos três fatores do peso, ou os leitores não se entenderam): fora de toda conta, contada na cobertura |
| Peso e faixa | peso = 100 × nível da alavanca (núcleo 1; entorno 0,6; fora 0,3) × alcance × mecanismo, arredondado. Faixas: mínimo (0 a 9), pontual (10 a 19), médio alcance (20 a 59), estruturante (60 ou mais, só no núcleo). **Estruturante confirmado**: duas leituras independentes na mesma faixa, ou a terceira decidiu; com uma leitura só, "a confirmar", e conta como médio alcance. O que compara pessoas conta só o confirmado |
| As três marcas | contas, isonomia e controle: +1, 0 ou −1 pelo código lido, ou "a ementa não permite dizer". Nunca somadas entre si nem multiplicadas pelo peso |
| Os projetos: parado e porta | do que tramita e tem leitura: sem andamento oficial há mais de 365 dias é "parado"; a porta e quem está com a matéria vêm da situação oficial, como em "A fila". A apensada segue a principal e não entra nas faixas. No gráfico, até 600 projetos por Casa, os de maior peso |
| Quem propõe: o perfil | PL, PLP e PEC de autoria principal (quem assina primeiro), apresentados na legislatura, com leitura. Ementa idêntica à de proposição anterior na Casa conta uma vez, para quem apresentou primeiro; projeto de bancada (10 coautores do mesmo partido, ou metade dela) fica fora. Percentuais só com 10 lidos ou mais. Saldo de contas = (projetos de marca +1 − de marca −1) ÷ projetos com marca ±1, de −100 a +100, com 5 ou mais. Taxa de exceção = a parte dos lidos que abre exceção para profissão, setor ou lugar |
| A faixa do acaso (funil) | para cada número de projetos lidos, o intervalo em que a parte da pessoa (de estruturantes confirmados, ou da faixa mínima) pode diferir da taxa da Casa só por acaso: binomial exata, 99% de confiança, já contando todas as pessoas comparadas |
| Na hora do voto: o universo | as votações nominais abertas do texto principal no Plenário, desde 2019, uma por matéria (na PEC, o último turno); fora a redação final, a mudança feita pela outra Casa e as quase unânimes (90% ou mais do Sim + Não do mesmo lado). Só Sim e Não contam como voto: obstrução, aqui, não é voto |
| Quando pesa | nas votações de matéria de peso 20 ou mais, a presença (votou ÷ podia votar) e o voto com a maioria do partido do dia, ao lado dos mesmos números nas demais votações do universo. Não depende de marca |
| Voto fiscal | só matéria com a leitura confirmada por dois leitores e marca legível (contas +1 ou −1, ou controle +1 ou −1 na alavanca das contas públicas), de peso 20 ou mais, ou da alavanca das contas públicas com qualquer peso (menos a de um caso só); matéria com marca +1 e −1 ao mesmo tempo fica fora, e também a que tem a marca das contas em disputa entre os leitores (a justificativa termina em `[leitores divergem na marca de contas]`), ainda que a de controle seja legível: é voto fiscal o Sim em matéria de marca +1 e o Não em matéria de marca −1, em % dos votos dados, com 10 votos ou mais. Diz de que lado do caixa a pessoa votou, não se votou certo. Com menos de 50 pessoas medidas: "não dá para responder por pessoa" |
| Contra o próprio partido fechado | voto diferente do da maioria do partido do dia quando ela reuniu 80% ou mais do Sim + Não. Por pessoa, só contagem: sem teste, porque com tão poucas votações nenhum teria força |
| Quanto o partido explica | da variação entre pessoas em cada medida, a parte que fica entre os partidos, descontado o que qualquer divisão em tantos grupos explicaria. A medida "quantos projetos apresentou na legislatura" conta os PL, PLP e PEC de autoria principal do perfil de "Quem propõe" (apresentados na legislatura, com ou sem leitura, fora os de ementa repetida e os de bancada), não o total do dossiê. O intervalo de 95% sai reamostrando as pessoas 2.000 vezes; "o que daria por acaso", embaralhando os partidos entre as pessoas 1.000 vezes (semente fixa: o resultado não muda de um dia para o outro com os mesmos dados). 70% ou mais: diz quase tudo; de 30% a 70%: diz parte; abaixo: diz pouco. Intervalo que encosta no acaso: "não se distingue do acaso" |
| O que anda junto | correlação de postos (Spearman) entre duas medidas, na Casa e dentro dos partidos; só vira afirmação o que passa do acaso a 99%, contando todos os pares (Holm) |

Os polos (hoje PT, PSOL e PCdoB × PL e NOVO), os governos, os limiares, as faixas
do campo, os macrotemas, os pesos da nota de relevância e os **apelidos dos
projetos** (cada um conferido contra a ementa oficial; apelido ambíguo, como "reforma
tributária", fica de fora) são **escolhas editoriais**: ficam em
`config/congresso.yaml` e vão escritos no **Como ler** da aba. Mudar um valor muda a
conta na atualização seguinte.

**Limites**: mede voto, não discurso (o cruzamento "disse × votou" ainda é manual:
a ficha põe o voto ao lado da manchete); a página **não resume** o que cada projeto
faz: mostra a ementa, a explicação e as palavras-chave oficiais, e aponta o texto
integral (a explicação só existe para parte das matérias); só o Plenário entra nas
contas de voto (as comissões aparecem à parte, como lista); a autoria conta o primeiro signatário; no Senado, a fila só
tem o que tramita; nomes muito comuns podem deixar de casar nas matérias (o
contrário, casar errado, é o que a regra evita). Na nota de relevância, uma matéria
mais antiga teve mais tempo para juntar projetos parecidos, e "último andamento" é a
data da situação oficial, que muda também quando a matéria é pautada e não votada.

**Limites da visão por parlamentar**: a leitura é da **ementa**, não do texto integral
(o jabuti sob "e dá outras providências", a fonte prevista no articulado e o que o
substitutivo mudou ficam de fora); na matéria votada, lê-se a ementa do registro, não o
texto que foi a voto; não houve codificação por pessoas para servir de padrão (a
auditoria mede se leitores de IA diferentes chegam à mesma faixa, e dois leitores que
concordam podem errar juntos); o topo da régua só é procurado entre o que a primeira
leitura pôs lá; o peso não diz se o projeto é bom, a marca negativa não diz que falta
fonte, e "parado" não é culpa do autor (a porta é de outro); só a autoria principal
entra (relatoria, emenda e negociação, não); o voto fiscal se confunde com apoiar ou
não o governo; e as medidas que saem da leitura herdam o erro dela, o que puxa para
baixo tanto o que o partido explica quanto as correlações.

### Dados e atualização

- **Fontes** (públicas, sem chave): arquivos em lote e API da Câmara
  (`dadosabertos.camara.leg.br`: votações, proposições, frentes e membros, e a cota em
  `arquivos/cotas/Ano-AAAA.csv.zip`, com `www.camara.leg.br/cotas` de reserva), API
  legislativa do Senado (`legis.senado.leg.br/dadosabertos`, inclusive as votações em
  comissão) e a API administrativa do Senado (`adm.senado.gov.br/adm-dadosabertos`, a
  cota).
- **Todo dia**, antes da edição, o passo *Atualizar o Congresso* do `daily.yml`
  roda `python -m qijournal congresso atualizar`: baixa o que mudou, refaz as contas
  e grava só os arquivos que mudaram (cerca de 2 a 4 minutos; os anos fechados da base
  de votos só são refeitos quando o formato da base muda). Também lê o histórico oficial das matérias aprovadas
  pelo Plenário da Câmara nos últimos 12 meses (uma consulta por matéria, guardada
  por 30 dias), as deliberações do Plenário do Senado, as frentes (a lista de membros,
  perto de 100 MB, é baixada de novo uma vez por semana), a cota do ano (e do anterior
  até abril; os anos fechados, uma vez por mês) e as votações das 16 comissões
  permanentes do Senado. Cada uma dessas fontes é acessória: se uma falhar, vira aviso e
  o resto sai. O que foi baixado fica num cache do Actions, fora do repositório. O passo é acessório (`continue-on-error`, 15 minutos): se uma fonte
  estiver fora do ar, a edição sai com os dados da véspera.
- **À mão**: *Actions → **Congresso** → Run workflow* atualiza fora de hora; a
  opção **Carga completa** refaz também os anos fechados e relê todas as
  proposições.
- **Onde fica** (`data/congresso/`, gerado):

| Arquivo | O quê |
|---|---|
| `base/cd-AAAA.json`, `base/sf-AAAA.json` | as votações nominais de cada Casa e ano: um caractere por pessoa por votação (`S` sim, `N` não, `A` abstenção, `O` obstrução, `P` presidiu, `J` ausência justificada, `F` falta…), uma votação por linha |
| `base/cd-exercicio.json` | os períodos em exercício de cada deputado e quem presidiu a Câmara |
| `fichas/<casa>-<id>.json` | o dossiê de cada parlamentar (carregado quando a ficha abre) |
| `resumo.json` | uma linha por parlamentar (a tabela da aba e as fichas das matérias) |
| `casa-cd.json`, `casa-sf.json` | os painéis de cada Casa (bancadas, temas, rachas, fila, o que espera × o que passa, a pauta (`pauta`: os sinais das aprovadas, a direção por tema e as semanas), as folhas da mesa (`folhas`), listas, no jornal × no voto, últimas votações, a cobertura do saldo pesado, as frentes, a cota e as comissões) |
| `votos-cd.json`, `votos-sf.json` | os votos de cada um nas votações de conteúdo da legislatura, um caractere por votação e uma pessoa por linha, com a data, o tipo, os temas e o turno (na PEC) de cada votação, e o **partido de cada um no dia de cada votação** (`partidos`: só de quem mudou) (o comparativo, os votos por tema e o plenário em pontos leem quando abrem) |
| `frentes-cd.json` | a ficha de cada frente de causa: todos os que assinam e estão em exercício, com o saldo no tema e quanto votaram igual ao coordenador (lido quando a ficha da frente abre) |
| `cota-cd.json`, `cota-sf.json` | a cota de cada um mês a mês e despesa a despesa, em centavos inteiros, uma pessoa por linha: quem teve lançamento em mês fechado da legislatura (em exercício ou não), com as séries `[despesa, primeiro mês, centavos de cada mês seguido…]` e, na Câmara, as passagens da agência por ano. Somando as células de uma pessoa volta-se ao total da ficha dela; o mês em curso fica fora. É o que a aba "Cota parlamentar" precisa para mudar o período e cruzar filtros. Os nomes curtos das despesas, quantos meses do fim ainda recebem notas e a tabela do limite mensal por estado (nula enquanto não for conferida na fonte oficial: sem ela, nada de limite aparece) ficam em `cota`, em `config/congresso.yaml` |
| `materias-cd.json`, `materias-sf.json` | a ficha de cada matéria votada no Plenário na legislatura e de cada ponto de "o que espera × o que passa", uma por linha: sobre o quê, autoria, situação, nota e cada votação com os lados (lido quando um detalhe abre) |
| `achados.json`, `meta.json` | a leitura do dia e as réguas usadas; com a visão por parlamentar, `meta.json` leva também `leitura_projetos`: a versão das instruções ao leitor, os cortes, a cobertura por Casa (quantas proposições as contas pedem, quantas têm leitura, quantas confirmadas) e a auditoria da leitura |
| `leitura/<casa>-AAAA.json`, `leitura/auditoria.json` | **a guarda da leitura dos projetos** (não é gerada pela atualização: é por onde as leituras de IA entram, com `python -m qijournal congresso leitura importar`): um arquivo por Casa e ano da proposição, uma proposição por linha, com os seis códigos, o alerta de iniciativa, a confiança, a justificativa e o estado da leitura (1 = uma leitura; 2 = duas na mesma faixa; 3 = a terceira decidiu; 0 = instável). `auditoria.json` é escrita por fora e só repassada a `meta.json`. A leitura diária grava na mesma pasta `diario.json` (o registro de cada dia) e `a_decidir.json` (as segundas leituras que divergiram da primeira e esperam a terceira) |
| `casa-cd.json`, `casa-sf.json` → `lp` | o bloco da visão por parlamentar dentro dos painéis de cada Casa (só com leituras): o estoque por faixa, por porta e por alavanca, os números de quem propõe, `decide` (o universo do voto, quando pesa, o voto fiscal por partido, as votações e quem mais divergiu) e `cruzar` (quanto o partido explica de cada medida, os pares e as medianas por partido). É o que a página mostra sem script |
| `projetos-cd.json`, `projetos-sf.json` | os projetos do topo da régua que tramitam, com tudo o que o cartão mostra: a ementa, a porta, o tempo sem andar, o peso, os códigos, a conta, a justificativa e os alertas (lido ao abrir "Os projetos") |
| `autores-cd.json`, `autores-sf.json` | uma linha por parlamentar em exercício: a composição por faixa, as marcas, o saldo de contas, a taxa de exceção, a faixa do acaso do funil e as medidas de voto (lido ao abrir "Quem propõe", "Na hora do voto" e "Pessoa ou partido?") |
| `decide-cd.json`, `decide-sf.json` | as votações que entram em "Na hora do voto" e o voto de cada um nelas, um caractere por votação, com as posições em que votou contra o próprio partido fechado (lido quando o cartão de uma pessoa abre) |
| `autoria/<casa>-<id>.json` | todos os projetos lidos de cada parlamentar, com a leitura de cada um (lido ao abrir "Todos os projetos" no cartão da pessoa) |

- **Desligar**: `congresso.enabled: false` em `config/site.yaml`, ou a variável
  **`QIJ_NO_CONGRESSO`** = `1` (o botão e os blocos somem; os dados ficam).
- **Código**: `qijournal/congresso/` (`fontes.py` coleta e cache; `base.py` votações;
  `regras.py` as réguas; `perfil.py` as contas de voto; `exercicio.py` períodos de
  mandato; `projetos.py` proposições, relatorias e fila; `relevancia.py` a nota de relevância; `pauta.py` os sinais das aprovadas, a direção e as semanas; `materias.py` a ficha de
  cada matéria e as folhas da mesa; `peso.py` o saldo pesado pela relevância; `frentes.py` as frentes × o voto; `cota.py` a cota parlamentar;
  `comissoes.py` os votos nas comissões; `jornal.py` os nomes no banco do jornal; `ficha.py` o dossiê; `dados.py` a atualização; `cli.py` os
  comandos; e, da visão por parlamentar, `leitura.py` a guarda da leitura e a conta do peso, `pais.py` o estoque e o perfil de quem propõe,
  `pais_voto.py` o voto quando pesa e o voto fiscal, `pais_cruzar.py` e `pais_estat.py` quanto o partido explica e o que anda junto) e
  `qijournal/render/congresso.py` com os modelos `congresso.*.j2`
  (`congresso_materias.js.j2` é o script das fichas das matérias e da busca;
  `congresso_cartoes.js.j2`, o dos cartões que inclinam e da ficha das frentes;
  `congresso_tema.js.j2`, o das votações de um tema, em fita, cartão e plenário;
  `congresso_pauta.js.j2`, o de "O que espera × o que passa"; `congresso_papel.js.j2`, o
  das folhas de "Votações e matérias"; `congresso_escolha.js.j2`, o de "Escolha o seu
  candidato"; os estilos de cada um em `congresso_tema.css.j2`, `congresso_pauta.css.j2`,
  `congresso_papel.css.j2` e as cores dos temas em `congresso_temas.css.j2`). A tela da
  visão por parlamentar fica à parte: `qijournal/render/congresso_lp.py` ("Os projetos" e
  "Quem propõe"), `congresso_lp_voto.py` ("Na hora do voto" e "Pessoa ou partido?") e
  `congresso_lp_metodo.py` (o bloco do Como ler), com os modelos `congresso_lp*.j2`.

### Leitura diária dos projetos de lei (`congresso ler`)

A visão por parlamentar da aba Congresso usa a leitura, por IA, de cada projeto de lei
(PL, PLP, PEC) com a régua pública de `config/congresso.yaml` (`projeto_de_pais`): o
leitor marca seis campos com códigos fechados e a nota sai da fórmula. As cerca de 40 mil
proposições que existiam foram lidas de uma vez e estão em
`data/congresso/leitura/<casa>-<ano>.json` (a guarda, uma proposição por linha). Chegam
umas 25 proposições por dia e matérias novas vão a voto: a leitura diária mantém a
cobertura.

- **Onde roda.** Num fluxo à parte, *Leitura dos projetos* (`leitura.yml`), que dispara
  quando a *Edição diária* termina: a edição, o e-mail e o Instagram não esperam a
  leitura, e a edição usa as IAs primeiro. A *Edição diária* termina de hora em hora
  (em segundos, quando não há edição nova); por isso o primeiro passo do fluxo decide:
  só lê se a edição de hoje já saiu e o registro ainda não tem a linha de hoje, e nas
  outras horas a execução termina em segundos. A leitura usa o cache do que a edição
  baixou do Congresso (só restaura, não grava); sem ele, não lê e avisa. O que foi lido
  entra nas contas na atualização seguinte do Congresso (a do dia seguinte, ou a do
  fluxo *Congresso*, à mão). Para rodar à mão: *Actions → **Leitura dos projetos** → Run workflow* lê na
  hora, mesmo que já tenha lido hoje (o teto de leituras é opcional; `0` só diz o que
  leria).
- **Como publica.** Num commit próprio, só de `data/congresso/leitura/` (*Publicar a
  leitura*), que nunca desfaz nem apaga o que outro enviou: leva só o que a leitura
  gravou e, se o repositório andou enquanto ela lia, é reaplicado por cima; se alguém
  mexeu nas mesmas linhas da guarda, nada é publicado, o passo avisa e a execução
  seguinte lê de novo. O contrário não vale: o passo *Publicar* da *Edição diária* e do
  *Congresso* refaz o commit com a cópia de `data/` do início da execução quando o push
  é recusado, e por isso o que chegar à guarda no meio de uma execução deles pode ser
  desfeito: as leituras importadas à mão e enviadas nessa hora (têm de ser enviadas de
  novo) e as leituras do dia, se forem publicadas nesse meio, o que só acontece quando
  um dos três fluxos é disparado à mão enquanto outro roda (são lidas de novo na
  execução seguinte).
- **O que lê.** O passo *Ler os projetos novos* roda
  `python -m qijournal congresso ler --dir . --cache DIR [--max N] [--now ISO]`.
  Primeiro, o que ainda não tem leitura: as matérias votadas, depois os projetos de quem
  está no mandato, depois o resto do que tramita, sempre dos mais novos para os mais
  antigos. Depois, a segunda leitura do que muda a conta: os projetos de peso 60 ou mais
  e as matérias votadas (o topo e o voto fiscal só contam com duas leituras).
- **Com que IA.** A cadeia do jornal (`llm.providers`, com as mesmas chaves da edição):
  quando uma estoura o limite, a seguinte assume. Cada pedido leva as instruções inteiras
  (`config/leitura_projetos.txt`: as regras, os erros comuns e 50 exemplos resolvidos) e
  um lote cego: tipo, ementa, ementa detalhada, palavras-chave e tema. O leitor não vê
  número, autor, partido nem Casa.
- **Perguntas de controle.** Em cada lote de 30 vão 10 proposições de
  `config/leitura_referencia.jsonl` (185 proposições reais que dois leitores
  classificaram igual), misturadas e sem aviso. Se o leitor põe menos de 7 delas na
  faixa certa, o lote inteiro é descartado: nada dele entra na guarda, e as proposições
  voltam no dia seguinte. Mais da metade das 185 cai numa faixa só; por isso nenhum lote
  leva mais de 6 perguntas da mesma faixa (o mínimo menos um): o leitor que responde a
  mesma coisa para tudo nunca chega aos 7 acertos. As perguntas não se repetem no dia
  dentro das 12 chamadas de uma rodada. No lote aceito, a resposta que falta ou traz código que não
  existe também volta.
- **Segunda leitura.** É uma chamada nova, que não vê a primeira. Quando a primeira foi
  feita na mesma rodada, a segunda vai a outra IA da cadeia, se houver. Quando a
  primeira é de outro dia, a guarda não diz que IA a fez: a segunda evita a primeira IA
  da cadeia (a que costuma fazer as primeiras leituras), sem garantia de que seja outra.
  Com uma IA só na cadeia, ela faz as duas. Mesma faixa: leitura confirmada. Faixas
  diferentes: uma terceira leitura decide (duas de três), feita por uma IA que ainda
  não leu a proposição, se houver (com duas IAs na cadeia, volta a uma das duas); se as
  três divergem, a proposição fica sem leitura ("leitura instável"). Sem terceira
  possível no dia, a leitura fica como estava e a segunda, que divergiu, fica guardada
  em `data/congresso/leitura/a_decidir.json`, com as IAs que já leram a proposição: na
  rodada seguinte não se pede outra segunda, a leitura nova é a terceira (de uma IA que
  ainda não leu, se houver) e vale a regra dos três. Sem isso, a segunda que divergia
  era esquecida e pedida de novo a cada dia, até uma concordar. Se esse arquivo não for
  lido inteiro (ilegível, de outra versão das instruções ou com linha fora do formato),
  a rodada não o regrava e não faz segundas leituras até o conserto. O que as leituras
  confirmam é a faixa; os outros códigos são os do primeiro leitor que caiu nela. A
  exceção é a marca das contas da matéria votada, que decide o voto fiscal: se os
  leitores da faixa não chegaram à mesma marca, o custo fica "a ementa não permite
  dizer", a justificativa termina em `[leitores divergem na marca de contas]` e a
  matéria fica fora do voto fiscal (a regra da leitura inicial; a diária anota também
  quando o primeiro leitor já não via marca e o outro via).
- **Limites** (`projeto_de_pais.leitura.diaria`): 150 primeiras e 60 segundas leituras
  por rodada, lotes de 30, 12 chamadas e 8 minutos. A guarda é gravada a cada lote: o
  que foi lido não se perde se o tempo acabar. O passo é acessório (`continue-on-error`,
  12 minutos): sem chave de IA, registra "sem IA: nada lido". Nenhuma leitura gravada é
  apagada. O arquivo da guarda que a rodada não consegue ler inteiro (de outra versão
  das instruções, ilegível, ou com uma linha que não valida, por exemplo com um código
  que a régua não tem mais) não é regravado: as proposições daquele ano e daquela Casa
  ficam sem leitura nova até o conserto, e o registro do dia avisa.
- **O registro.** `data/congresso/leitura/diario.json`, uma linha por dia (os últimos
  60): quantas leu, os lotes e os descartados, os acertos nos controles, as confirmadas,
  as decididas pela terceira e as instáveis, o que ficou pendente (sem leitura, à
  espera da segunda e, dessas, as que esperam a terceira) e as IAs que
  responderam. Também quantas segundas foram feitas pela mesma IA que fez a primeira
  na rodada (`mesmo_editor`), quantas eram de primeira leitura de outra rodada, em que
  não se sabe (`de_outra_rodada`), e quantas terceiras foram feitas por uma IA que se
  sabe que já tinha lido a proposição (`terceira_de_quem_leu`; da primeira leitura de
  outra rodada também não se sabe a IA, e a terceira feita por ela não entra na conta). O resumo
  vai a `meta.json` (`leitura_projetos.diaria`) e ao resumo do passo no Actions.
- **Desligar**: variável **`QIJ_NO_LEITURA`** = `1` no GitHub, ou `ligada: false` no
  bloco do config (`QIJ_NO_CONGRESSO` também a desliga). A opção *Gerar sem IA* da
  *Edição diária* não vale para a leitura, que roda no fluxo dela. À mão, `--max 0` só
  diz o que leria.
- **Código**: `qijournal/congresso/ler.py` (a rodada) e `leitura.py` (a guarda e a
  conta); testes em `tests/test_congresso_ler.py`.

---

## Analítico dos Tribunais (aba Tribunais, só Triangula)

O botão **Tribunais** do cabeçalho abre `edicoes/tribunais.html`: **quem está com o
quê no STJ e no STF**. Mostra o acervo de cada ministro por área do direito, o que está
na mesa dele e há quanto tempo, os assuntos em que cada gabinete se concentra e as partes
que se repetem. Tudo vem do que os próprios tribunais publicam em dados abertos, e
**nenhuma IA entra nas contas**: é contagem sobre o registro de cada processo. As réguas
ficam à vista em [`config/tribunais.yaml`](config/tribunais.yaml) (áreas, composição,
limiares e fontes) e [`config/tribunais_partes.yaml`](config/tribunais_partes.yaml)
(o catálogo de litigantes).

No alto fica o tribunal (STF ou STJ, com o número de ministros no painel) e, logo abaixo,
o que ver dele. Cada aba tem endereço próprio, que pode ser enviado (`tribunais.html#v=ministros&t=stj`; a mesa de um ministro, `&min=stj-raul-araujo`).
Sem script, as abas saem todas em sequência.

- **Resumo**: a leitura do dia. A manchete é o achado de maior peso, com o número e o
  link **Ver a prova**; abaixo vêm os demais achados e o mapa do que tem em cada aba.
- **Ministros**: a matriz **ministro × área**, com a parte do acervo de cada gabinete em
  cada área. A linha em itálico traz a média do grupo (a Turma no STJ, o tribunal no STF).
  ▲ e ▽ marcam onde o gabinete está **bem acima ou bem abaixo dos pares**. A marca só
  aparece onde passa no teste (binomial exata, 99%, Holm) **e** a diferença é grande:
  1,25 vez ou mais (ou 0,8 ou menos) e 2 pontos ou mais. Há filtro por Seção ou grupo.
  Clicar no nome abre a **mesa** do ministro, um dossiê com:
  - de que é o acervo, contra a Turma;
  - o que espera há mais tempo;
  - quem mais aparece, de que lado;
  - o detalhe por subárea e recorte;
  - as classes;
  - onde está o acervo;
  - há quanto tempo;
  - o que está em pauta;
  - o que entrou e o que decidiu;
  - os temas de que é relator.
- **Assuntos**: de que se ocupa o tribunal, área por área. Mostra as subáreas, os
  assuntos mais frequentes da Tabela Processual Unificada do CNJ e quem cuida de cada um.
  Inclui os **recortes** que atravessam áreas: bancário, planos de saúde, dano moral,
  execução fiscal, recuperação judicial, drogas e improbidade.
- **Partes**: os litigantes que mais se repetem, de que lado e em quantas mesas. No recurso,
  "recorre" e "responde"; nas ações originárias (ADI, HC, MS, rescisória, reclamação),
  "polo ativo" e "polo passivo". O total inclui quem é só interessado (o Ministério Público
  no habeas corpus). Também mostra a parte do acervo com **dinheiro público num dos polos**
  (União, Fazenda Nacional, INSS, Estado ou DF, Município; o MP, a Defensoria e as autarquias
  não entram).
- **A fila**: o que espera há mais tempo e na mesa de quem.
  - No STJ, conta "sem decisão terminativa registrada há N dias": da última distribuição ou
    da última decisão terminativa, a mais recente. O arquivo do STJ só registra a decisão
    que encerra o processo ou o recurso; liminares e despachos não entram. Redistribuição e
    atribuição zeram a conta: quem chegou à Turma ou herdou acervo há menos de um ano
    aparece com "o relógio recomeçou", com a data, sem o número.
  - No STF, conta "concluso há N dias", quando o andamento atual é a conclusão ao relator.
  - Com a camada do CNJ (DataJud), "concluso há N dias (CNJ)" no STJ, com o selo e a
    cobertura (ver abaixo).
  - O que saiu no DJe (DJEN), por ministro e por área, quando a máquina do dono coletou.
- **Como ler**: as definições, as fontes com a data de cada uma, o que a página não
  mostra e por quê.

Cada achado, cada mesa e a lista de quem se repete têm o link **Contestar este número**,
que abre uma issue no repositório com o id do achado ou do ministro no título (nunca nome
de parte nem número de processo).

**O que a página não mostra, e por quê.**
- **Valor da causa**: nenhuma fonte aberta dos dois tribunais publica esse campo. No lugar
  dele, a página mostra o dinheiro público num polo, a liminar (no STJ, quantos chegaram
  com pedido de liminar, decidida ou não; no STF, quantos têm liminar ou tutela pendente) e
  os recortes econômicos.
- **Nomes de pessoas físicas**: nunca aparecem. MEI e empresário individual ("Fulano - ME",
  "12.345.678 Fulano") contam como pessoa física.
- **Processos em segredo de justiça**: entram no acervo, nas áreas e no dinheiro público,
  mas nunca são listados e ficam fora de "quem se repete".
- **Advogados, autoridades e qualificadores** ("e outros", "representado por") ficam fora
  de "quem se repete".

### As contas

- **Na mesa** é o processo em tramitação cujo relator está no painel e que está fisicamente
  no gabinete:
  - no STJ, o `localProcesso` do acervo é o gabinete do relator;
  - no STF, a "Localização Atual" é "GABINETE MINISTRO X".
  A Presidência, a Vice, os processos sem relator e os de quem deixou a Turma aparecem em
  linhas próprias, fora da conta dos ministros.
- **Uma área por processo.** A área vem do **código** do assunto principal na TPU do CNJ,
  nunca do texto. A classe vale antes (ADI/ADC/ADO/ADPF → Constitucional; HC, RHC, APn…
  → Penal), salvo o HC e o RHC cujo mérito é de Família ou de Saúde (prisão civil por
  alimentos, guarda, acolhimento, internação compulsória), que ficam na área do mérito. Depois vale o nó mais específico listado na árvore e, por fim, a raiz. Quando o
  assunto principal é só de rito, vale o primeiro assunto de mérito; sem nenhum, o processo
  fica em "Só processual", à mostra. No STF, os assuntos vêm em texto, e o caminho é casado
  com a árvore da TPU nível a nível (98,98% das linhas casam até a folha).
- **Partes.**
  - No STJ, os litigantes são agrupados pela **raiz do CNPJ**.
  - No STF, o agrupamento é por padrões de nome do catálogo, porque a planilha cola os nomes.
  - União, Fazenda Nacional, INSS, Estados/DF e Municípios contam como **dinheiro público**.
  - Sindicato, associação, partido e OAB são pessoa jurídica, mesmo com um ente no nome;
    "empresa" só para as categorias empresariais do catálogo.
  - "Quem se repete" conta sobre a própria base: sem os processos em segredo e sem os
    precatórios (Prc, RPV), em que o requerente é o credor. A porcentagem de cada
    litigante é sobre essa base (`meta.json → cobertura.<trib>.partes.base`).
- **Comparações.** No STJ, o que entra na Turma vem por sorteio ou prevenção (as atas não
  distinguem), então um gabinete muito acima dos colegas numa área diz algo sobre o que
  ficou: o que não foi decidido, o que veio por prevenção, o que foi herdado. Não diz nada
  sobre escolha ou especialidade.
  - Recém-chegados (menos de 6 meses) e acervos pequenos ficam fora do teste.
  - No STF, em que os pares são o tribunal inteiro, o gabinete que fica bem acima numa área
    sai da conta dos outros naquela área (as ações penais que um ministro concentra por
    prevenção não marcam os demais "abaixo").
  - O selo de **herança** marca quem recebeu acervo por atribuição. A atribuição aparece
    nas atas e não soma no que o ministro recebeu por distribuição.
  - O selo **recebe menos processos** marca quem recebe menos da metade da mediana da Turma
    por distribuição (quem acumula o TSE, por exemplo): ele sai da comparação da mesa.
  - A comparação da mesa (há quanto tempo) deixa de fora, dos dois lados, quem chegou à
    Turma ou herdou acervo há menos de um ano e quem recebe menos por distribuição.
- **Achados**: um por ministro no máximo, com peso declarado em
  `limiares.pesos_achados`: herança, mesa antiga, fora do padrão, litigante, tema, tema
  quente, vista e entrou. A manchete é o de maior peso.

### Dados e atualização

- **Fontes** (públicas, sem chave). O `robots.txt` de cada host é lido e respeitado, com o
  ritmo que ele pede (10 s entre pedidos no STJ).
  - **STJ**: Portal de Dados Abertos (`dadosabertos.web.stj.jus.br`, licença CC-BY), com:
    - o acervo em tramitação, diário, um registro por processo;
    - as atas de distribuição;
    - as pautas;
    - os precedentes qualificados;
    - as íntegras das decisões do Diário da Justiça.

    Os endereços de download são lidos da página HTML de cada conjunto. A API do CKAN
    (`/api/`) é proibida pelo robots e não é usada.
  - **STF**: a planilha do acervo (`Acervo_STF_Lista_Geral.xlsx`, diária), as atas de
    distribuição, a pesquisa de jurisprudência e a lista de temas de repercussão geral.
  - **CNJ**: as Tabelas Processuais Unificadas, a árvore de assuntos e classes, guardada em
    `data/tribunais/tpu/` e baixada de novo só quando a versão muda.
  - **CNJ, DataJud** (API Pública): ligado e **publicado**, por decisão do dono
    (`datajud.ativo: true`, `datajud.publicar: true`). Os movimentos dos processos na mesa
    do STJ dão o "concluso há N dias (CNJ)": a última Conclusão (código 51 da TPU), só quando
    ela veio depois do último ato do magistrado e não é anterior à última distribuição ou
    decisão que o acervo registra. A camada vai em `data/tribunais/cnj.json`, com o selo
    "CNJ · DataJud" em cada número, a cobertura (quantos processos já têm o estado no CNJ) e
    um parágrafo no "Como ler"; as faixas de 180 dias e de 1 ano de cada ministro só mostram
    número com 80% ou mais dos candidatos com data. A carga anda em fatias (lotes de 1.000
    números, com o serviço quase sempre sobrecarregado: 429 e 504); o estado fica no cache
    (`<cache>/datajud/`). Decisão do dono (23/11/2026), sabendo que o repositório é público:
    "Quero os números do DataJud no site sim. Está público apenas pra eu ter fácil acesso,
    mas só eu uso." O termo de uso pede dar ciência ao CNJ de informação disponibilizada ao
    público (item 3.9): isso fica com o dono. `publicar: false` volta ao uso só pessoal (a
    camada fica no cache, em `<cache>/datajud/camada.json`, e nada do DataJud entra em
    `data/tribunais/`); a variável `QIJ_DATAJUD_PUBLICAR` (`1` ou `0`) troca isso numa
    execução. Carga avulsa, para encher a mesa mais depressa:
    `python -m qijournal.tribunais.datajud --cache .cache/tribunais --fatias 6`.
  - **CNJ, DJEN** (o DJe do STJ): coletado só na máquina do dono, no Brasil (ver abaixo),
    e lido aqui do `data/tribunais/djen.json`.
  - **Fora, de propósito**: a consulta processual dos dois tribunais (`processo.stj.jus.br`,
    `portal.stf.jus.br/processos`) não é coletada; o link do processo leva até lá.
- **Quando roda**: o fluxo **Tribunais** (`.github/workflows/tribunais.yml`) roda depois
  de cada edição diária na `main`, e às 22:41 de Brasília como reserva. Antes, o job
  *decidir* lê `data/tribunais/meta.json`: se nada novo é esperado das fontes
  (`proxima_execucao`), termina em segundos.
  - O STJ publica o acervo do dia à tarde do dia seguinte (o de sexta sai no sábado; o de
    segunda, na terça) e a ata por volta das 19:30. A validade do acervo do STJ conta de
    segunda a sexta (`dias_da_semana` no config).
  - Uma atualização comum leva poucos minutos.
  - A carga completa leva cerca de 1 hora: 12 meses de íntegras e 30 dias de atas. Ela
    roda sozinha quando o cache do Actions está vazio (o primeiro dia na `main`, ou depois
    de o cache expirar): sem isso, a primeira execução trocaria o painel de 12 meses por
    um de uma semana. Mesmo assim, a série que ainda cobre menos que a publicada não a
    substitui (fica o bloco publicado, com a fonte dele no `meta.json`). **Depois do
    merge, rode *Actions → Tribunais → Run workflow → Carga completa*.**
  - Toda execução tem prazo (25 min; 70 na carga completa). Um portal mudo põe o host
    fora até o fim da execução (o disjuntor), e perto do prazo os pedidos encurtam; o que
    faltou fica para a próxima.
  - O que foi baixado fica num cache do Actions, perto de 600 MB com as íntegras. A poda
    roda em toda execução e nunca apaga o que um estado cita (o acervo em uso, o socorro
    de cada fonte, as séries).
- **Falhas**: fonte que recusa (403 do Cloudflare), está fora do ar ou devolve dado
  parcial nunca apaga o último dia bom. Vira aviso na página e o resto sai.
  - Fonte que passa da validade (`velho_dias`, contada da data que a própria fonte
    declara) abre uma **issue** para o dono.
  - Relator desconhecido no acervo também abre issue: é sinal de que a composição em
    `config/tribunais.yaml` precisa ser atualizada.
  - Só erro de programa deixa a execução vermelha.
- **Um escritor por pasta**: a edição diária nunca grava em `data/tribunais/` nem em
  `edicoes/tribunais.html`; só o fluxo Tribunais grava, num commit próprio. A edição só
  confere se a aba existe para acender o botão.
- **À mão**: *Actions → **Tribunais** → Run workflow*. As opções:
  - **Carga completa**: refaz as séries e confere a TPU;
  - **Só um tribunal**;
  - **Ensaio**: coleta e refaz a aba sem publicar; o resultado fica como artefato;
  - **Aceitar o acervo**: aceita uma vez o acervo do dia fora da faixa da sanidade (mais
    de 15% de diferença), depois de conferir que a mudança é real.
- **No computador**:

```bash
python -m qijournal tribunais atualizar --dir . --cache .cache/tribunais   # coleta e refaz
python -m qijournal tribunais atualizar --dir . --completo --so stj        # carga completa, só o STJ
python -m qijournal tribunais atualizar --dir . --aceitar-acervo stj       # aceita o acervo recusado pela sanidade
python -m qijournal tribunais pagina --dir .                               # só a página, sem rede
QIJ_DATAJUD_PUBLICAR=0 python -m qijournal tribunais atualizar --dir .     # nesta execução, o DataJud só no cache
```

- **Onde fica** (`data/tribunais/`, gerado):

| Arquivo | O quê |
|---|---|
| `meta.json` | por fonte: a data que a fonte declara (`data_dados`), quando foi coletada, se está ok e o aviso; no topo, `atualizado_em` e `proxima_execucao`; a cobertura de cada tribunal (quantos processos, quantos casaram com a TPU, como a área foi decidida, as partes reconhecidas) e as réguas usadas |
| `stj.json`, `stf.json` | o painel de cada tribunal: a matriz ministro × área com as comparações, a mesa, os assuntos, as partes, a fila, as pautas e os temas |
| `ministros/<trib>-<nome>.json` | o dossiê de cada ministro (carregado quando a mesa abre) |
| `achados.json` | a leitura do dia, com o peso de cada achado |
| `cnj.json` | a camada do DataJud (só com `datajud.publicar`): o estado de cada processo listado no STJ, as faixas de "concluso há mais de 180 dias e de 1 ano" por ministro e a cobertura |
| `djen.json` | o DJe (DJEN), escrito só pela máquina do dono, no Brasil: as publicações do último dia e dos 30 dias por ministro, por Turma e por área, e os processos da mesa que saíram no DJe (contrato em `qijournal/tribunais/djen.py`) |
| `tpu/` | a árvore de assuntos e classes da TPU do CNJ e a versão dela |

- **Desligar**: `tribunais.enabled: false` em `config/site.yaml`, ou a variável
  **`QIJ_NO_TRIBUNAIS`** = `1` (o botão some; os dados ficam).
- **Código**: `qijournal/tribunais/`:
  - `fontes.py`: coleta, robots, ritmo e cache;
  - `stj.py` e `stf.py`: os leitores de cada tribunal;
  - `tpu.py` e `areas.py`: a TPU e a classificação por área;
  - `partes.py`: os litigantes;
  - `contas.py`: o painel e os dossiês;
  - `achados.py`: a leitura do dia;
  - `datajud.py`: o DataJud (movimentos, conferência e a carga avulsa);
  - `djen.py`: o DJEN, que só roda na máquina do dono (com `scripts/djen/`);
  - `dados.py`: a atualização;
  - `cli.py`: os comandos;
  - `modelo.py`: os tipos e a leitura do config.

  A página fica em `qijournal/render/tribunais.py`, com os modelos `tribunais*.j2`.

### DJe (DJEN) na máquina do dono

Desde 29/11/2024, as decisões, os despachos, as ementas de acórdão, as intimações e a distribuição do STJ
são publicados no **DJEN**, o Diário de Justiça Eletrônico Nacional do CNJ (Res. CNJ 455/2022 e
Res. STJ/GP 19/2024). O DJe próprio do STJ ficou só com os atos administrativos. É a fonte do dia
para saber **o que saiu de cada gabinete**. As íntegras dos dados abertos trazem o mesmo conteúdo,
mas com semanas de atraso.

A API pública de consulta do DJEN (`comunicaapi.pje.jus.br`) **recusa quem está fora do Brasil**
(403 geográfico), e os executores do GitHub Actions estão fora. Por isso a coleta roda **numa máquina
do dono, no Brasil**, por uma tarefa agendada, e envia só `data/tribunais/djen.json`, num commit
próprio. Nada contorna a recusa: não há proxy, VPN, navegador nem troca de origem. O pedido sai com o
User-Agent do projeto, depois da leitura do `robots.txt`, com 2 s entre pedidos. **A edição e a aba
nunca dependem disso**: sem `djen.json` novo, a aba sai sem a camada do DJe.

O STF tem DJe próprio (o STF Digital) e, até onde se sabe, não publica no DJEN. A sonda confere. O
"o que saiu" do STF vem da pesquisa de jurisprudência, que já é coletada no Actions.

**O que sai** (`data/tribunais/djen.json`, cerca de 100 KB; contrato em `qijournal/tribunais/djen.py`):

- as publicações do último dia e dos 30 dias, por ministro, por Turma ou Seção e pela Presidência;
- por tipo: decisão, acórdão, despacho, pauta, distribuição e edital;
- por área: a do processo no acervo, ou a da classe;
- **os processos que estavam na mesa do relator e tiveram publicação**, casados pelo número único com o
  acervo do dia, com os que estavam parados há mais tempo;
- as partes por grupo de litigante (União, INSS, Estados, bancos...).

Algumas coisas nunca saem:

- **Pessoa física e MEI** ficam sem nome, só na contagem.
- **Advogado** nunca aparece, nem o nome nem a OAB.
- **Processo em segredo** conta nos números e não é listado.
- **O texto das publicações** é lido para achar o relator e o tipo e depois descartado: não fica nem no
  cache.

**Ver se funciona agora (do celular ou do computador, no Brasil, sem instalar nada).** Abra estes dois
endereços. Troque a data por um dia útil recente.

- STJ: <https://comunicaapi.pje.jus.br/api/v1/comunicacao?pagina=1&itensPorPagina=5&siglaTribunal=STJ&dataDisponibilizacaoInicio=2026-11-20&dataDisponibilizacaoFim=2026-11-20>
- STF: <https://comunicaapi.pje.jus.br/api/v1/comunicacao?pagina=1&itensPorPagina=5&siglaTribunal=STF&dataDisponibilizacaoInicio=2026-11-20&dataDisponibilizacaoFim=2026-11-20>

Na resposta (um texto JSON), confira cinco pontos:

1. **Abriu um JSON?** Ele começa com `{"status":"success"`. Se aparecer *"configured to block access from
   your country"*, a rede não está saindo pelo Brasil.
2. **`"count"`**: quantas publicações o tribunal teve no dia. No STJ, espera-se de milhares a dezenas de
   milhares. Com `0`, o tribunal não publicou nesse dia. Se o STF der `0` em vários dias, ele não usa o DJEN.
3. **`"siglaTribunal"`** de cada item tem de ser `"STJ"` (ou `"STF"`). Se vier `"TJSP"`, `"TRT2"`..., o
   filtro foi ignorado. Avise: a coleta para sozinha nesse caso.
4. **`"tipoDocumento"`** e **`"nomeOrgao"`**: que tipos aparecem (Decisão, Acórdão, Despacho...) e como o órgão
   vem escrito (por exemplo, "GABINETE DO MINISTRO ..." ou "TERCEIRA TURMA").
5. **`"texto"`**: se tem a linha "RELATOR: MINISTRO ...".

**Instalar (uma vez).** Precisa de Python 3.11 ou mais novo e do git.

```bash
# 1. Um clone só para o robô, com permissão de envio. O melhor é uma chave de implantação
#    (Settings → Deploy keys → Add deploy key → "Allow write access"), que vale só para este repositório.
git clone git@github.com:pbcvphyton/TriangulaTech.git ~/triangula-djen
cd ~/triangula-djen
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 2. A sonda: poucos pedidos. Diz se a API responde, se o STJ e o STF publicam, quantos itens houve
#    no último dia útil e que tipos de documento vieram. Grava uma amostra mascarada.
scripts/djen/rodar_djen.sh --sondar

# 3. A tarefa agendada: às 8h17 e às 20h47. No macOS usa o launchd (com o Mac dormindo, roda ao
#    acordar); no Linux, o systemd do usuário (Persistent=true roda o que se perdeu) ou o cron.
scripts/djen/instalar.sh
```

No Windows (PowerShell, sem administrador):

```powershell
git clone git@github.com:pbcvphyton/TriangulaTech.git $HOME\triangula-djen
cd $HOME\triangula-djen
py -3 -m venv .venv; .venv\Scripts\pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File scripts\djen\rodar_djen.ps1 -Sondar
powershell -ExecutionPolicy Bypass -File scripts\djen\instalar_windows.ps1
```

**Como funciona a tarefa** (`scripts/djen/rodar_djen.sh` e `rodar_djen.ps1`):

1. Põe o clone igual à `main`. Se o clone tiver alteração local, recusa rodar, porque é um clone só
   do robô.
2. Roda `python -m qijournal tribunais djen`.
3. Faz commit **só** de `data/tribunais/djen.json`.
4. Envia. Se o envio for recusado porque os robôs do GitHub publicaram no meio, reaplica o commit
   sobre o remoto e tenta de novo, até 5 vezes.
5. Sem nada novo, não faz pedido nenhum à API e não faz commit.

Uma execução por vez. O log de cada dia fica em `~/.cache/triangula-djen/logs/` (no Windows,
`%LOCALAPPDATA%\triangula-djen\logs\`) e é guardado por 30 dias.

**Volume e ritmo.**

- Por execução, entram até 5 dias novos, com 100 publicações por página e 2 s entre pedidos. A janela de
  30 dias enche em uma semana; `--completo` enche de uma vez.
- Um dia do STJ deve dar dezenas de páginas, poucos minutos.
- Para casar com o acervo, a máquina baixa uma vez por dia o arquivo aberto do acervo do STJ (82 MB,
  10 s de pausa no portal, CC-BY) e a planilha do STF. Isso leva cerca de 30 s de leitura, e só o
  retrato compacto fica guardado.

**À mão:**

```bash
python -m qijournal tribunais djen --sondar --cache ~/.cache/triangula-djen     # só a sonda
python -m qijournal tribunais djen --dir . --cache ~/.cache/triangula-djen      # coleta e grava o djen.json
python -m qijournal tribunais djen --dir . --completo --so stj                  # a janela inteira, só o STJ
python -m qijournal tribunais djen --dir . --sem-acervo                         # sem a mesa (não baixa o acervo)
```

**Desligar**: rode `scripts/djen/instalar.sh --remover` ou `instalar_windows.ps1 -Remover`, ou ponha
`djen.ativo: false` em `config/tribunais.yaml`. A aba segue sem a camada.

**Por que não um executor próprio do GitHub Actions (self-hosted) na máquina do dono.** O repositório é
público. Um *pull request* vindo de fork pode trazer um fluxo próprio com `runs-on: [self-hosted, brasil]`
e rodar código na máquina do dono. As proteções existem, mas cada uma deixa uma brecha:

- *Settings → Actions → General → Fork pull request workflows*: "Require approval for all outside
  collaborators". Basta um clique de aprovação por engano.
- Fluxo do DJEN só com `schedule` e `workflow_dispatch`. Isso não impede que um PR traga outro fluxo.
- Executor num usuário sem privilégios, de preferência efêmero (`--ephemeral`) e numa máquina virtual.

O GitHub recomenda não usar executor próprio em repositório público. A tarefa agendada roda só o que
está na `main`, a mesma confiança de quem clona e usa o projeto.


## Receber por e-mail

O e-mail segue o modelo do e-mail do **TSA Tech**: a faixa do cabeçalho (o logo
sobre o marinho com a rede do site), o slogan e a data; **"Hoje em 6 notícias"**,
com a manchete em destaque (editoria, título, linha fina e veículos) e as outras
cinco em uma linha de duas colunas e outra de três (número, editoria, título e
quantos veículos), uma por editoria quando dá, na ordem da primeira página; **"Ler
a edição completa"** e o total de notícias do dia (link para a lista completa); e
um rodapé curto (marca, slogan, fontes, edições anteriores, Instagram). Cada título
abre a matéria na edição. O HTML é sempre claro, tem 600 px e cerca de 10 KB; no
celular, as colunas ficam uma embaixo da outra. A versão em texto puro traz a
manchete, as principais notícias com os links e os links da edição. A quantidade
de notícias além da manchete fica em `email.max_stories` (`config/site.yaml`,
padrão 5). Há dois caminhos, que podem ser usados juntos ou separados.

### Opção 1 — Rotina diária do Claude (Gmail) — já configurada

Uma rotina agendada do Claude, com o Gmail conectado, roda todo dia às
**06:54 (Brasília)** e envia o e-mail do dia para o endereço cadastrado nela (o
endereço fica só na rotina, não no repositório, que é público). Se a edição do
dia ainda não tiver saído (agendamento do GitHub atrasado), ela mesma dispara o
workflow (`repository_dispatch` com `event_type` `edicao`), espera a edição sair
(alguns minutos) e envia. Se ainda assim não sair, tenta de novo às **09:04**;
só então, se a edição não saiu, manda um aviso curto. Nunca envia duas vezes no
mesmo dia. Para pausar, mudar o horário ou
apagar: lista de *Routines* do Claude Code em <https://claude.ai/code>.

Ela usa os arquivos que o próprio jornal gera:

- `edicoes/latest.json` — dados da última edição (data, assunto, manchete, links);
- `edicoes/email.html` e `edicoes/email.txt` — o e-mail pronto. O HTML é
  enxuto (até 40 KB; matérias saem do e-mail se passar disso), porque a rotina o
  copia inteiro no parâmetro `htmlBody` do Gmail.

O que a rotina faz:

1. Lê `latest.json` em
   `https://raw.githubusercontent.com/pbcvphyton/TriangulaTech/main/edicoes/latest.json`
   (os campos `email_html_raw_url` e `email_text_raw_url` apontam para o e-mail
   no mesmo lugar; `email_html_url` é a cópia no GitHub Pages).
2. Se `date` for a data de hoje em `America/Sao_Paulo`: não envia se
   `email_sent` for `true` (já saiu por SMTP) ou se um e-mail com o mesmo
   assunto já estiver na pasta *Enviados* do Gmail; senão, envia com `subject`
   como assunto, `email.html` como corpo HTML e `email.txt` como texto.
3. Se `date` **não** for de hoje (a edição do dia não saiu), manda só um aviso
   curto com o link das execuções no GitHub, em vez de reenviar a edição de ontem.

O campo `email_sent` sobrevive a uma nova execução no mesmo dia (inclusive com
*Não enviar o e-mail*), então refazer a edição de manhã não dispara um segundo
envio por SMTP.

### Opção 2 — Envio direto por SMTP (Gmail com senha de app)

O próprio GitHub envia o e-mail logo após gerar a edição.

1. Na conta Google que vai enviar, ative a **verificação em duas etapas**.
2. Crie uma **senha de app** em <https://myaccount.google.com/apppasswords>
   (16 letras; pode colar com ou sem espaços).
3. Cadastre os segredos em *Settings → Secrets and variables → Actions*:

   | Segredo | Valor |
   |---|---|
   | `SMTP_USER` | o endereço Gmail que envia |
   | `SMTP_PASSWORD` | a senha de app (não a senha normal da conta) |
   | `EMAIL_TO` | quem recebe; vários endereços separados por vírgula |
   | `EMAIL_FROM` *(opcional)* | remetente, se diferente de `SMTP_USER` |
   | `SMTP_HOST` / `SMTP_PORT` *(opcionais)* | outro provedor; padrão `smtp.gmail.com` / `465` (use `587` para STARTTLS) |

4. Teste: *Actions → Edição diária → Run workflow*.

O envio acontece **depois** que a edição foi commitada e o GitHub Pages terminou
de publicá-la (a etapa espera até ~3 minutos), para os links do e-mail já
funcionarem; se a publicação falhar, nenhum e-mail sai. Se o envio falhar (senha
errada, por exemplo), a edição continua publicada e o problema aparece na
etapa *Enviar e-mail*. Rodar de novo no mesmo dia não reenvia o e-mail já
enviado; para reenviar de propósito, use `python -m qijournal send-email
--force-email`. Os destinatários ficam só nos segredos — nunca no repositório,
que é público —, e com vários endereços cada leitor recebe como cópia oculta
(ninguém vê o e-mail dos outros).

---

## Rodar manualmente

### No GitHub

*Actions → **Edição diária** → Run workflow.* Duas opções:

- **Gerar sem IA** — força a edição automática (útil para testar sem custo);
- **Não enviar o e-mail por SMTP**.

Rodar de novo no mesmo dia substitui a edição do dia. Ao final, a aba
*Summary* da execução mostra o modo (IA ou automático), os tokens usados, as
fontes com erro e os links da edição.

*Actions → **Congresso** → Run workflow* atualiza só os dados e a aba do
Congresso (opção **Carga completa**: refaz tudo desde 2019).

*Actions → **Leitura dos projetos** → Run workflow* lê por IA os projetos de lei
novos na hora, mesmo que a leitura do dia já tenha rodado (campo opcional: o teto de
leituras da rodada; `0` só diz o que leria). No dia a dia ela roda sozinha, depois
da edição.

### No computador

Requer Python 3.11 ou mais novo.

```bash
pip install -r requirements.txt
python -m qijournal run --no-email      # gera index.html, edicoes/ e data/ na pasta atual
```

Para ver uma edição de exemplo sem internet e sem IA:

```bash
python -m qijournal render --bundle tests/fixtures/bundle.json --out /tmp/qij --no-llm
# abra /tmp/qij/index.html no navegador
```

Todos os comandos (`python -m qijournal <comando> --help` mostra os detalhes):

| Comando | O que faz |
|---|---|
| `run [--out DIR] [--bundle ARQ] [--no-llm] [--no-email] [--force-email] [--now ISO] [-v]` | coleta, edita, publica e envia o e-mail (SMTP, se configurado; não reenvia o do dia sem `--force-email`) |
| `collect --out ARQ` | só coleta e salva as notícias em JSON |
| `render --bundle ARQ --out DIR [--no-llm]` | gera a edição a partir de uma coleta salva, sem internet e sem e-mail |
| `send-email [--dir DIR] [--force-email]` | envia por SMTP o e-mail da última edição gerada (uma vez por edição, salvo `--force-email`) |
| `check-sources` | testa cada fonte e mostra quais estão respondendo |
| `congresso atualizar [--dir DIR] [--cache DIR] [--completo] [--now ISO]` | baixa os dados da Câmara e do Senado, refaz as contas, `data/congresso/` e a aba Congresso (o que baixa fica em `.cache/congresso`, fora do git) |
| `congresso pagina [--dir DIR]` | só refaz a aba Congresso a partir de `data/congresso/`, sem internet |
| `congresso ler [--dir DIR] [--cache DIR] [--max N] [--now ISO]` | a leitura diária dos projetos de lei: a cadeia de IAs lê as proposições novas e relê as que mudam a conta, e grava em `data/congresso/leitura/` (sem chave de IA, não lê nada; `--max 0` só diz o que leria); no GitHub, roda no fluxo *Leitura dos projetos*, depois da edição |

Códigos de saída: `0` sucesso; `1` erro; `2` dados insuficientes (edição não
publicada) ou argumentos inválidos.

---

## Fontes de notícia

As fontes ficam em [`config/sources.yaml`](config/sources.yaml), uma por linha:

```yaml
- {id: valor, name: "Valor Econômico", url: "https://valor.globo.com/rss/valor/brasil/", lang: pt, weight: 1.3, topics: [brasil]}
```

| Campo | Significado |
|---|---|
| `id` | apelido curto do veículo (pode repetir entre feeds do mesmo veículo) |
| `name` | nome mostrado nos créditos |
| `url` | endereço do feed RSS/Atom |
| `lang` | `pt`, `en` ou `es` |
| `weight` | importância editorial (0,5 a 1,5) — pesa na escolha das matérias |
| `topics` | seções prováveis (`brasil`, `mercados`, `juridico`, `politica`, `mundo`, `tecnologia`, `imobiliario`, `esporte`, `natureza`, `variedades`); em feeds gerais (capas), use `[]` e a dica sai do endereço da matéria (`/internacional/` → `mundo`, `/esporte/` → `esporte`…) |
| `enabled` | `false` desliga a fonte sem apagá-la |
| `exclude_url_patterns` | trechos de endereço a ignorar nessa fonte |

- **Adicionar:** acrescente uma linha e rode `python -m qijournal check-sources`
  para confirmar que o feed responde e tem notícias recentes.
- **Remover:** apague a linha ou use `enabled: false`.

Seções, palavras-chave, cotações do ticker, cidades do clima (hoje só São Paulo;
Montevidéu saiu em nov/2026: o clima da linha de indicadores mostra só a primeira cidade configurada,
também nas edições refeitas) e limites da edição
(quantidade de matérias, mínimos, idade máxima das notícias) ficam em
[`config/site.yaml`](config/site.yaml), com comentários explicando cada item.

---

## Trocar a marca

Há duas marcas prontas: **Triangula** (`tech`, padrão; antes PBCV Tech) e **PBCV Advogados**
(`pbcv`), cada uma com logo próprio em `assets/`. Para trocar:

- de forma permanente: em `config/site.yaml`, mude `brand: tech` para `brand: pbcv`; ou
- sem mexer no código: crie a variável **`QIJ_BRAND`** = `pbcv` em
  *Settings → Secrets and variables → Actions → Variables*.

Cores, nome, slogan e logo de cada marca ficam em `config/site.yaml → brands`.
Uma `QIJ_BRAND` com marca inexistente (como o antigo `qi`) cai na marca padrão.

A marca Triangula (até out/2026, PBCV Tech) usa o logo **Editorial** (projeto
*Triangula — Marca 2026* no Claude Design): "triangula" em minúsculas na
Newsreader (wght 600, opsz 72; SIL Open Font License), com o pingo do i trocado
por um triângulo laranja. As letras são contornos (o logo não depende de fonte
carregada na página) e o ícone é o triângulo laranja sobre o marinho. A chave da
marca continua `tech` (o termômetro depende dela). Desde out/2026 o jornal usa
a estrutura visual do TSA Tech: página na largura toda da tela (margens em
`--gut`, que crescem nas telas largas), cabeçalho em bloco marinho `#273351`
(`colors.masthead`, com data e hora à esquerda do logo no computador) e, desde
nov/2026, a home em três andares (ver
[A home](#a-home-três-andares-cada-um-com-teto)): uma capa só, que se rearruma
pela largura, com a manchete ao lado de "Em 1 minuto" a partir de 1280 px. A
paleta vem do quadro do Sinal: marinho `#1B2745` (linha de indicadores, rodapé,
títulos), ardósia `#394A7A` (links e numerais), laranja de sinal `#FF5A1F` (o
triângulo do logo e a marca de divergência; só decoração no fundo branco) e
altas/quedas das cotações em `#7FC8FF`/`#FF5C9A`, com os cinzas azulados do
layout original.

- `assets/triangula-marca-cabecalho.svg` — logo do cabeçalho, com o letreiro em
  `currentColor` (branco sobre o bloco, nos dois modos) e o triângulo laranja
  (`logo_height: 64`, `logo_height_mobile: 46`);
- `assets/triangula-marca.svg` — o mesmo logo em marinho, para fundos claros;
- `assets/triangula-favicon.svg` — o ícone: triângulo laranja sobre o marinho;
- `assets/triangula-cabecalho-email.jpg` — faixa do topo do e-mail, no modelo do
  TSA Tech: o logo sobre o marinho do cabeçalho com a rede do site, em 2x (1200 ×
  264, exibida em 600 × 132), servida pelo GitHub Pages (`email_header`);
- `assets/triangula-marca-email.png` — logo do e-mail (clientes de e-mail não
  exibem SVG), branco sobre a cor do cabeçalho, em 3x (248 × 64), servido pelo
  GitHub Pages (`email_logo`; vale quando não há `email_header`). Os logos anteriores (em pixel: `triangula-logo*`,
  `pbcv-tech-*`) ficam no repositório com os mesmos nomes e tamanhos porque os
  e-mails já enviados apontam para eles.

Outra marca pode usar cabeçalho em bloco de cor com `colors.masthead` e
`colors.on_masthead` (logo em `currentColor`).

---

## Instagram

Depois de cada edição, o workflow **Instagram** gera os posts do dia e os publica pela API oficial do Instagram, cada um no seu horário (3 posts de feed por dia, sempre na mesma ordem de cor):
- 07:00, "Em 1 minuto" (marinho), e os stories da manhã logo depois; nos dias sem IA, o "Terminal da manhã";
- 12:00, "Número do dia" (laranja chapado);
- 18:30, "Raio-X" (papel), ou o "Placar do dia", ou o "Mercado em linha";
- uma vez, o Manifesto, para fixar no perfil.

Cada peça passa por um lint antes de sair e, reprovada, cai para um substituto; as séries são numeradas ("RAIO-X Nº 014"). Todo o texto vem do JSON da edição, de contas e de moldes, sem IA nova. As imagens e a fila de publicação ficam na branch `instagram`. A configuração da conta e dos segredos é feita uma vez: veja **[docs/instagram.md](docs/instagram.md)**.

---

## Segredos e variáveis do GitHub

| Nome | Tipo | Para quê |
|---|---|---|
| `GEMINI_API_KEY` | segredo | editor 1 da cadeia (Gemini Flash e Flash-Lite, grátis; a mesma chave do TSA Tech) |
| `AIMLAPI_KEY` | segredo | editor 2 (AIML, grátis) |
| `SENSENOVA_API_KEY` | segredo | editor 3 (SenseNova, grátis) |
| `MISTRAL_API_KEY` | segredo | editor 4 (Mistral, grátis) |
| `MOONSHOT_API_KEY` | segredo | editor 5 (Kimi, pago) |
| `ANTHROPIC_API_KEY` | segredo | editor 6 (Claude, pago) |
| `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_TO` | segredos | envio do e-mail por SMTP |
| `EMAIL_FROM`, `SMTP_HOST`, `SMTP_PORT` | segredos (opcionais) | ajustes do SMTP |
| `IG_USER_ID`, `IG_ACCESS_TOKEN` | segredos | publicação no Instagram ([guia](docs/instagram.md)) |
| `GH_SECRETS_TOKEN` | segredo | grava o token do Instagram renovado toda semana |
| `IG_API_VERSION`, `IG_API_HOST`, `IG_PAUSE` | variáveis (opcionais) | versão e host da API do Instagram; `IG_PAUSE=true` pausa as publicações |
| `QIJ_BRAND` | variável | marca (`tech` ou `pbcv`) |
| `QIJ_NO_TERMOMETRO` | variável | `1` desliga o Termômetro de Viés e a aba Analítico Político |
| `QIJ_NO_CONGRESSO` | variável | `1` desliga a aba Congresso e as fichas nas matérias |
| `QIJ_NO_TRIBUNAIS` | variável | `1` desliga a aba Tribunais (STF e STJ) |
| `DATAJUD_API_KEY` | variável (opcional) | chave do DataJud do CNJ (o padrão é a chave pública, em `config/tribunais.yaml`; esta a troca quando o CNJ a mudar) |
| `QIJ_DATAJUD_PUBLICAR` | variável de ambiente (opcional) | `1` publica a camada do CNJ (`cnj.json`) numa execução, `0` a deixa só no cache; sem ela vale `datajud.publicar` do config (hoje `true`) |
| `QIJ_NO_LEITURA` | variável | `1` desliga a leitura diária dos projetos de lei por IA (`congresso ler`, no fluxo *Leitura dos projetos*) |
| `QIJ_MODEL` | variável | modelo do Claude |
| `QIJ_GEMINI_MODEL`, `QIJ_GEMINI_LITE_MODEL`, `QIJ_AIML_MODEL`, `QIJ_SENSENOVA_MODEL`, `QIJ_MISTRAL_MODEL`, `QIJ_KIMI_MODEL` | variáveis | modelo de cada editor (padrões na tabela de editores acima) |

Todos são opcionais: sem nenhum deles, o jornal é gerado no modo automático e
publicado normalmente.

---

## Quando algo dá errado

- **A execução falhou com código 2:** poucas fontes responderam; nada foi
  publicado e a edição anterior continua no ar. A execução da hora seguinte
  tenta de novo sozinha; também dá para rodar à mão (*Run workflow*).
- **Outros erros:** o GitHub avisa por e-mail o dono do repositório. O log da
  etapa *Gerar a edição* mostra a causa.
- **Investigar uma edição:** cada execução guarda por 7 dias a coleta crua e as
  páginas enriquecidas (artefato `coleta-…` na página da execução, com
  `bundle-AAAA-MM-DD.json` e `pages-AAAA-MM-DD.json`). Para reproduzir a edição
  automática, com o mesmo enriquecimento (o `pages-*.json` ao lado do bundle é
  lido sozinho) e as regras de limpeza atuais:
  `python -m qijournal render --bundle bundle-AAAA-MM-DD.json --out /tmp/x --no-llm`.
  A edição por IA não é reproduzível (o modelo pode escolher e escrever
  diferente a cada chamada).
- **O passo *Atualizar o Congresso* falhou ou demorou:** a edição sai do mesmo
  jeito, com os dados do Congresso da véspera (o passo é acessório e tem 15
  minutos). O *Summary* da execução lista os avisos (fonte fora do ar, por
  exemplo). Para refazer: *Actions → Congresso → Run workflow*.
- **A leitura dos projetos de lei não leu ou não publicou:** a edição não depende
  dela. O *Summary* da execução do fluxo *Leitura dos projetos* diz o que foi lido, o
  motivo quando nada foi lido ("sem IA: nada lido", o prazo, o teto de chamadas) e os
  avisos. Se ela leu e não conseguiu publicar (o passo *Publicar a leitura* avisa), a
  execução seguinte lê de novo. Se o aviso é "Sem o cache do Congresso", o fluxo não
  achou o que a *Edição diária* baixou (o cache do Actions expirou ou não foi gravado)
  e não leu, para não baixar tudo de novo a cada hora: a leitura volta sozinha depois
  da próxima edição, ou de uma execução do fluxo *Congresso*, que gravam o cache. Para
  ler na hora: *Actions → Leitura dos projetos → Run workflow*.
- **O agendamento atrasou ou não rodou:** o GitHub atrasa ou descarta
  agendamentos (em outubro de 2026, o das 05:07 chegava entre 10h e 14h). Por
  isso o workflow é agendado de hora em hora: a primeira execução que chega a
  partir das **05:00** (Brasília) gera a edição do dia, e as demais terminam em
  segundos. A rotina do e-mail também dispara o workflow às 06:54 e às 09:04 se
  a edição ainda não saiu (`repository_dispatch`, sem depender da fila de
  agendamentos). Mudar o `daily.yml` na `main` também gera a edição do dia, se
  ela ainda não saiu.
- **O agendamento parou:** o GitHub desativa agendamentos de repositórios sem
  atividade por 60 dias (o commit diário da edição evita isso). Reative em
  *Actions → Edição diária → Enable workflow*.

### Tribunais: quando algo dá errado

A issue "Tribunais: dados parados ou fonte recusada" aponta para cá. A aba continua no ar
com o último dado bom de cada fonte e diz de quando ele é.

- **Fonte recusada (403) ou fora do ar:** nada é contornado. O portal do STJ recusa por
  taxa às vezes (Cloudflare) e libera em minutos; a execução seguinte tenta de novo. Se a
  recusa durar dias, veja o diário da coleta (artefato `tribunais-diario-…`: host, status
  e tempo de cada pedido).
- **Acervo recusado pela sanidade** ("trouxe N processos, X% dos M"): o total do dia saiu
  da faixa de 85% a 115% do último dia bom. Confira no portal do tribunal se a mudança é
  real; se for, *Run workflow* com **Aceitar o acervo** = `stj` ou `stf` (uma vez). Se o
  arquivo veio quebrado, não faça nada: o dia seguinte resolve.
- **Relator fora da composição:** um ministro novo, um convocado ou uma troca de Turma.
  Atualize `composicao` em `config/tribunais.yaml` (o PDF da composição do tribunal diz
  quem é quem). Até lá, os processos dele contam como resíduo.
- **Cache frio ou carga completa:** a execução com o cache vazio já faz a carga completa
  sozinha (até 75 min). Se ela não couber, as seguintes completam; a série que cobre menos
  que a publicada não a substitui.
- **Página acima do teto (1,5 MB):** a execução fica vermelha e nada é publicado (os dados
  novos com a página velha sairiam incoerentes). Reduza as listas em `limiares.listas` do
  config e rode de novo.
- **DataJud sem resposta:** é o normal dele (429 e 504). A camada do CNJ usa os movimentos
  já guardados e a cobertura diz quanto falta.
- **DJe (DJEN) sem atualização:** a tarefa da máquina do dono parou (máquina desligada,
  chave de implantação, rede fora do Brasil). Veja o log em
  `~/.cache/triangula-djen/logs/`. A camada some da aba depois de 4 dias úteis sem coleta.

---

## Estrutura de pastas

```
qijournal/                código do jornal
  collect/                coleta: feeds, páginas das matérias, cotações, clima
  edit/                   edição: agrupamento, IA (llm.py = Claude e fluxo geral;
                          chatapi.py = AIML, SenseNova, Mistral, Kimi; chain.py =
                          cadeia de editores; blocks.py = compilação por
                          editoria; topics.py e coverage.py = modo etapas;
                          index.py = todas as notícias do dia) e modo automático
  render/                 páginas HTML e e-mail (modelos em render/templates/;
                          termometro.py e analitico.py = termômetro do dia e aba
                          Analítico Político; achados.py = a leitura da aba (os
                          achados, o placar dos nomes e a matriz); lados.py = a
                          lente do enquadramento (assuntos divididos, pares);
                          pesquisa.py = Pesquisa analítica; midia.py = nomes em
                          destaque de "Como a mídia cobre"; tom.py = tom das
                          manchetes)
  termometro/             Termômetro de Viés: referencia.py (cálculo, cópia literal
                          da especificação), agregacao.py (aba analítica),
                          analise.py (textos → IA → cálculo) e historico.py
  congresso/              Analítico do Congresso: fontes.py (coleta e cache),
                          base.py (votações nominais), perfil.py (as contas de
                          voto), projetos.py (proposições, relatorias e fila),
                          relevancia.py (o que espera × o que passa),
                          jornal.py (os nomes no banco do jornal), ficha.py (o
                          dossiê), dados.py (a atualização) e cli.py (comandos)
  tribunais/              Analítico dos Tribunais (STF e STJ): fontes.py (coleta,
                          robots e cache), stj.py e stf.py (leitores), tpu.py e
                          areas.py (TPU e áreas), partes.py, contas.py,
                          achados.py, datajud.py (o DataJud do CNJ), djen.py (o
                          DJEN, só na máquina do dono), dados.py (a atualização)
                          e cli.py
  deliver/smtp.py         envio do e-mail por SMTP
  instagram/              Instagram: formatos/ (um módulo por post do dia),
                          estatistica.py, selecao.py e redacao.py (contas, escolha
                          e lint), gerar.py + templates/ (grade do dia e imagens),
                          fila.py e publicar.py (fila e publicação), graph.py (API)
  pipeline.py             orquestra coleta → edição → páginas → e-mail
  __main__.py             comandos (python -m qijournal …)
config/site.yaml          marca, seções, cotações, clima, limites da edição
config/sources.yaml       fontes de notícia
config/saida_modelo.schema.json, lexico.yaml, atores.yaml  termômetro (formato da
                          saída da IA, dicionário e âncoras de campo)
config/congresso.yaml     as réguas do Analítico do Congresso (polos, governos,
                          limiares, faixas do campo e macrotemas)
config/tribunais.yaml     as réguas do Analítico dos Tribunais (áreas, composição
                          dos tribunais, limiares, fontes, DataJud);
                          tribunais_partes.yaml, o catálogo de litigantes
assets/                   logos e ícones das marcas; fonts/ (fontes das imagens do
                          Instagram); instagram/ (foto de perfil e capas de destaque)
docs/instagram.md         guia do Instagram
scripts/djen/             a tarefa agendada do DJEN na máquina do dono, no Brasil
                          (rodar_djen.sh/.ps1, instalar.sh, instalar_windows.ps1)
index.html                edição do dia (gerado automaticamente)
edicoes/                  arquivo de edições, todas as notícias de cada dia,
                          e-mail e latest.json (gerado)
data/                     cada edição em JSON; em data/termometro/, as análises
                          do termômetro de cada dia, guardadas para sempre; em
                          data/busca/, o que cada edição publicou, para a Pesquisa
                          analítica; em data/congresso/, a base de votos, as
                          fichas e os painéis do Congresso; em data/tribunais/,
                          os painéis e dossiês dos Tribunais, a camada do CNJ
                          (cnj.json) e o DJe (djen.json, da máquina do dono)
tests/                    testes automáticos
.github/workflows/        daily.yml (edição diária), congresso.yml (Congresso, à
                          mão), instagram.yml (posts do Instagram) e ci.yml (testes)
                          e leitura.yml (leitura dos projetos de lei, depois da edição)
                          e tribunais.yml (aba Tribunais, depois da edição)
```

`index.html`, `edicoes/` e `data/` são escritos pelo robô — não edite à mão.

---

## Testes

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Os testes não acessam a internet (usam arquivos de exemplo em `tests/fixtures/`).
A cada envio de código, o GitHub roda os testes e gera uma edição de exemplo
(workflow *CI*).
