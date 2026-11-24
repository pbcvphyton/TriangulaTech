# Decisões finais (valem sobre spec.json quando houver conflito)

Rascunho temporário de trabalho (nova home e Agenda da semana). Será removido
antes da entrega final. Os caminhos de rascunho citados abaixo são da sessão
de trabalho; as cópias que importam estão nesta pasta.

Contexto: a home tem blocos demais e repete conteúdo; o objetivo é (1) uma home
que faça sentido e (2) uma agenda da semana com o que está pautado no STF, no
STJ e no Congresso, junto com a mídia que o jornal já acompanha. Um painel
(design, político, jurídico, dados) produziu `spec.json`; este arquivo é o
desempate, já com as objeções da revisão incorporadas.

Arquivos de apoio (todos em
`/tmp/claude-0/-home-user-TriangulaTech/853edc40-6c97-5a3a-9141-a5a7681881a4/scratchpad/`):

- `painel/spec.json` — especificação do painel (home, agenda, etapas, metas).
- `painel/criticas.json` — objeções; as "bloqueia" e "importante" foram aceitas
  conforme descrito aqui.
- `painel/mapa-home.json`, `painel/mapa-congresso.json`, `painel/mapa-tribunais.json`
  — como o código funciona hoje, com arquivo:linha.
- `trabalho/congresso/` — respostas reais das APIs da Câmara e do Senado
  (semana de 23 a 27/11/2026); `trabalho/eng-cetico/dl/` — mais respostas reais.
- `previa.py` — `python previa.py 2026-11-23 <pasta>` renderiza a home e a cópia
  arquivada com o código atual, sem escrever no repositório.
- `medir.js` — `node medir.js <arquivo.html> <pasta> [prefixo]` fotografa em
  1440x900 e 390x844 e grava as medidas (altura, palavras, links, blocos).
- `shots/` e `medidas-antes/` — a home de hoje (antes).

## Regras de trabalho (todas as etapas)

- Português em nomes, comentários e textos, como no resto do projeto. Texto de
  tela em português claro, sem jargão e sem travessão. Nenhum identificador de
  modelo de IA em código, comentário ou texto.
- Nada pode derrubar a edição diária: toda leitura de arquivo novo é tolerante
  (ausente, ilegível, formato inesperado, versão desconhecida = fonte ausente).
- `index.html`, `edicoes/` e `data/` do repositório são escritos pelo robô: NÃO
  edite nem regrave esses arquivos à mão. Prévia só na pasta de rascunho.
- Um escritor por pasta: a edição diária nunca escreve em `data/tribunais/` nem
  em `edicoes/tribunais.html`; o fluxo Tribunais nunca escreve fora deles.
- Testes sem rede, com fixtures em `tests/fixtures/`. Rode `python -m pytest -q`
  nos arquivos da sua área enquanto trabalha e a suíte inteira no fim.
- Outros agentes editam a MESMA árvore ao mesmo tempo, em outros arquivos. Só
  toque nos arquivos da sua lista. Nunca use `git stash`, `git checkout --`,
  `git reset`, `git clean` nem `git commit`. Se um teste fora da sua área falhar
  por causa de trabalho alheio em andamento, anote e siga; não "conserte" o
  arquivo de outro agente.
- Não crie documentos de planejamento. README só quando a etapa mandar.

## Home (etapa 1) — o corte

Ordem final da home (uma cópia só no código, que se rearruma pela largura):

1. Cabeçalho. No celular (até 640 px): logo e UMA fileira de botões; todo botão
   com nome legível ou rótulo acessível; o lema sai do cabeçalho no celular (vai
   para o rodapé). Teto: 120 px no celular. No computador fica como está.
2. Linha de indicadores: cotações PARADAS (sem rolagem automática, sem botão de
   pausa); arrasta-se para o lado para ver as demais; a página nunca rola na
   horizontal. No computador, o clima da primeira cidade entra como último item,
   sem emoji; no celular o clima não aparece. A faixa própria do clima acaba.
   Sem chave nova de configuração: a ordem das cotações é a de hoje.
3. Manchete: editoria, título (abre a janela de leitura `#s-id`), linha fina,
   foto quando houver (ao lado no computador; no celular, sem foto ou miniatura
   de até 96 px) e uma linha de serviço: "N veículos · há X" e, quando o assunto
   tem os dois lados, "veículos divergem" (link para o item em "Onde os veículos
   divergiram"). Nenhum parágrafo do corpo. Nunca cortar título: controle de
   tamanho por classe de letra conforme o comprimento, calculada no servidor.
4. "Em 1 minuto": lista numerada única com 5 chamadas clicáveis: as 3
   secundárias e os 2 primeiros destaques; se faltar, completa com as próximas
   matérias na ordem das seções. Título e, só no computador, a linha fina em 1
   linha. Cada matéria aparece UMA vez neste andar (manchete + 5). Um link no
   pé: "Todas as N matérias" (abre o bloco 7).
5. (Reservado) "Agenda da semana": entra na etapa 5, entre o bloco 4 e o 6.
6. "Como a mídia cobriu", duas colunas (uma sob a outra no celular):
   - Linha de abertura do bloco, em português, sem "T" e sem número, só para a
     marca com termômetro e quando há termômetro no dia: "Viés político das
     notícias de hoje: equilibrado. Ver a análise." (o rótulo vem do termômetro;
     "Ver a análise" leva a `#termometro` da cópia arquivada). Só quando algum
     texto sai da faixa de equilíbrio a linha cresce e lista até 3 textos.
   - (a) "Onde os veículos divergiram": até 3 assuntos com os dois lados e 3
     veículos ou mais. Cada um: o título e os dois lados EM PALAVRAS com a
     conta: "3 veículos: critica a falta de ambição. 1 veículo: destaca os
     limites do consenso. 11 sem lado." Só se chama divergência quando o lado
     menor tem 2 veículos ou mais; com 1, o texto é "1 veículo destoou". Sem
     nenhum assunto assim: uma frase ("Hoje os veículos relataram os N assuntos
     comparados de forma semelhante."). Link: "Ver os N assuntos comparados"
     (`#cobertura` da cópia arquivada).
   - (b) "Mais recentes até HHhMM" (hora da edição): 5 itens no computador, 3 no
     celular, com hora, veículo e título. Um único link para a lista completa
     do dia ("Todas as N notícias do dia").
   - Estados: edição em modo automático (sem cobertura comparada e sem
     termômetro) mostra só (b); marca sem termômetro não mostra a linha do
     viés. Testar com `data/2026-11-16.json` (automática) e com a marca pbcv.
7. "Todas as N matérias, por editoria": ÚNICO bloco recolhido (`<details>` com
   `id="editorias"`), uma linha quando fechado. Aberto: as abas de hoje começando
   em "Todas", a busca do dia dentro, cartões só com editoria, título e linha
   fina (marca de cobertura só quando houve dois lados). Sem a segunda cópia do
   Radar. O atalho "/" abre o bloco antes de focar a busca.
8. Rodapé: uma linha de método ("Compilada de N notícias de M veículos. Como foi
   feita" → `#racional` da cópia arquivada), edições anteriores, Instagram,
   código-fonte, estado das fontes recolhido, e o lema.
9. Só na cópia arquivada (`edicoes/AAAA-MM-DD.html`), depois do bloco 7: três
   gavetas fechadas (`<details>`), com o conteúdo de hoje por dentro, sem
   mudança: `id="cobertura"`, `id="termometro"`, `id="racional"`. Uma função só
   abre toda gaveta que contém o alvo da âncora (no carregamento e em
   `hashchange`) e refaz a medição das abas no evento de abrir. A home
   (`index.html`) NÃO carrega as gavetas: os links apontam para a cópia
   arquivada do dia. Âncoras `#s-id`, `#cobertura`, `#termometro`, `#racional`
   da cópia arquivada continuam valendo (e-mails enviados, Analítico, Pesquisa).
10. Janelas de leitura das matérias: sem mudança.

Saem da home: editorial do dia; corpo da manchete; "Em 1 minuto" em prosa;
"Mais para ler"; "Destaques"; chamada relacionada; listas de veículos, barras e
o selo "Sem divergência entre os N veículos" nas chamadas; navegação de
editorias do topo; faixa do clima; rolagem automática das cotações; barra de
progresso de leitura e botão "voltar ao topo" (só na página da edição; essas
peças vivem no `layout.html.j2`, que é compartilhado: use bloco condicional e
não quebre as outras páginas); faixa "Compilada de..."; Radar (duas cópias);
Racional; cobertura completa; painéis do termômetro. O JSON da edição NÃO muda
(editorial, briefing, highlights continuam gravados; e-mail e Instagram iguais).

Metas (edição real de 23/11/2026, medidas com `medir.js`):

- Computador 1440x900: até 2.250 px (2,5 telas) JÁ COM a agenda; sem a agenda
  (etapa 1), até 1.700 px. Manchete e as 5 chamadas inteiras na primeira tela.
- Celular 390x844: até 3.400 px (4 telas) já com a agenda; sem a agenda, até
  2.700 px. O título da manchete começa a no máximo 240 px do topo e a página
  não rola na horizontal.
- Palavras visíveis: até 1.000. Controles visíveis: até 12. `index.html` real:
  até 450 KB. A página da edição de exemplo dos testes segue abaixo de 250 KB.
- Zero movimento automático. Zero termo de bastidor ("blocos por editoria",
  "parte 1 de 2", "chamadas à IA") na home.
- Poucos tamanhos de letra nos blocos novos (meta: até 6 tamanhos e 2 pesos na
  home), laranja só para destaque pontual, um tipo de fio.

## Agenda da semana — decisões

Arquitetura:

- Contrato único (pacote novo `qijournal/agenda/`, módulo `contrato.py`): o
  formato de `data/congresso/agenda.json` (escrito pela edição diária) e de
  `data/tribunais/agenda.json` (escrito pelo fluxo Tribunais) é o MESMO, com
  campo `versao`. Leitor tolerante: versão desconhecida ou formato inesperado
  vale como fonte ausente. Os dois coletores validam a saída com o contrato.
- Retrato do dia gravado na edição (`Edition.agenda`, opcional, só gravado
  quando existe, no molde de `Edition.congresso`). A edição só lê arquivos.
- Liga/desliga por marca no molde de `tribunais` (`agenda: {enabled, marcas:
  [tech]}` em `config/site.yaml`, `QIJ_NO_AGENDA`).
- Página completa `edicoes/agenda.html` (sem botão novo no cabeçalho), escrita
  só pela edição diária; depois que existe é sempre regravada (com o estado
  "sem pauta válida desde DD/MM" quando for o caso). Renderização dentro de
  `try/except` próprio em `publish()`: se falhar, não grava e registra aviso; a
  edição sai igual. O retrato é convertido em objetos validados dentro de
  `build_view` sob `try/except` que devolve vazio; o modelo só lê esses objetos
  (o ambiente Jinja usa `StrictUndefined`).

Janela e ordem:

- Por dia, nunca por instituição. Janela: o dia da edição e os 4 dias úteis
  seguintes (edição de sábado ou domingo começa na segunda). Os dias já
  passados da semana corrente vão para "Já decidido esta semana" (até 2
  resultados na home; todos na página completa). Coleta: da segunda-feira da
  semana corrente até 13 dias depois.
- "HOJE" e "AMANHÃ" são marcados no navegador do leitor (data de Brasília);
  sem script, aparecem só as datas. Dias passados ficam em cinza.
- Dentro do dia: hora oficial (linha sem hora depois) e, no empate, a nota.

Seleção (régua pública em `config/agenda.yaml`, sem IA, igual para todos):

- Só entra o que tem data e decide: sessão deliberativa de Plenário (Câmara,
  Senado, Congresso), julgamento (Plenário presencial do STF; Corte Especial e
  Seções do STJ), comissão que decide (CCJC e CFT na Câmara; CCJ e CAE no
  Senado; CMO) e prazo constitucional que vence. Fora: solenes, audiências,
  requerimentos de rotina, redações finais, outorgas, recursos internos em
  lista do STJ, segredo de justiça.
- Nota de 0 a 85, soma de três partes: FORO (até 30), ALCANCE (até 30),
  ESTÁGIO (até 25), com os valores do campo `regra_de_selecao` de `spec.json`.
  A MÍDIA NÃO PONTUA (a mídia não escolhe o que entra). Corte: 60 na home, 40
  na página completa. A nota de relevância do Congresso só desempata.
- Piso por instituição: STF, STJ, Câmara, Senado e Congresso sempre aparecem na
  home com o melhor item da janela pelo nome, em uma linha, mesmo abaixo de 60.
  Sem nada com data na janela, uma frase traz o próximo evento com o assunto
  ("STJ: nada de alcance geral nesta semana; em 2/12, Corte Especial, <título
  do tema>"). Contagens ("2.219 itens", "112 vetos") não aparecem na home.
- Tetos da home: até 8 linhas na janela; no celular, HOJE e AMANHÃ abrem com
  até 4 linhas no total, cada uma com 1 item nomeado (o resto vira "mais N") e
  no máximo 3 linhas de texto; os outros dias em linhas de uma linha. No
  computador, duas colunas: "hoje e amanhã" (larga) e "resto da semana"
  (estreita); nada de grade de cinco colunas. Altura: até 520 px no computador
  e 600 px no celular.

Nome do item (sem IA; o assunto vem ANTES do número):

1. Nome curto oficial quando a fonte publica (campo de apelido do Senado; nome
   da medida provisória).
2. Tabela opcional de nomes aprovados por pessoa em `config/agenda.yaml`.
3. Título do tema (repercussão geral ou repetitivo), cortado no fim de palavra.
4. Ementa do tipo "Altera/Modifica/Acrescenta ... , para X": mostra a partir de
   "X". Ementa que começa por "Institui", "Cria", "Dispõe sobre": corte normal.
   Limpar espaços especiais. Até 90 caracteres no computador e 60 no celular.
5. Sem texto aproveitável: o item NÃO é nomeado na home (entra em "mais N").
   Depois do nome vem o identificador como link oficial ("· PL 6461/2019").
   Apelido aprovado aparece entre parênteses: "PLP 299/2026 (pacote fiscal)".

- Uma palavra de área no começo do item quando a fonte traz classificação
  oficial (tema da proposição, ramo do direito). Sem classificação, sem palavra.
- Dicionário fixo de situações (escrito por gente, em `config/agenda.yaml`),
  usado na home; o termo oficial fica na página completa. Exemplos: "retorno
  de vista" → "volta ao julgamento depois do pedido de vista de <ministro>";
  "aprovado, ressalvados os destaques" → "texto principal aprovado; faltam os
  trechos votados em separado"; "referendo de cautelar" → "o Plenário confirma
  ou derruba a decisão provisória"; prazo de medida provisória → "Medida
  provisória perde a validade"; dia sem nada → "nada de peso pautado".
- Verbo sempre "pautado" / "consta da pauta"; nunca "vai votar", "será
  julgado", previsão, chance, adjetivo ("polêmico", "pauta-bomba").
- Nenhum texto escrito por IA. Pessoa física e advogado nunca; processo em
  segredo só em contagem; empresa só se estiver no catálogo de litigantes.

Resultado (dias passados):

- Câmara: só a PALAVRA do resultado, e só quando o texto traz, sem ambiguidade,
  "Aprovado/Rejeitado o Projeto (a Proposta, o Substitutivo, a Medida
  Provisória...)" do próprio item. Nenhum placar nesta entrega. Sessão que não
  está encerrada aparece como "em andamento", sem resultado. Guardar a resposta
  real de 23/11 (`/eventos/83419/pauta`) como fixture e travar em teste que
  "127 a 347" (requerimento de adiamento) nunca sai como resultado.
- Senado: resultado oficial de `plenario/resultado`, só a palavra.
- Tribunais: não há dado de resultado; a linha passada diz "resultado no
  andamento oficial" com o link. O `agenda.json` dos tribunais ACUMULA a semana
  corrente (sessão passada é mantida do arquivo anterior, marcada "como pautado
  em DD/MM"); a poda é na virada da semana.

Fontes e frescor:

- Câmara: `/api/v2/eventos` + `/eventos/{id}/pauta`. O Plenário só registra a
  sessão deliberativa na véspera ou no dia: a agenda diz "pauta do Plenário
  ainda não publicada pela Câmara" e mostra a FILA da última sessão, rotulada
  como fila, nomeando os 2 itens de maior nota; nunca "não haverá sessão".
- Senado: `plenario/agenda/mes` (uma chamada por mês da janela), comissões que
  decidem; Congresso: `plenario/agenda/cn`. Prazos de medida provisória SÓ se a
  consulta exata for confirmada ao vivo e guardada como fixture; nada de
  calcular prazo à mão. MPVs anteriores a 2002 ficam fora.
- Rede do coletor do Congresso: prazo global dentro do comando (150 s), ordem
  de prioridade (Plenários, Senado, prazos, CCJC e CFT, o resto), grava o
  parcial com a situação de cada fonte; fetch próprio com 15 a 20 s e uma nova
  tentativa, injetado no Coletor, SEM tocar `congresso/fontes.py`. Fonte fora
  do ar mantém as sessões daquela Casa do arquivo anterior, com a data delas;
  nunca grava zero. O comando sai 0 com qualquer falha de fonte.
- STF: só o Plenário presencial (a fonte só dá contagem do virtual e das
  Turmas; isso é dito na página completa). STJ: seleção sobre o arquivo
  completo de pautas, antes do corte de 20 por ministro; a fonte não informa
  hora nem assunto, e a agenda não afirma nenhum dos dois.
- Frescor dos tribunais: usar `data_dados`, `ok` e `velho_dias` de
  `data/tribunais/meta.json`; aviso só a partir de dois dias úteis; acima do
  limite os itens da fonte somem e fica uma frase com o link oficial.
- Pé do bloco na home, em uma linha: "Conferido em DD/MM. A pauta pode mudar.
  Agenda completa." (data por fonte só quando alguma está velha) e os links
  "Raio-X do Congresso" e "Raio-X dos Tribunais" (as abas).

Mídia (ligação, sem IA):

- Liga só por identificador (proposição com sigla, número e ano; processo por
  classe e número; "Tema N" com o tribunal no mesmo texto) ou por apelido
  aprovado em `config/agenda.yaml` (2 palavras ou mais, com validade de/até;
  NUNCA em `config/congresso.yaml`). Sigla solta, nome de ministro, nome de
  parte e palavras da ementa não ligam. Feed do próprio órgão não conta como
  imprensa.
- Item ligado ganha "na mídia: N veículos" com link para a matéria da edição
  (abre a janela) ou para a Pesquisa. Item sem ligação fica calado. A home NÃO
  publica frase de ausência ("X pautas sem cobertura"). Na página completa a
  redação é "menção direta encontrada em N veículos".
- A chamada da matéria ligada ganha, na linha de serviço, "na agenda: qua 25".

Fora desta entrega (registrar no README como pendência): placar, atualização da
agenda ao longo do dia, agenda no e-mail, sugestão automática de apelidos,
linha da agenda nas abas Congresso e Tribunais, revisão da editoria Jurídico.
