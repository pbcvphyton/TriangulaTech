# Instagram da Triangula: especificação da Onda 1 (nível BJ/BD)

Versão condensada da especificação do diretor de conteúdo (26/10/2026), feita a
partir do painel de benchmark, dados e editoria sobre as edições reais de 29/09
a 25/10. É a referência para o código de `qijournal/instagram/` e para quem
revisar os posts. Todo número citado veio dos JSONs de `data/`.

## 0. Decisões

1. **Posicionamento.** A Triangula deixa de ser um "resumo bem diagramado" e
   passa a ser **a máquina que mede a imprensa**: a capa traz uma tese que é um
   contraste medido entre os veículos, nunca uma opinião da casa; o gráfico faz
   o papel da foto.
2. **Grade.** 3 posts de feed por dia, sempre na mesma ordem de cor:
   07:00 Em 1 minuto (marinho), 12:00 Número do dia (laranja chapado),
   18:30 Raio-X (papel). Cada dia ocupa uma linha do grid.
3. **Onda 1 (sem nenhuma chamada nova de IA):** filtro eleitoral por conteúdo,
   frases falsas removidas, assinatura em toda arte, numeração de série,
   cotação com hora, sistema visual novo, Raio-X 2.0 com Placar do dia, Número
   do dia, Em 1 minuto 2.0, stories sem clima e manifesto no lugar do "Comece aqui".
4. **Onda 2:** Placar da Imprensa (domingo), A Semana em Dados (sábado),
   fixados 2 e 3, insights, IA redatora com trava (7 dias em sombra).
5. **Onda 3:** Reels do Relógio da notícia, Mapa da imprensa mensal (só com
   amostra suficiente), continuidade entre dias.
6. **Não publicamos:** ranking ou concordância entre veículos, o Termômetro de
   Viés, contagem de menções a candidatos, manchete literal de veículo, foto.

## 1. Princípios

**Técnica.** Cadência de máquina (uma linha do grid por dia; se uma peça falha,
sai um substituto). O carrossel é um argumento: a capa promete e o slide 2
entrega um dado novo; **o slide 2 nunca repete a capa** (o Instagram
reapresenta o carrossel a partir dele). Franquias numeradas e seladas
("RAIO-X Nº 014"). Um CTA por formato, de envio ou salvamento. A legenda
estende a arte: 1ª linha com o contraste e o número (até 110 caracteres),
3 a 5 linhas "▸", o método em uma linha e 1 CTA.

**Arte.** Gancho em grotesca condensada (Archivo ExtraCondensed Black, caixa
alta, entrelinha apertada) com **uma** expressão em laranja, sempre substantivo
ou número. Uma cor domina a grade: todo dia há um quadrado inteiro laranja (a
capa do Número). O gráfico é a foto. Pouca moldura. Assinatura "▲ @handle" (ou
"▲ triangula" sem handle) em toda arte.

**Pensamento.** Tese com tensão, mas medida (contagem contra contagem, tempo
contra tempo, expectativa contra dado). Autoridade por métricas próprias com
nome: Placar do dia, Relógio da notícia, Número triangulado, Pauta solitária.
Âncora de tempo só contra a nossa série ("desde 29/09"), nunca "recorde". O
enquadramento dos outros (`outlets[].framing`) é a matéria-prima. Rigor
visível: número sempre com base ("4 de 25"), percentual só com base ≥ 20,
universo nomeado, verbos de observação. Ausência medida também é notícia
("0 de 14 assuntos dividiram a cobertura hoje").

**Rejeitado:** provocação opinativa, foto/rosto/meme, "comente PALAVRA",
carrossel acima de 10 slides, ranking de veículos, manchete literal de veículo,
citar BJ ou BD como modelo em público.

## 2. Grade e regras

| Horário (BRT) | Formato | Capa | Substituto |
|---|---|---|---|
| 07:00 (sáb–dom 08:30) | Em 1 minuto 2.0, 8 slides | marinho | Terminal da manhã (dias sem IA) |
| 07:10 (sáb–dom 08:40) | Stories da manhã, 4 telas | — | — |
| 12:00 | Número do dia, 3 slides | laranja chapado | a cadeia do Número nunca fica vazia |
| 18:30 | Raio-X 2.0, 8 slides | papel | Placar do dia (4 slides); se a base < 10, sem IA ou falha: Mercado em linha |
| uma vez (D0) | Manifesto, 6 slides | laranja | — |

Prazos: manhã até 11:00, Número até 16:00, noite até 22:30. `FEED_GAP` de 3 h
continua. Hashtags: 3 a 4 por post (#triangula + formato + 1–2 de editoria;
formato: #noticias, #dados, #jornalismo).

Ordem de seleção do dia (`gerar.build_pieces`), com a `Fila`:
1. **Noite:** `raio_x()`, ou `placar_do_dia()`, ou `mercado_em_linha()`
   (a mais escassa vem primeiro).
2. **Número:** `numero_do_dia(exclude={lead, story do Raio-X}, usados=chaves
   publicadas nos últimos 7 dias)`.
3. **Manhã:** `em_1_minuto(raiox_story_id=…)` ou `terminal()`.
4. **Stories:** `stories_manha(agenda=…)` com o que de fato foi gerado.

Os três posts do dia nunca têm o mesmo assunto como peça principal (para a capa do
Em 1 minuto, os destaques do Placar contam como peça principal da noite, e o Raio-X
ou os destaques do Placar da noite anterior também contam, salvo número novo: §5.4). A ordem na
fila fica: manhã, stories, número, noite. Quando a noite cai para o Mercado em
linha, ele é montado depois do Número e não repete o ativo dele no título; o
Terminal também evita os ativos dos outros dois. O título do Terminal só vai para uma variação
fora do comum (pelo menos 1% e 2 vezes a variação típica do ativo, leitura do dia); sem ela, sai
neutro ("13 INDICADORES ANTES DA ABERTURA", ou "EM DIA SEM PREGÃO"), e a linha do achado vai
marcada na tabela. CTA do Terminal: "Salve para comparar com a próxima leitura.".

**Período eleitoral** (`election_until`, hoje 2026-10-27): política e assunto
eleitoral fora do Raio-X, do Placar, do Número e da capa (também a eleição de
outro país no título, `estatistica.eleicao_escrita`: "Israel vai às urnas", 27/10;
no Placar ela sai da própria base, `estatistica.eleicao_na_cobertura`, para capa,
waffle, "por editoria" e lista contarem a mesma coisa; "reeleição na Fifa" fica). O
Número de mercado, o título do Terminal e o do Mercado em linha pulam o ativo que
estiver no título de matéria eleitoral do dia (`estatistica.mercado_eleitoral`:
"Ibovespa desaba 13,88%… após eleição", 27/10);
no Em 1 minuto, no máximo um item de política (sempre o último). Duas regras são **permanentes**:
o destaque em laranja e o gancho nunca levam nome de partido ou de político, e
nenhum veículo é rotulado com campo político.

## 3. Pré-requisitos (já na base)

- **Filtro eleitoral por conteúdo** (`estatistica.is_electoral`): TSE, TRE,
  Justiça Eleitoral, 1º/2º turno, urna, propaganda/horário eleitoral,
  candidato/candidatura, campanha eleitoral, chapa, intenção de voto, cassação,
  institutos de pesquisa (fora de Mundo); nomes de `instagram.eleicao.nomes`
  em qualquer seção; partido só com artigo/preposição ("do PL") e só em
  brasil/politica/juridico/mercados. Não usa "voto" sozinho nem "eleição" em Mundo.
- **Frases falsas fora:** "mais de 1.600 notícias todo dia", "mais de 100
  fontes", "mais de 40 veículos", "um robô coleta", "sem opinião". No lugar, o
  número vivo do dia; nos fixados, número arredondado para baixo e com data.
  "Fontes" quando a contagem inclui órgãos públicos; "veículos" só sem eles.
- **Cotações:** "fech. DD/MM" só quando é fechamento de pregão; senão "leitura
  das HHhMM"; sem variação quando a leitura é velha (`estatistica.leitura_velha`:
  a mesma cotação da edição anterior, ou ativo sem pregão lido em outro dia). Série
  com uma leitura por dia; acumulado sempre ponta a ponta, com as duas datas (e as
  comparações na mesma janela: "No mesmo período, de 28/09 a 20/10: Brent +11,7%
  (desde 29/09)"); fechamento e leitura do meio do pregão nunca se comparam sem o
  selo de cada um; pregão faltante tracejado; juro real ex-post = (1 + Selic) /
  (1 + IPCA 12m) − 1. Horários de dias diferentes levam a data nas duas pontas
  ("28/10 10h51 a 29/10 04h47").
- **Numeração de série:** `Fila.series` guarda o último número **publicado**
  de cada série; o post usa `series[serie] + 1`, mantém o número ao ser refeito
  e reaproveita o de um post expirado. O Placar do dia pertence à série do
  Raio-X. O Mercado em linha não tem número.
- **Lint** (`redacao.lint`): palavras proibidas (sensacionalismo: urgente,
  bomba, chocante, despenca, dispara, derrete, desaba, explode, polêmica,
  escândalo…; vícios de IA: cenário, "em meio a", desdobramentos, "vale
  destacar", "sem precedentes", "acende alerta"…), léxico eleitoral no modo
  eleitoral, limites de caracteres e overflow do render. Peça reprovada cai para
  o substituto e o motivo vai para o log.

## 4. Regras comuns

- Número sempre com base (`fracao()`); o placar diário vai sempre como "X de Y",
  sem percentual.
- **Ressalva preservada:** com "cerca de | mais de | até | quase |
  aproximadamente | em torno de", o qualificador aparece junto do número, em
  caixa menor acima dele ("CERCA DE / R$ 1 BI").
- **Adjetivo da casa** (gigante, histórico, inédito, bilionário, polêmico,
  chocante, explosivo, dramático, colossal, recorde): num texto de capa só fica
  se aparecer no título de pelo menos metade dos veículos do assunto; senão a
  manchete é cortada no último " e " ou "," antes dele (sobrando 5+ palavras),
  ou usa-se o dek. "Histórico de/do/da" depois de artigo, possessivo ou "e" é
  substantivo ("luto e histórico de segurança na malha"). Se tirar a palavra
  deixar emenda sem sentido ("luto e de segurança"), o texto é omitido.
- **Verbos para veículo:** destacou, enfatizou, priorizou, focou, registrou,
  citou, publicou às. Proibidos: omitiu, ignorou, escondeu, distorceu, errou,
  enviesou, furou, atrasou, admitiu.
- **Horário** sempre em BRT, "horário informado pelo veículo"; nunca "primeiro
  a noticiar" nem "furo".
- **Laranja:** um destaque por slide, número ou substantivo; nunca adjetivo,
  veículo, partido ou político. No waffle, o laranja fica no quadrado do racha
  e no gancho; as contagens da legenda vão em tinta.
- **Concordância pelo numerador:** "1 de 24 assuntos dividiu", "0 de 14
  assuntos dividiram".
- **Gancho em caixa alta:** o número fica preso à palavra seguinte e "só"/"de"
  ao número (espaço inseparável: nunca "MESMO FATO, 4 / VEÍCULOS"); hora no
  gancho sai com "h" minúsculo ("LEITURA DAS 06h52"); duração vai por extenso.
- **Legenda:** linha 1 (≤ 110 caracteres, única no dia, com o número e o
  contraste); linha em branco; 3–5 linhas "▸" (as opcionais saem de trás para a
  frente, nunca abaixo de 3); uma linha de método; 1 CTA (o mesmo do último
  slide); 3–4 hashtags; até 1.300 caracteres; sem emoji em tragédia.
- **Alt text:** todo slide, com o texto integral; quando há gráfico, a frase
  gerada pela mesma função que desenha o gráfico.

## 5. Formatos

### 5.1 Raio-X 2.0 (18:30, 8 slides, papel) — `formatos/noite.py`

Elegível: story com `coverage`, 4+ veículos, a ≥ 2 e b ≥ 2, 4+ framings, lados
escritos, fora de política/eleição no modo eleitoral. Nota =
2×importance + 4×min(a,b) + 0,5×(a+b) + 2×peso_seção − 8×repetição (peso: 3
brasil/mercados, 2 tecnologia/imobiliário, 1 jurídico/natureza, 0
mundo/política, −2 esporte/variedades; repetição = 2+ entidades em comum com o
Raio-X dos 3 dias anteriores).

Gancho por molde (≤ 90 caracteres, só o rótulo da minoria em laranja): G2
minoria ("11 VEÍCULOS, MESMO FATO. 6 DESTACARAM SINERGIA EM IA. SÓ 2,
COMPLEXIDADE DE GOVERNANÇA."), G3 empate ("metade" só quando todos tomaram
lado), G0 padrão, compacto (o do G2 guarda o "só": "6 DE 11 DESTACARAM SINERGIA
EM IA. SÓ 2, COMPLEXIDADE DE GOVERNANÇA."), sem rótulo. Maioria e minoria pela
contagem; nunca "Lado A" como título. Quando o assunto teve veículos fora da
análise, a contagem vira "11 VEÍCULOS ANALISADOS" (o relógio do mesmo carrossel
conta os 12 que publicaram); passando de 90, cai no compacto.

Slides: 1 capa-tensão com barra A | N | B e o subtítulo que diz qual é o fato,
com sujeito e o quê (o assunto ou a manchete só sem o adjetivo da casa; a linha
fina só se nomear o fato; senão a frase do corpo que o nomeia, cortada na
vírgula; nunca título literal de veículo: 29/10, "Pelo menos 68 pessoas
morreram… colisão entre dois trens…"); 2 o fato (nunca repete a capa nem o
gancho); 3 a maioria; 4 a minoria + sem lado (no empate, os chapéus são
"Metade"/"A outra metade" só sem neutros; com neutros, "Empate · um lado"/
"Empate · o outro lado"); 5 Relógio da notícia (válido com 4+ veículos com
horário e span ≥ 30 min; senão "A atenção"); título com a duração por extenso
("15 VEÍCULOS EM MAIS DE 18 HORAS", "QUASE 23 HORAS"; o exato fica sobre o eixo);
a data vai no eixo, uma vez (na 1ª marca e em cada virada), as linhas só com
HHhMM e o veículo, e uma legenda sob o eixo nomeia cada cor (● maioria ● sem
lado ● minoria ○ fora da análise); nenhum veículo escondido em "+k" com 12 ou
menos (as linhas de contexto saem antes); 6 o que isso significa (conclusão +
espectro, sem percentual): a conclusão da IA só com as frases que a contagem
sustenta (sai a que atribui um lado a um grupo de veículos que não medimos,
"veículos internacionais", "agências", "jornais generalistas"; a de proporção
quando a maioria não passa da metade; a de número que não é uma das contagens);
sem nenhuma, o molde com os números do espectro, a janela de horário de cada
lado quando elas não se cruzam e a conta por idioma ("Entre os veículos em
inglês, 2 destacaram X e 3, Y; em português, 3 e 2."); a lista "fora da
análise" com nomes inteiros e "+k"; 7 por que importa (some sem
`why_it_matters`); 8 Placar do dia + método + CTA "Leu só uma versão? Mande
para quem leu a outra." no gancho condensado (como o fecho do Número), sem seta.

Legenda: "▸ O fato" com sujeito e o quê (o subtítulo da capa ou o slide 2, o
que nomear o fato); os sem lado vão na linha da minoria; relógio com "(11
analisados)" quando houve veículo fora da análise; até 5 linhas "▸".

### 5.2 Placar do dia (variante da noite, 4 slides)

Quando nenhuma story é elegível e a base do placar ≥ 10: capa "X DE Y ASSUNTOS
DIVIDIRAM A COBERTURA HOJE" ("1 DE 17 ASSUNTOS DIVIDIU…"; selo "DIA DE
CONSENSO" com 0), o maior consenso (ou os rachas: no empate a barra diz "de um
lado"/"do outro", nunca "maioria"; rótulo hiperbólico, "riscos astronômicos",
não vira rótulo), o mais coberto com o relógio (no período eleitoral, quando o
primeiro do ranking é eleição de outro país, o seguinte; se for o mesmo assunto
do slide 2, "O mesmo assunto foi o mais coberto do dia"), método + "Salve: o
placar sai todo dia." no gancho condensado. "Por editoria": assunto comparado
sem matéria cuja metade dos veículos é esportiva no dia conta em Esporte (a Bola
de Ouro veio como mercados em 27/10). Legenda com 3 a 5 linhas "▸" (dividiram ou
maior consenso, o mais coberto, por editoria, o maior consenso, a pauta
solitária).

### 5.3 Número do dia (12:00, 3 slides, laranja) — `formatos/numero.py`

Escolha (`estatistica.numero_do_dia`), primeira regra com candidato:
1 mercado fora do comum (z ≥ 3 sobre a escala robusta do ativo); 2 número
triangulado (o mesmo número, com unidade, no título de 3+ veículos; fora
esporte, política/eleição no modo eleitoral, `exclude` e `usados`); 3 o assunto
mais coberto com 12+ veículos; 4 mercado com z ≥ 2; 5 acumulado da série;
6 juro real ex-post; 7 o maior acumulado, mesmo repetido.

Nos passos 1 e 4 a leitura velha não vale, e o fechamento de outro dia vai no
pretérito, com a data ("Ibovespa caiu 13,88% em 26/10").

Slides: capa laranja chapada (texto marinho #0F1730), qualificador, o número em
Archivo condensada 300–360 px, tese ≤ 14 palavras e a prova em mono (quando a
manchete cai só pelo adjetivo da casa e a linha fina não diz de quem é o número,
o sujeito vai na frente: "Embraer: volume total de encomendas avança 14% em doze
meses"); slide 2 com o gráfico do tipo (checklist, matriz de pontos, barras de
variação, com as leituras do meio do pregão vazadas, linha da série ou barras do
juro real) e nunca a tese da capa; slide 3 método + "Mande para quem acha que leu
tudo.". A fonte vai uma vez por slide: na assinatura (slides 1 e 2) ou na ficha
(slide 3); o pé do slide 2 leva a base e o método.

### 5.4 Em 1 minuto 2.0 (07:00, 8 slides, marinho) — `formatos/manha.py`

**Seleção dos 5 fatos:** preferência por editoria brasil > mercados >
tecnologia > imobiliário > mundo > jurídico > natureza > política (pós-eleição)
> esporte > variedades (esporte e variedades só se faltar fato). Fora:
`is_electoral` no modo eleitoral, e também a eleição de outro país com todas as
letras (`estatistica.eleicao_escrita`, fora o esporte: "Israel vai às urnas",
27/10); mais de 1 item de política no modo eleitoral (e esse vai por último);
itens que repetem a variação de Ibovespa, dólar, Brent ou bitcoin já mostrada
no slide de mercado; itens com 2+ entidades em comum com a capa ou os fatos da
véspera **e** sem número novo; duplicatas (o mesmo assunto em dois itens do
briefing). O 1º fato é a story da capa. Texto do fato: o item do briefing, a
manchete ou a linha fina, nunca o título literal de um veículo
(`estatistica.titulo_literal`, que vale também para a capa: "Colisão entre trens
de alta velocidade mata ao menos 68 pessoas na Espanha" é o da Folha, 29/10);
quando a linha fina não nomeia o fato, a frase do corpo que o nomeia, cortada na
vírgula ("Pelo menos 68 pessoas morreram… após a colisão entre dois trens…"). Quando a
linha fina entra no lugar da manchete (adjetivo da casa sem corte possível ou título
literal) e não diz de quem é o fato, o sujeito vai na frente, como na tese do Número
(`manha.texto_capa`: "Binance: corretora informou ter coberto 90% dos valores levados",
01/11, e não "Corretora informou…"); a tela 2 dos stories usa o mesmo texto.

**Capa e a noite (§2):** a capa não estampa o que a noite do mesmo dia estampa
(a matéria do Raio-X, ou as do Placar: em 19/10 os protestos na França, maior
consenso e mais coberto do Placar, saem da capa) nem reabre o assunto da noite
anterior (a matéria do Raio-X ou as que o Placar destaca; `Fila.materias_raiox`),
o post logo antes dela na grade: mesmo assunto (2+ entidades em comum ou
`manha.mesmo_assunto` com a manchete) e nenhum número novo (em 29/10, "Flávio
Bolsonaro desembarca nos EUA" depois do Raio-X de 28/10 sobre a fuga). Com número
novo, a continuação pode ser capa, e o número em destaque é o novo (01/11: a Binance
volta depois do Placar de 31/10, que destacou o roubo de US$ 6,3 bi; a capa estampa
"90%", o que a corretora cobriu). O assunto ainda pode ser fato e aparecer na linha
do mais coberto.

| # | Slide | Conteúdo |
|---|---|---|
| 1 | Capa | Selo "EM 1 MINUTO Nº 031 · QUA 21 OUT"; número em destaque em laranja (`manha.numero_da_capa`: o da manchete que sai na capa ou, sem ele, o da manchete original e da linha fina, com o qualificador); sem número e com 5+ veículos, a cobertura da própria capa ("NOTICIADO POR / 9 VEÍCULOS", 22/10); a manchete sem adjetivo da casa (Noto Serif 800, 64–80 px, até 90 caracteres); **sem dek** (vai para o slide 2); faixa "1.640 notícias · 46 fontes · 1.159 assuntos"; "O MAIS COBERTO · {k} VEÍCULOS / {topo}" só com k ≥ 5, com a contagem em tinta (o laranja é só do dado da capa); quando o mais coberto é a própria capa, só "É O ASSUNTO MAIS COBERTO DO DIA", e a contagem fica para o slide 2 |
| 2–6 | Fatos | número em destaque (180–220 px; sem número, a frase sobe para 60 px); frase em Noto Serif 52 px; `selo_cobertura` com mini barra A/N/B; chip vazado da editoria; fontes (as da matéria e, se forem menos que o selo conta, as do assunto no index). **No slide 2**, o texto nunca repete a capa (`manha.repete_capa`: Jaccard ≥ 0,6 com a manchete ou 60% das palavras de assunto dela, pelas 5 primeiras letras, dentro do texto; 31/10, "COP17 de Biodiversidade é encerrada na Armênia com críticas às metas globais…"; 02/11, "O Ministério da Fazenda descartou fixar teto…" sob "Fazenda descarta teto…"): vai o próximo (o item do briefing, a manchete, a linha fina e, por último, uma das 2 primeiras frases do corpo, para a capa não cair por falta de texto); nenhum número que a capa mostra volta como destaque (o em laranja e os da manchete), nem a cobertura quando ela é o número da capa; quando o relógio é válido, linha extra com as 3 primeiras batidas ("Folha 15h57 · g1 16h00 · NeoFeed 16h35 +5"). Fato sem casamento com story sai sem selo |
| 7 | Mercado em linha | `mercado.mercado_slide(tema="dark")` |
| 8 | Fechamento | uma estatística (pauta solitária: "1.039 de 1.159 assuntos de hoje (90%) saíram em uma fonte só") + matriz de pontos pequena + 1 CTA "Mande no grupo de quem precisa começar o dia informado." |

**Decisão de arte (dono, 11/2026):** os fatos usam tamanhos maiores que os da
tabela, para encher o slide (fato sem número com 5+ veículos usa a cobertura como
número). Tamanhos adotados (`manha.tamanhos_fato` e `tamanhos_capa`, o maior par que
cabe): número do fato de 260 a 180 px e frase de 68 a 44 px (sem número, a frase
sozinha de 96 a 60 px, em até 6 linhas); na capa, número de 280 a 160 px e manchete
de 80 a 64 px (96 a 72 sem número).

Selo de cobertura (`estatistica.selo_cobertura`): "{k} veículos" (do index, sem
órgãos; quando a cobertura comparada tem órgão público, "{k} fontes", §3) + a
divisão + "· Raio-X às 18h30" quando a story é a da noite. **"Dividida" tem uma
definição só no perfil, a do Placar** (`estatistica.dividida`: 4+ comparados e 2+
de cada lado): "· dividida 6 × 2"; abaixo do critério, com alguém dos dois lados,
só a contagem ("· 2 × 1", "· 4 × 1"); com um lado só, "· sem divisão"; com uma
fonte comparada, nada. A mini barra conta o mesmo e nunca soma mais que o selo.

Legenda: "{capa} · e mais 4 fatos da {dia}" (≤ 110 caracteres), "▸ {fato}
({selo})", mercado em uma linha com o acumulado, "Resumo por IA a partir de {N}
notícias de {M} fontes.", CTA, hashtags.

### 5.5 Stories da manhã (07:10, 4 telas) — `formatos/stories.py`

Bom dia + "Hoje lemos N notícias de M fontes" + agenda do que foi gerado (sem
clima); manchete (a da capa do Em 1 minuto, com o mesmo texto, `manha.texto_capa`, e
o verbo neutro: "caem até 10%"; com o filtro eleitoral, também a eleição de outro país,
e nunca o título literal de um veículo; a 2ª frase nunca repete o título; selo, mini
barra e fontes como no Em 1 minuto, a lista fechando com a contagem); mercado com a coluna do
acumulado e a hora da leitura; chamada para o carrossel com a capa do Em 1 minuto
já renderizada de miniatura (sem ela, o desenho genérico). Assinatura em todas.

### 5.6 Manifesto (Comece aqui 2.0) — `formatos/manifesto.py`

Capa laranja "MESMO FATO, VÁRIAS VERSÕES."; o funil da edição com a data;
pauta solitária e divisão no histórico com IA desde 14/10, com período e n (a
divisão com a base e o critério do Placar: de 14/10 a 26/10, 330 de 391 assuntos
com 4 ou mais veículos comparados sem divisão; "Dividir = pelo menos 2 veículos de
cada lado, o mesmo critério do Placar do dia"); "como fazemos e o que não fazemos"
("Sem adjetivo nosso, sempre com fonte.": o adjetivo do veículo pode aparecer
citado, o da casa não); "Todo dia, 3 posts" + Siga. "x em cada 10" arredondado
para baixo.

### 5.7 Mercado em linha — `formatos/mercado.py`

Título-achado com o maior |acumulado| e, embaixo, a ponta final ("até o
fechamento de 23/10", "até a leitura das 06h45"); 4 gráficos pequenos 2×2 (Ibovespa,
Dólar, Brent, Bitcoin) com a série desde 29/09, ponto de hoje em laranja,
extremo anotado e pregão faltante tracejado; faixa Selic · IPCA 12m · juro
real; rodapé com o método (a fonte fica na assinatura). Como substituto da noite:
tema papel, sem número de série, e o título só vai para ativo lido no dia (às
18h30, o fechamento de sexta não é título da segunda-feira).

## 6. Sistema visual

- **Fontes:** Archivo (ganchos e números, `font-stretch: 62%; font-weight:
  900`), Noto Serif (manchetes e texto), IBM Plex Mono (dados, horários,
  selos). A Noto Sans sai dos templates do Instagram.
- **Cor:** marinho #1B2745/#0F1730, laranja #FF5A1F, papel #F6F4EF; lados A
  ardósia, B petróleo, neutro cinza. Tema `.t-orange` (texto marinho). O laranja
  nos demais formatos só marca o dado.
- **Moldura:** sai a régua dupla, o logotipo nos slides internos, o "01 FATO"
  gigante e os tracinhos de progresso. Fica uma faixa de série de 8 px com o selo
  em pílula e o contador ("03/08"), e um rodapé de uma linha com a assinatura.
  Nada essencial a menos de 72 px das laterais.
- **Gráficos** (`graficos.py`, funções puras que devolvem `(svg, descricao)`):
  barra A/N/B, relógio, espectro, waffle, matriz de pontos, linha, barras de
  variação, checklist. Nenhum texto sai do quadro do SVG (o guarda do render não
  mede texto de SVG; os testes medem); nome de veículo não é cortado com
  reticências: lista longa vira "nomes inteiros +k".

## 7. Perfil

- Bio: "MESMO FATO, VÁRIAS VERSÕES. ▲ ~1.500 notícias por dia (média), de
  dezenas de veículos: o fato, o mercado e como cada um contou. Por IA, com
  fontes ↓".
- Destaques: Comece aqui, Raio-X, Método. Fixados: Manifesto (D0), Como ler
  um Raio-X e Nossos números (Onda 2).

## 8. Limites honestos

- Lado A/B/neutro é classificação da IA da edição, numa passada, sem
  confiabilidade medida: todo método diz isso, e não há ranking de veículos.
- O agrupamento de assuntos é automático e tende a superestimar exclusivos;
  só vale a partir de 14/10.
- Domingo tem cerca de 45% menos notícias; comparação entre dias só com o mesmo
  dia da semana.
- O Termômetro de Viés fica fora do Instagram (91% "Equilibrado", 61% das
  análises de um só veículo).
