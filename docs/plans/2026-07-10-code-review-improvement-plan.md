# Plano de correções e melhorias após code review

**Projeto:** IRAI — Intraday Risk Appetite Index  
**Data da revisão:** 2026-07-10  
**Status:** Proposto  
**Escopo:** backend, banco SQLite, motor V1/V2, API, frontend React, sincronização Firebase, coletor e testes

## 1. Objetivo

Corrigir os problemas funcionais encontrados na revisão, proteger a causalidade dos sinais, tornar o
contrato entre frontend e API explícito, garantir que uma instalação nova seja inicializável e reduzir
riscos operacionais causados por concorrência, cache e persistência incorreta do estado Kalman.

O plano prioriza primeiro tudo que pode impedir a inicialização ou alterar o sinal mostrado ao operador.
Melhorias de desempenho, experiência de desenvolvimento e limpeza estrutural ficam para etapas
posteriores, depois que os invariantes quantitativos estiverem cobertos por testes permanentes.

## 2. Regras para execução

- Para cada bug, criar primeiro uma regressão permanente que descreva o comportamento correto e falhe
  antes da correção.
- Não usar dados futuros em cálculos apresentados como causais.
- Não validar mudanças de timezone apenas pelos valores brutos do banco; verificar os eixos EEST e BRT.
- Não executar coleta live no Linux como prova de funcionamento, pois o runtime MT5 é Windows-only.
- Preservar as mudanças locais já existentes no worktree. No início da implementação, revisar novamente
  os diffs de `backend/api/main.py`, `backend/irai/engine.py`, `backend/irai/zscore.py` e os testes novos.
- Os números de linha citados nesta análise refletem o estado observado durante o review e podem mudar.
  As funções e comportamentos descritos são as referências autoritativas.

## 3. Resumo executivo dos achados

| Prioridade | Tema | Risco principal |
|---|---|---|
| P0 | Schema incompatível com o engine | Banco novo carrega zero modelos silenciosamente |
| P0 | `version=both` sem implementação | UI rotulada como Kalman recebe o caminho estático |
| P0 | Cursor de pré-mercado | Ghost bars B3 podem exibir retorno falso |
| P1 | Persistência Kalman histórica | Replay de sessão antiga pode sobrescrever o estado live |
| P1 | NWE não causal no overview | Card pode usar a barra seguinte ao calcular a inclinação anterior |
| P1 | Trabalho síncrono no event loop | Overview V2 pode bloquear a API após cada invalidação de cache |
| P2 | Contrato Firebase incompleto | NWE e eixo BRT divergem entre localhost e hospedagem |
| P2 | Acurácia ausente no detalhe | Convicção usa 80% mesmo quando o modelo possui outra acurácia |
| P2 | Corrida de requests no React | Resposta antiga pode substituir o ativo/data atual |
| P3 | Fluxo `collector --once` | Não notifica a API e não fecha explicitamente a conexão |
| P3 | Reprodutibilidade e cobertura | Dependências ausentes impedem lint, build e integração do engine |

## 4. Plano de implementação

### Fase 0 — Baseline e proteção do trabalho existente

#### Ações

1. Registrar `git status --short` e revisar os diffs locais antes de editar qualquer arquivo.
2. Executar o teste Python leve existente.
3. Preparar as dependências de desenvolvimento do frontend sem alterar versões incidentalmente.
4. Confirmar qual Python/ambiente Windows é usado em produção e documentar como instalar `pykalman` e
   as demais dependências em um ambiente de desenvolvimento sem MT5.
5. Criar uma forma de testar o engine com banco temporário e sem acesso a terminais MT5.

#### Validação inicial

```bash
python3 tests/test_zscore.py
python3 -m compileall -q backend scripts tests
cd frontend && npm run lint
cd frontend && npm run build
```

#### Observação do review

- `tests/test_zscore.py` passou com 5/5 testes.
- `compileall` passou; houve apenas um `SyntaxWarning` em script arquivado.
- `npm run lint` e `npm run build` não rodaram porque `eslint` e `vite` não estavam instalados.
- O import do engine falhou no ambiente Linux porque `pykalman` não estava instalado.

---

### Fase 1 — Tornar o schema inicializável e falhar de forma explícita

#### Problema

`backend/db.py` cria `asset_models` sem a coluna `divergence_config`. Já
`IRAIEngine._load_params()` seleciona essa coluna. A exceção SQL é capturada genericamente e convertida
em uma lista vazia, fazendo o processo iniciar com zero modelos.

O problema foi reproduzido com um banco temporário criado por `init_db()`: a coluna não estava presente.
O script `scripts/calc_sigmas.py` também assume que ela existe ao executar o `UPDATE`.

#### Impacto

- Uma instalação limpa não é funcional mesmo depois de executar `python backend/db.py`.
- O erro real fica oculto atrás de “0 models loaded”.
- Scripts de calibração/manutenção podem falhar dependendo do histórico de migrações manuais do banco.

#### Teste de regressão antes da correção

Criar `tests/test_db_schema.py` usando um banco temporário:

1. Executar `init_db(temp_path)`.
2. Consultar `PRAGMA table_info(asset_models)`.
3. Verificar a presença de `divergence_config` com default JSON válido ou `NULL` aceito pelo loader.
4. Inserir um modelo mínimo e instanciar o loader do engine.
5. Verificar que o modelo é registrado e que uma falha real de schema não é silenciosamente ignorada.

#### Correção proposta

- Adicionar `divergence_config` ao `SCHEMA` de `asset_models`.
- Criar uma migração idempotente, por exemplo `migrate_asset_models()`, que confira as colunas via
  `PRAGMA table_info` antes do `ALTER TABLE`.
- Executar a migração no fluxo principal de `backend/db.py`.
- Restringir o `except` em `_load_params()`. Erro “tabela não existe” ou “coluna não existe” deve abortar
  a inicialização com mensagem acionável; fallback só deve existir para compatibilidade intencional.
- Validar e tratar JSON inválido em `factors`, `factor_labels` e `divergence_config` com contexto do target.

#### Critérios de aceite

- Banco novo contém todas as colunas usadas pelo código atual.
- Rodar migração duas vezes não falha nem altera dados.
- Engine não inicia silenciosamente com zero modelos por incompatibilidade de schema.
- `scripts/calc_sigmas.py` funciona sobre um banco inicializado pelo fluxo oficial.

---

### Fase 2 — Definir o contrato V1/V2 e remover o falso `both`

#### Problema

O frontend solicita `version=both`, mas `IRAIEngine.compute_from_db()` reconhece apenas a comparação
exata com `"v2"`; qualquer outro valor segue o caminho estático. A API retorna somente campos genéricos
como `p_up`, enquanto componentes do overview procuram `p_up_v1` e `p_up_v2`.

Com isso, a tela local marcada como “DINÂMICO (KALMAN)” pode mostrar V1. No Firebase o sincronizador pede
V2, porém o overview ainda interpreta o campo genérico como fallback de V1.

#### Decisão necessária

Escolher formalmente uma das opções:

**Opção recomendada — somente V2 na UI atual**

- Frontend pede `version=v2`.
- Remover `rastroView` e campos V1/V2 não utilizados.
- Manter V1 acessível apenas por query explícita para comparação/debug.
- Menor custo de CPU e contrato mais simples.

**Opção alternativa — comparação V1 e V2**

- Implementar `both` como contrato oficial.
- Resposta deve conter séries e resumos separados, sem sobrecarregar `p_up` ambiguamente.
- Definir se fatores, divergência, veredito e pesos pertencem a V1, V2 ou ambos.
- Evitar executar o mesmo carregamento de barras duas vezes; compartilhar o dataset da sessão.

#### Testes de regressão antes da correção

- Teste de API que rejeita `version=both`/`invalid` com HTTP 422 se a opção recomendada for adotada.
- Ou teste que confirma `p_up_v1` e `p_up_v2` quando `both` for oficialmente implementado.
- Teste garantindo que `version=v2` realmente instancia/atualiza o Kalman e que `version=v1` não o faz.
- Teste de contrato do frontend com fixture de resposta da API.

#### Correção proposta

- Tipar `version` com `Literal["v1", "v2"]` ou enum no FastAPI.
- Alinhar `/overview`, `/series`, WebSocket e `firebase_sync.py` ao mesmo contrato.
- Remover o default WebSocket `version="both"` ou implementar o contrato correspondente.
- Não aceitar versões desconhecidas como fallback silencioso para V1.
- Remover o estado React `rastroView` se a comparação tiver sido abandonada.

#### Critérios de aceite

- Todo texto “Dinâmico/Kalman” recebe comprovadamente dados V2.
- Nenhuma chamada do frontend ou sincronizador usa versão não suportada.
- Swagger/OpenAPI documenta os valores válidos.
- O overview e o detalhe exibem a mesma versão e o mesmo último `p_up` para o mesmo target/data.

---

### Fase 3 — Corrigir ghost bars e pré-mercado B3

#### Problema

No modo normal do engine, `target_cursor` começa em zero, mas `is_pre_market` é calculado com
`target_cursor < 0`. Assim, o estado de pré-mercado nunca é verdadeiro e o guard que deveria forçar
`win_return = 0.0` não é executado.

Antes da primeira barra real do target, o engine cria uma linha sintética com o fechamento anterior,
mas chama `compute()` usando a abertura da sessão atual. Isso pode produzir retorno falso e reintroduzir
o bug de ghost bars documentado em `.planning/docs/TIMEZONE_ARCHITECTURE.md`.

#### Teste de regressão antes da correção

Criar uma fixture SQLite mínima com:

- Fatores Tickmill desde `00:00` EEST.
- Último fechamento B3 da sessão anterior.
- Primeira barra B3 às `09:00` BRT, alinhada para `15:00` EEST.
- Algumas barras B3 posteriores à abertura.

Verificar que:

1. Barras anteriores a `15:00` EEST são `is_ghost=True`.
2. Barras anteriores a `15:00` EEST têm `win_return == 0.0`.
3. A primeira barra real usa abertura e fechamento corretos.
4. O timestamp da primeira barra real aparece como `15:00` no eixo EEST e `09:00` no eixo BRT.
5. Ativos globais não recebem o deslocamento de seis horas.

#### Correção proposta

- Inicializar o cursor em `-1`.
- Avançar o cursor enquanto a próxima barra real tiver timestamp menor ou igual ao timestamp corrente.
- Separar explicitamente `has_target_bar`, `is_pre_market` e `is_ghost_bar`.
- Usar o fechamento anterior como `win_open` e `win_current` durante o pré-mercado, ou sobrescrever o
  retorno de forma garantida antes de qualquer cálculo de divergência.
- Não atualizar cumulative delta com ghost bars.
- Conferir a virada de data EEST no final da sessão B3.

#### Critérios de aceite

- Linha do preço fica exatamente em 0% antes da abertura B3.
- Nenhuma ghost bar é confundida com barra real.
- Eixos EEST/BRT permanecem separados por seis horas.
- Teste equivalente para ativo global continua passando.

---

### Fase 4 — Proteger a persistência do estado Kalman

#### Problema

Toda chamada V2 salva o último estado no banco, inclusive replay de sessão histórica. Uma consulta antiga
pode sobrescrever o estado live e virar o prior da próxima computação. Há ainda uma definição morta de
`compute_from_db()` no início da classe, com referências indefinidas e uma chamada incompatível de
`save_kalman_state()`, indicando uma proteção incompleta que nunca é executada.

#### Invariante desejado

Um estado Kalman persistido só pode avançar no tempo. Replays históricos devem ser puros e não alterar o
estado operacional, salvo quando uma rotina explícita de reconstrução for solicitada.

#### Testes de regressão antes da correção

1. Salvar estado com timestamp de hoje.
2. Calcular uma sessão anterior em V2.
3. Confirmar que média, covariância e timestamp persistidos não mudaram.
4. Calcular uma barra mais nova em modo live.
5. Confirmar que o timestamp e o estado avançaram.
6. Simular duas gravações concorrentes fora de ordem e verificar que a mais antiga não vence.

#### Correção proposta

- Remover a primeira definição morta de `compute_from_db()`.
- Adicionar ao engine um modo explícito, por exemplo `persist_state=False` por default para histórico e
  `True` somente no fluxo live controlado.
- Alternativamente, persistir com SQL condicional:

```sql
INSERT INTO kalman_state (...)
VALUES (...)
ON CONFLICT(slug) DO UPDATE SET ...
WHERE excluded.timestamp_utc > kalman_state.timestamp_utc;
```

- Fazer comparação de timestamps normalizados e timezone-aware.
- Não carregar estado cuja dimensão seja incompatível com o número atual de fatores.
- Validar shape e finitude de média/covariância antes de chamar `set_state()`.
- Considerar separar estado por versão do modelo/calibração para evitar carregar betas de uma cesta antiga.

#### Critérios de aceite

- Replay histórico não modifica estado live.
- Gravações fora de ordem são ignoradas atomicamente pelo banco.
- Mudança na quantidade de fatores não quebra o engine nem reaproveita estado incompatível.
- Reinício do serviço mantém continuidade causal entre sessões.

---

### Fase 5 — Unificar o NWE causal

#### Problema

O NWE do frontend usa somente lookback. No overview do backend, `get_center(i_idx)` percorre todas as
barras. Ao calcular o centro da penúltima barra, ele inclui a última barra, introduzindo lookahead de uma
barra no `nwe_slope` usado pelos cards.

#### Teste de regressão antes da correção

- Construir uma série em que a última barra tenha um salto grande.
- Calcular o centro da penúltima barra antes e depois de anexar o salto.
- O centro causal da penúltima barra deve permanecer idêntico.
- Comparar backend e frontend sobre a mesma fixture, com tolerância numérica definida.

#### Correção proposta

- Limitar o kernel do backend a índices `j <= i_idx` e ao mesmo lookback de 95 barras.
- Centralizar constantes `bandwidth=8`, `multiplier=3` e `lookback=95` em configuração documentada.
- Preferencialmente calcular o NWE no backend e enviar os valores para todos os consumidores, evitando
  duas implementações com semânticas divergentes.
- Se o cálculo continuar duplicado, criar fixtures compartilhadas para validar equivalência.

#### Critérios de aceite

- Acrescentar uma nova barra não repinta valores anteriores.
- Overview, detalhe local e Firebase concordam sobre direção e bandas da última barra.
- Cálculo permanece causal durante toda a sessão.

---

### Fase 6 — Evitar bloqueio e recomputação duplicada na API

#### Problema

Endpoints `async` executam SQLite, pandas, Kalman e Johansen de forma síncrona. O overview percorre todos
os targets, e o Johansen pode ser recalculado repetidamente para cada barra. Após `notify_update`, o cache
inteiro é limpo e a primeira requisição refaz o trabalho no event loop.

#### Riscos

- Health checks e requisições simples ficam presos atrás do overview.
- Polls simultâneos podem computar a mesma chave mais de uma vez.
- Corrigir `version=both` de forma ingênua pode dobrar ainda mais o custo.
- WebSocket, caso usado, chama os próprios handlers e disputa o mesmo loop.

#### Medição antes da mudança

Instrumentar por target e endpoint:

- Duração de `compute_from_db()`.
- Duração total do overview.
- Quantidade de chamadas Johansen por request.
- Cache hit/miss.
- Número de computações simultâneas para a mesma chave.

#### Correção em etapas

1. Adicionar lock/single-flight por `(target, date, version)`.
2. Mover trabalho síncrono para threadpool como contenção imediata.
3. Evitar chamar handlers FastAPI diretamente no loop WebSocket; extrair serviços puros.
4. Calcular somente a extensão desde a última barra, em vez de reprocessar toda a sessão.
5. Como arquitetura preferida, calcular no coletor/worker após inserir uma barra e deixar a API apenas
   servir snapshots já materializados.
6. Invalidar apenas chaves afetadas, e não todo o cache indiscriminadamente.

#### Critérios de aceite

- `/api/health` responde durante uma recomputação de overview.
- Duas chamadas concorrentes da mesma chave produzem uma única computação.
- Tempo de resposta e cache hits ficam observáveis em logs.
- Resultado numérico antes/depois permanece idêntico nas fixtures.

---

### Fase 7 — Completar o contrato Firebase

#### Problema

`/api/irai/series` retorna `history_closes`, `is_b3`, nome, ícone e resumo. `firebase_sync.py` conserva
apenas os arrays `series` e `summary`. O frontend Firebase procura `data.history[safeTarget]`, campo que o
sincronizador nunca cria, e não recebe `is_b3` para reconstruir o eixo BRT.

#### Impacto

- O NWE hospedado começa sem histórico anterior e pode produzir bandas diferentes do localhost.
- Ativos B3 perdem o eixo secundário BRT na hospedagem.
- Metadados de target são reconstruídos parcialmente de fontes diferentes.

#### Teste de regressão antes da correção

- Testar `sync()` com respostas HTTP mockadas.
- Confirmar que o payload Firebase conserva `history_closes`, `is_b3`, `display_name`, `icon`, `accuracy`
  e o identificador da versão.
- Alimentar o frontend com fixture Firebase e fixture API local equivalentes e comparar a transformação.

#### Correção proposta

- Armazenar a resposta de série por target como objeto completo, em vez de separar parcialmente campos.
- Alternativamente, criar mapas explícitos `history`, `series_meta` e `summaries`, usados de forma
  consistente pelo React.
- Incluir `schema_version` no payload Firebase para permitir migrações futuras.
- Evitar baixar todo o banco Firebase quando somente overview ou um target forem necessários; considerar
  paths separados se o tamanho do payload continuar crescendo.

#### Critérios de aceite

- Mesmo target/data produz NWE e eixos equivalentes localmente e no Firebase.
- Payload possui versão de schema.
- Frontend trata payload antigo de forma controlada durante a transição.

---

### Fase 8 — Corrigir acurácia e corridas de requisição no frontend

#### 8.1 Acurácia incorreta no detalhe

O endpoint inclui `summary.accuracy`, mas `seriesInfo` não recebe o valor. `SignalGauge` usa 80% como
fallback, então a convicção do detalhe pode discordar do overview.

**Correção:** propagar a acurácia real para `seriesInfo` em ambos os modos, local e Firebase. Usar fallback
somente quando o backend realmente não fornecer calibração.

**Teste:** fixture com acurácia diferente de 80% e asserção do valor de convicção calculado.

#### 8.2 Respostas fora de ordem

`fetchSeries()` não cancela a requisição anterior nem verifica se a resposta ainda corresponde ao target,
data e modo atuais. Trocas rápidas por swipe ou polling sobreposto podem permitir que uma resposta velha
substitua dados novos.

**Correção:** usar `AbortController` no cleanup do effect ou um request sequence ID guardado em `useRef`.
Trocar `setInterval` por um loop que agende o próximo poll somente após o anterior terminar.

**Teste:** atrasar artificialmente a resposta do target A, selecionar B e confirmar que A não sobrescreve B.

#### 8.3 Tratamento HTTP

O frontend chama `response.json()` sem verificar `response.ok`. Padronizar um helper de fetch que:

- valide status HTTP;
- diferencie erro 404 sem dados de backend offline;
- aplique timeout/abort;
- não atualize estado após desmontagem;
- preserve dados atuais durante falha de refresh silencioso.

#### Critérios de aceite

- Convicção usa a acurácia calibrada do target.
- Trocar rapidamente de target nunca exibe série de outro ativo.
- Não existem polls sobrepostos.
- Erros HTTP aparecem de forma coerente, sem apagar dados válidos em refresh transitório.

---

### Fase 9 — Robustez do coletor

#### Problema

No modo `--once`, o loop executa `break` antes de chamar `/api/internal/notify_update` e antes do fechamento
explícito da conexão. Erros inesperados durante a coleta de um símbolo também podem encerrar o ciclo sem
garantir cleanup do SQLite e do MT5.

#### Teste de regressão antes da correção

- Mockar MT5, conexão SQLite e notificação HTTP.
- Rodar um ciclo `--once` e confirmar commit, close, `mt5.shutdown()` e notificação quando houver mudança.
- Simular falha em um símbolo e confirmar que os demais continuam sendo processados.

#### Correção proposta

- Colocar conexão e shutdown em `try/finally`.
- Notificar a API após qualquer ciclo que tenha inserido/atualizado barras, inclusive `--once`.
- Não notificar quando nada mudou, evitando invalidações desnecessárias.
- Capturar erros por terminal e por símbolo com logs contextualizados.
- Considerar timeout/configuração da URL de notificação por variável de ambiente.

#### Critérios de aceite

- Todos os caminhos fecham conexão e terminal.
- `--once` atualiza o cache da API quando necessário.
- Falha de um símbolo não derruba a coleta dos outros terminais.

---

### Fase 10 — Cobertura, tipagem e manutenção

#### Cobertura mínima proposta

```text
tests/
├── test_db_schema.py
├── test_engine_premarket.py
├── test_engine_kalman_state.py
├── test_engine_versions.py
├── test_nwe_causality.py
├── test_api_contract.py
├── test_firebase_sync.py
├── test_collector_cycle.py
└── test_zscore.py
```

Para o frontend, adotar Vitest + React Testing Library apenas se houver intenção de manter os testes.
Priorizar inicialmente funções puras extraídas de `App.jsx`: transformação de payload, timezone, NWE,
convicção e padding de série.

#### Limpeza estrutural

- Remover a definição morta de `compute_from_db()` e imports não usados.
- Dividir `frontend/src/App.jsx`, atualmente muito grande, por responsabilidade:
  - cliente/contrato de dados;
  - transformação temporal;
  - NWE;
  - gauge;
  - gráficos;
  - detalhe do ativo.
- Extrair a lógica de overview da camada HTTP para um serviço testável.
- Substituir `except Exception: pass` em caminhos vivos por exceções específicas e logs com contexto.
- Validar target e data nos endpoints; target desconhecido não deve receber silenciosamente parâmetros WIN.
- Documentar dependências Python de forma reproduzível. Se `MetaTrader5` impedir instalação Linux, separar
  dependências core/dev das dependências Windows/live.
- Adicionar CI com testes Python puros e `npm run lint && npm run build` em Linux; deixar integração MT5
  para validação Windows separada.

#### Observabilidade

- Health check deve informar idade da última barra por fonte/terminal, não apenas `status: ok`.
- Logar quantidade de modelos carregados e falhar readiness se for zero em ambiente configurado.
- Logar versão do modelo, timestamp do estado Kalman e cache hit/miss.
- Não expor `/api/internal/notify_update` sem proteção se a API puder ser acessada fora do localhost.

## 5. Ordem recomendada de entrega

### Entrega A — Correção de inicialização e sinal

1. Schema + migração.
2. Contrato `version`.
3. Ghost bars/pré-mercado.
4. Persistência monotônica do Kalman.
5. NWE causal.

Essa entrega deve sair junta apenas se todos os testes quantitativos forem executados. Caso contrário,
dividir em PRs pequenos na mesma ordem.

### Entrega B — Consistência entre ambientes

1. Contrato Firebase completo.
2. Acurácia real no detalhe.
3. Proteção contra responses fora de ordem.
4. Tratamento HTTP comum.

### Entrega C — Operação e desempenho

1. Métricas de duração/cache.
2. Single-flight e isolamento do event loop.
3. Computação incremental/materializada.
4. Robustez do coletor.
5. Health/readiness detalhados.

### Entrega D — Manutenção

1. Modularização do frontend.
2. Remoção de código morto e exceções silenciosas.
3. Dependências reproduzíveis.
4. CI Linux e validação Windows documentada.

## 6. Matriz de validação final

| Área alterada | Validação obrigatória |
|---|---|
| Schema/migração | Banco vazio + banco antigo + migração executada duas vezes |
| Engine V1/V2 | Fixtures determinísticas para as duas versões |
| Kalman | Causalidade, shape, replay histórico e persistência fora de ordem |
| Timezone/B3 | EEST, BRT, pré-mercado, abertura, fechamento e virada de data |
| NWE | Não repaint e equivalência backend/frontend |
| API | Contrato, status HTTP, target/version inválidos e cache |
| Firebase | Fixture equivalente à API local e compatibilidade de schema |
| React | Lint, build, corrida de requests e acurácia |
| Collector | Cleanup, falha parcial, `--once` e notificação |

Comandos esperados ao final:

```bash
pytest -q
python3 -m compileall -q backend scripts tests
cd frontend && npm run lint
cd frontend && npm run build
```

Para mudanças quantitativas, executar também os backtests/calibrações relevantes definidos em
`CLAUDE.md` e comparar métricas antes/depois. Para timezone e gráficos, realizar validação visual com um
ativo global e um ativo B3 nos dois eixos.

## 7. Critério global de conclusão

O plano estará concluído quando:

- uma instalação limpa criar um banco compatível e carregar modelos sem migração manual;
- toda versão solicitada pela UI estiver explicitamente implementada e validada;
- pré-mercado B3 permanecer em 0% até a primeira barra real;
- replay histórico não puder retroceder o estado Kalman persistido;
- NWE não usar dados futuros e produzir resultado consistente em todos os ambientes;
- Firebase e API local entregarem os mesmos metadados e semântica;
- requests antigos não puderem sobrescrever a seleção atual do usuário;
- API permanecer responsiva durante recomputações;
- coletor sempre limpar recursos e notificar corretamente;
- testes, lint e build forem reproduzíveis e executados com sucesso.

## 8. Riscos durante a implementação

- Corrigir `version=both` pode alterar imediatamente os sinais exibidos, pois hoje o fallback leva ao V1.
  Comparar capturas e respostas antes/depois para distinguir correção de regressão.
- Corrigir o cursor de pré-mercado muda `win_return`, divergência, NWE e alertas derivados. Os testes devem
  cobrir toda essa cadeia, não apenas `is_ghost`.
- Proteger estado Kalman pode mudar a continuidade entre reinícios. Registrar estados e timestamps antes
  de implantar em produção.
- Centralizar NWE no backend pode modificar pequenas diferenças numéricas do gráfico; definir tolerância.
- Computação concorrente com SQLite WAL ainda exige transações curtas e tratamento de `database is locked`.
- Alterações de schema devem ser testadas sobre uma cópia do banco de produção antes da execução real.

## 9. Resultado esperado

Após essas entregas, o IRAI terá um caminho de inicialização reproduzível, sinais V1/V2 sem ambiguidade,
processamento causal protegido por regressões, consistência entre localhost e Firebase e uma base mais
segura para evoluir a performance sem comprometer a lógica quantitativa.
