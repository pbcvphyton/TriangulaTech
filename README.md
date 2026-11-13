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
mais veículos, com o rótulo "Pende para: …". Ele aparece no card de cada
matéria, na janela da matéria (com a conclusão e a **análise por veículo**) e na
seção **Cobertura comparada**. No e-mail, cada matéria ganha a barra e a
conclusão. A análise usa só o que os veículos publicaram; quando o trecho não
permite dizer, o veículo fica como neutro.

A seção **Cobertura comparada** reúne todos os assuntos do dia vistos por dois ou
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

Os atalhos do topo da edição levam à Cobertura comparada, ao Termômetro do dia, ao
Racional da edição e a todas as notícias do dia.

### Termômetro de Viés Editorial e Analítico Político (só Triangula)

Depois da edição, os textos compilados nas matérias do dia passam pelo
**Termômetro de Viés Editorial, versão 1.1** — a especificação em
`termometro-referencia-v1.3`, que é o pilar desta parte. Ele é **estritamente
adicional**: a análise da cobertura acima continua igual, e o termômetro aparece
**depois** dela, no fim da edição, e na aba **Analítico Político** (botão
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

No fim da edição, o **Termômetro do dia** é um painel com:

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

### Racional da edição e lista completa do dia

- **Racional da edição:** uma faixa no topo da capa e uma seção própria dizem
  como a edição foi compilada — quantas notícias e veículos, os blocos por
  editoria (notícias lidas, matérias redigidas e assuntos comparados em cada
  um), quantos assuntos sobraram depois de unir as repetidas e qual IA fez o
  trabalho. Cada matéria mostra de quantas notícias e veículos foi compilada e
  em que bloco.
- **Nenhuma notícia é descartada:** a página *Todas as notícias do dia*
  (`edicoes/AAAA-MM-DD-todas.html`, com link na capa e no e-mail) lista todas as
  notícias coletadas, por editoria e por assunto (as repetidas ficam juntas),
  com link para o original. Só ficam de fora da coleta publieditoriais,
  resultados de loteria, horóscopo e vídeos sem texto (`exclude_url_patterns` e
  `exclude_title_patterns` em `config/site.yaml`).

---

## Analítico do Congresso (aba Congresso, só Triangula)

O botão **Congresso** do cabeçalho abre `edicoes/congresso.html`: o que cada
deputado e senador **vota, propõe e deixa parado**, lido dos registros oficiais
das duas Casas. **Nenhuma IA entra nas contas**: é contagem sobre voto nominal,
proposição e relatoria, com as réguas à vista em
[`config/congresso.yaml`](config/congresso.yaml). Cobre todos os que estão em
exercício (513 deputados e 81 senadores) e quem passou pela legislatura, com o
histórico de votos desde 2019.

A página segue o padrão do Analítico (a leitura → as lentes → Como ler):

- **A leitura**: a manchete é o achado de maior peso, com o número e o link para
  onde ele está na página; abaixo, os demais achados, numerados. Uma caixa abre o
  dossiê de qualquer nome.
- **1 · Com quem vota** ("Por baixo dos rótulos, de que lado cada um fica"):
  quantos votam com cada polo, a matriz **partido × tema** (o mesmo partido vota
  com a direita num assunto e com a esquerda em outro) e as bancadas (saldo,
  governismo e coesão).
- **2 · A fila** ("O que espera, com quem, e o que passou na frente"): quantas
  matérias tramitam e em que porta estão (à espera de relator, de parecer, de
  pauta…), as comissões e os relatores com mais matérias na mão, os projetos
  prontos para o Plenário que não entram na pauta e **o que passou na frente** (do
  protocolo ao voto em menos de 120 dias).
- **3 · O Plenário** ("O que foi votado, e quem saiu da linha"): as últimas
  votações nominais, com o placar, a orientação do governo, como cada partido votou
  e quem votou contra o próprio partido.
- **4 · Quem é quem** ("O que cada um vota, propõe e deixa parado"): as listas
  (quem menos vota, quem mais vota contra o próprio partido, quem vota longe da
  própria bancada, quem mudou em relação ao governo, quem tem mais propostas
  paradas, os mais citados no jornal) e a tabela de **todos os parlamentares**, com
  filtro e ordenação. Um botão troca a Casa (Câmara ou Senado).
- **O dossiê**: clicar num nome abre a ficha (endereço próprio:
  `congresso.html#p=cd-204536`): com quem vota, quanto segue o governo da vez (e o
  anterior), presença, fidelidade ao partido, o saldo **por tema**, o governismo
  **semestre a semestre**, **quando saiu da linha** (dissidências e surpresas), os
  últimos votos, **o que propõe** (por tema e por situação, com as paradas), **o
  que tem na mão** (relatorias sem parecer) e **no jornal** (as manchetes que o
  citam).

**Nas matérias da edição**: quando o texto de uma matéria cita um deputado ou
senador em exercício, ela ganha o bloco **No Congresso**, com a ficha curta de cada
um (até `fichas_por_materia`, 4): o saldo, o governismo, a presença, as propostas
paradas, as relatorias sem parecer e o link para o dossiê. O nome só casa inteiro; nomes de uma palavra só (ou
feitos de prenomes comuns) precisam do cargo ("deputado", "senadora") ou da legenda
("(PL-SP)") ao lado, para não confundir com homônimos.

### As contas

| Conta | Como é feita |
|---|---|
| Só voto nominal | só as votações de Plenário que registram o voto de cada um; a simbólica (a maioria) não diz quem votou o quê. As secretas do Senado (autoridades) contam para a presença, não para a posição |
| Tipo da votação | pela descrição oficial: texto principal, destaque/emenda, urgência, requerimento de procedimento, recurso, autoridade |
| Com quem vota (saldo) | só votações **de conteúdo** (texto principal e destaques) **divisivas**: a maioria do polo de esquerda votou de um jeito e a do polo de direita, de outro, com 75% de cada polo do mesmo lado. Vai de −100 (sempre com a esquerda) a +100 (sempre com a direita); aparece com 10 votações ou mais. É comportamento, não rótulo |
| Governismo | onde a liderança do Governo orientou Sim ou Não, a parte em que votou igual; contado à parte em cada governo (quem acompanha qualquer governo aparece assim). Obstrução conta como Não |
| Presença | votações em que podia votar e votou. Câmara: só os dias em exercício (histórico do mandato); os dados abertos não dizem se a ausência foi justificada. Senado: a ausência justificada (licença, missão, atividade parlamentar) fica fora da conta e aparece à parte. Quem preside a Casa só vota em caso de empate: fica fora da lista |
| Fidelidade e dissidência | votos iguais aos da maioria do partido do dia; dissidência é votar diferente quando 80% ou mais do partido votou junto |
| Surpresa | quem tem lado definido (saldo além de ±60) e, numa votação divisiva, ficou com o outro polo |
| Mudança | o último semestre contra os anteriores do mesmo governo: só com 15 pontos ou mais e 99% de confiança (duas proporções) |
| Temas | a área temática oficial da matéria votada (Câmara) ou a classe do processo (Senado), levadas a 13 macrotemas |
| A fila | a situação oficial de cada proposição vira um estado (tramitando, apensada, aprovada, virou norma, encerrada) e uma porta (relator, parecer, pauta, Mesa, comissão…); "parada" é sem andamento há mais de 365 dias |
| Passou na frente | texto principal aprovado em votação nominal nos últimos 240 dias, menos de 120 dias depois de apresentado |
| No jornal | os nomes procurados nas manchetes e no texto das matérias de `data/busca/` (as edições dos últimos 60 dias; fora esporte, natureza e variedades) |

Os polos (hoje PT, PSOL e PCdoB × PL e NOVO), os governos, os limiares, as faixas
do campo e os macrotemas são **escolhas editoriais**: ficam em
`config/congresso.yaml` e vão escritos no **Como ler** da aba. Mudar um valor muda a
conta na atualização seguinte.

**Limites**: mede voto, não discurso (o cruzamento "disse × votou" ainda é manual:
a ficha põe o voto ao lado da manchete); só o Plenário (as comissões ficam de fora
das contas de voto); a autoria conta o primeiro signatário; no Senado, a fila só
tem o que tramita; nomes muito comuns podem deixar de casar nas matérias (o
contrário, casar errado, é o que a regra evita).

### Dados e atualização

- **Fontes** (públicas, sem chave): arquivos em lote e API da Câmara
  (`dadosabertos.camara.leg.br`) e API legislativa do Senado
  (`legis.senado.leg.br/dadosabertos`).
- **Todo dia**, antes da edição, o passo *Atualizar o Congresso* do `daily.yml`
  roda `python -m qijournal congresso atualizar`: baixa o que mudou, refaz as contas
  e grava só os arquivos que mudaram (cerca de 2 minutos; os anos fechados da base
  de votos nunca são refeitos). O que foi baixado fica num cache do Actions, fora do
  repositório. O passo é acessório (`continue-on-error`, 15 minutos): se uma fonte
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
| `casa-cd.json`, `casa-sf.json` | os painéis de cada Casa (bancadas, temas, fila, listas, últimas votações) |
| `achados.json`, `meta.json` | a leitura do dia e as réguas usadas |

- **Desligar**: `congresso.enabled: false` em `config/site.yaml`, ou a variável
  **`QIJ_NO_CONGRESSO`** = `1` (o botão e os blocos somem; os dados ficam).
- **Código**: `qijournal/congresso/` (`fontes.py` coleta e cache; `base.py` votações;
  `regras.py` as réguas; `perfil.py` as contas de voto; `exercicio.py` períodos de
  mandato; `projetos.py` proposições, relatorias e fila; `jornal.py` os nomes no
  banco do jornal; `ficha.py` o dossiê; `dados.py` a atualização; `cli.py` os
  comandos) e `qijournal/render/congresso.py` com os modelos `congresso.*.j2`.

---

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
Montevidéu saiu em nov/2026: a faixa de clima mostra só as cidades configuradas,
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
(`colors.masthead`, com data e hora à esquerda do logo no computador), barra de
editorias e primeira página no desenho de jornal no computador (editorial,
manchete com a foto ao lado, "Mais para ler" e trilho com o Radar); no celular
e no tablet valem os blocos de sempre. A paleta vem do quadro do Sinal: marinho `#1B2745` (ticker, rodapé, títulos), ardósia `#394A7A` (links e
réguas), laranja de sinal `#FF5A1F` (destaques; só decoração no fundo branco) e
altas/quedas do ticker em `#7FC8FF`/`#FF5C9A`, com os cinzas azulados do layout
original.

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
                          jornal.py (os nomes no banco do jornal), ficha.py (o
                          dossiê), dados.py (a atualização) e cli.py (comandos)
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
assets/                   logos e ícones das marcas; fonts/ (fontes das imagens do
                          Instagram); instagram/ (foto de perfil e capas de destaque)
docs/instagram.md         guia do Instagram
index.html                edição do dia (gerado automaticamente)
edicoes/                  arquivo de edições, todas as notícias de cada dia,
                          e-mail e latest.json (gerado)
data/                     cada edição em JSON; em data/termometro/, as análises
                          do termômetro de cada dia, guardadas para sempre; em
                          data/busca/, o que cada edição publicou, para a Pesquisa
                          analítica; em data/congresso/, a base de votos, as
                          fichas e os painéis do Congresso (gerado)
tests/                    testes automáticos
.github/workflows/        daily.yml (edição diária), congresso.yml (Congresso, à
                          mão), instagram.yml (posts do Instagram) e ci.yml (testes)
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
