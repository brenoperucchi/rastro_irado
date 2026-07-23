# IRAI-25: Corte Para Runtime Isolado

Este procedimento cria um runtime de produção separado do checkout de
desenvolvimento. Ele é um runbook de execução humana: os scripts ficam
versionados e são revisáveis, mas **nenhum passo com `--apply`, `systemctl
stop/start/restart`, clone de runtime ou cópia de `data/` deve ser executado
sem a autorização explícita de corte**.

## Invariantes

- O runtime alvo fica em
  `C:\Users\brenoperucchi\production\rastro_irado`, acessado no WSL como
  `/mnt/c/Users/brenoperucchi/production/rastro_irado`.
- A raiz é um **clone Git independente** de `origin`, com `HEAD` destacado em
  um SHA completo aprovado. Nunca usar um `git worktree`, um branch móvel ou
  um alias do checkout de desenvolvimento.
- `data/` permanece dentro da raiz de runtime. API, collector, GEX e ticks
  executam `py.exe`; para esses processos, NTFS é I/O local. Colocar SQLite/WAL
  em `/home/...` o transforma em acesso `\\wsl.localhost\...` via 9p e não é
  suportado.
- As seis units IRAI são arquivos persistentes reais, não symlinks para o
  checkout de desenvolvimento. O frontend Vite em `:5175` deixa de depender
  da unit transient após a validação do corte.
- O frontend é provisionado com `npm ci` a partir de
  `frontend/package-lock.json` rastreado no SHA de runtime. `node_modules` do
  desenvolvimento não é copiado.
- O snapshot de dados só ocorre com todas as units IRAI inativas, após
  `PRAGMA wal_checkpoint(TRUNCATE)` retornar exatamente `0|0|0`. A cópia é
  feita em staging, comparada por manifesto e validada por `integrity_check`
  antes de substituir qualquer `data/` de runtime.
- Os poucos artefatos rastreados dentro de `data/` devem estar limpos no
  checkout de desenvolvimento e idênticos ao SHA de runtime. O copiador
  transfere o estado ignorado live, mas recusa transportar um artefato
  versionado divergente e deixar o runtime com código aparentemente sujo.
- Não executar corte ou atualização durante o pregão B3 (09:00--18:00 BRT),
  no disparo GEX (09:10 BRT) ou no disparo do ledger (17:56 BRT).

## Pré-requisitos Humanos

1. Os scripts desta tarefa e `frontend/package-lock.json` foram commitados,
   revisados e enviados ao `origin`.
2. Registrar o SHA completo aprovado em `RUNTIME_REF`; não usar `main`, tag ou
   `HEAD` como substituto.
3. Criar apenas o diretório pai NTFS. Os scripts recusam pais implícitos para
   não transformar um typo em uma raiz nova.
4. Confirmar janela de manutenção e a ausência de writers não gerenciados. O
   checkpoint e os manifestos detectam concorrência observável, mas não podem
   provar que um processo externo não escreverá após a última checagem.
5. Confirmar espaço em `C:` para um clone, `data/`, staging e estado de
   rollback. O estado de rollback deve ficar fora do checkout e no mesmo
   volume NTFS.

```bash
SOURCE_ROOT=/mnt/c/Users/brenoperucchi/devs/rastro_irado
RUNTIME_ROOT=/mnt/c/Users/brenoperucchi/production/rastro_irado
RUNTIME_REF=<SHA-completo-aprovado>
UNIT_DIR=$HOME/.config/systemd/user
STATE_DIR=/mnt/c/Users/brenoperucchi/production/.rastro-irado-state/cutover-$(date +%Y%m%dT%H%M%S)
UNIT_BACKUP_DIR="$STATE_DIR/units"

mkdir -p /mnt/c/Users/brenoperucchi/production/.rastro-irado-state
mkdir -p /mnt/c/Users/brenoperucchi/production
```

## Corte Inicial

### 1. Criar somente o clone de código

Este é o único `--apply` possível antes da janela de manutenção. Ele cria um
clone limpo do `origin` e não altera dados, units ou serviços.

```bash
"$SOURCE_ROOT/scripts/systemd/create-runtime-clone.sh" \
  --source-root "$SOURCE_ROOT" --runtime-root "$RUNTIME_ROOT" \
  --ref "$RUNTIME_REF"

"$SOURCE_ROOT/scripts/systemd/create-runtime-clone.sh" \
  --source-root "$SOURCE_ROOT" --runtime-root "$RUNTIME_ROOT" \
  --ref "$RUNTIME_REF" --apply
```

Não tente executar `copy-runtime-data.sh` em dry-run antes da parada: ele
intencionalmente exige que todos os writers já estejam inativos e que o clone
de destino exista.

### 2. Capturar units atuais e parar writers

Primeiro materialize as definitions efetivamente carregadas. Isto ocorre antes
de parar o frontend transient, para que o rollback tenha uma definição regular
e auditável dele.

```bash
"$SOURCE_ROOT/scripts/systemd/snapshot-runtime-units.sh" \
  --backup-dir "$UNIT_BACKUP_DIR" --apply

systemctl --user stop rastro-irado-gex.timer rastro-irado-p-dynamic-ledger.timer
systemctl --user stop rastro-irado-gex.service rastro-irado-p-dynamic-ledger.service || true
systemctl --user stop rastro-irado-api.service rastro-irado-win-ticks.service \
  rastro-irado-frontend.service rastro-irado-collector.service

systemctl --user is-active rastro-irado-api.service rastro-irado-collector.service \
  rastro-irado-win-ticks.service rastro-irado-gex.service \
  rastro-irado-p-dynamic-ledger.service \
  rastro-irado-frontend.service rastro-irado-gex.timer \
  rastro-irado-p-dynamic-ledger.timer
```

Todos precisam estar `inactive`. O GEX é parado antes do collector porque seu
wrapper pode religar o collector no `trap`. O frontend transient também deve
ter descarregado; o instalador recusa continuar se o `FragmentPath` ainda vier
de `/run/user/.../systemd/transient/`.

### 3. Copiar dados e provisionar frontend

Agora a cópia testa o checkpoint WAL, usa staging no mesmo volume, compara os
manifestos antes/depois e valida o banco de staging. A origem de desenvolvimento
nunca é movida (só lida via staging), então permanece intacta em qualquer falha.
Uma falha antes da promoção não substitui o `data/` do runtime; se uma validação
pós-promoção falhar, o `data/` já promovido pode ter sido substituído — recrie o
clone de runtime a partir de `RUNTIME_REF` e repita a cópia. Nenhum dado de
origem é perdido em nenhum dos casos.

```bash
"$SOURCE_ROOT/scripts/systemd/copy-runtime-data.sh" \
  --source-root "$SOURCE_ROOT" --runtime-root "$RUNTIME_ROOT"

"$SOURCE_ROOT/scripts/systemd/copy-runtime-data.sh" \
  --source-root "$SOURCE_ROOT" --runtime-root "$RUNTIME_ROOT" --apply

"$RUNTIME_ROOT/scripts/systemd/provision-runtime-frontend.sh" \
  --runtime-root "$RUNTIME_ROOT"
"$RUNTIME_ROOT/scripts/systemd/provision-runtime-frontend.sh" \
  --runtime-root "$RUNTIME_ROOT" --apply
```

### 4. Instalar units e validar antes de writers

```bash
"$RUNTIME_ROOT/scripts/systemd/install-runtime-units.sh" \
  --runtime-root "$RUNTIME_ROOT" --unit-dir "$UNIT_DIR" \
  --backup-dir "$UNIT_BACKUP_DIR"

"$RUNTIME_ROOT/scripts/systemd/install-runtime-units.sh" \
  --runtime-root "$RUNTIME_ROOT" --unit-dir "$UNIT_DIR" \
  --backup-dir "$UNIT_BACKUP_DIR" --apply --daemon-reload

"$RUNTIME_ROOT/scripts/systemd/runtime-preflight.sh" \
  --runtime-root "$RUNTIME_ROOT" --development-root "$SOURCE_ROOT" \
  --expected-ref "$RUNTIME_REF" --unit-dir "$UNIT_DIR"

systemctl --user start rastro-irado-api.service
"$RUNTIME_ROOT/scripts/systemd/runtime-preflight.sh" \
  --runtime-root "$RUNTIME_ROOT" --development-root "$SOURCE_ROOT" \
  --expected-ref "$RUNTIME_REF" --unit-dir "$UNIT_DIR" \
  --api-url http://127.0.0.1:8888

systemctl --user start rastro-irado-frontend.service
curl --fail --silent http://127.0.0.1:5175/ >/dev/null
curl --fail --silent 'http://127.0.0.1:8888/api/irai/gex?target=WIN%24N' >/dev/null
```

O primeiro preflight abre o snapshot quiescente com `immutable=1`, pois o
checkpoint WAL já ocorreu com todos os writers parados. O segundo, depois da
API, muda deliberadamente para leitura `mode=ro` normal: assim a validação vê
as páginas ainda presentes no WAL e não certifica apenas o arquivo principal
anterior ao startup.

Somente depois destas verificações, inicie collector, ticks e timers. Este é o
**ponto sem rollback automático**: a partir daqui o runtime passa a receber
dados novos que não existem no checkout de desenvolvimento.

```bash
systemctl --user start rastro-irado-collector.service rastro-irado-win-ticks.service
systemctl --user start rastro-irado-gex.timer rastro-irado-p-dynamic-ledger.timer
systemctl --user enable rastro-irado-frontend.service
systemctl --user status rastro-irado-api.service rastro-irado-collector.service \
  rastro-irado-win-ticks.service rastro-irado-frontend.service \
  rastro-irado-gex.timer rastro-irado-p-dynamic-ledger.timer --no-pager
```

### Rollback do Corte Inicial

Se uma validação falhar **antes** de collector, ticks ou timers serem iniciados:

```bash
systemctl --user stop rastro-irado-api.service rastro-irado-frontend.service || true
"$SOURCE_ROOT/scripts/systemd/restore-runtime-units.sh" \
  --backup-dir "$UNIT_BACKUP_DIR" --unit-dir "$UNIT_DIR" --apply

systemctl --user start rastro-irado-api.service rastro-irado-collector.service \
  rastro-irado-win-ticks.service rastro-irado-frontend.service
systemctl --user start rastro-irado-gex.timer rastro-irado-p-dynamic-ledger.timer
```

O clone novo e seus dados ficam preservados para investigação. Não copie o
runtime de volta sobre a raiz de desenvolvimento: ela permanece sendo a fonte
de rollback do corte inicial.

## Atualização Posterior De Ref

Uma atualização não acompanha o checkout de desenvolvimento. Ela usa outro SHA
completo aprovado e um snapshot de estado próprio. Esse snapshot protege contra
uma migração de banco aplicada pela API nova antes de uma validação falhar.

```bash
UPDATE_STATE_DIR=/mnt/c/Users/brenoperucchi/production/.rastro-irado-state/update-$(date +%Y%m%dT%H%M%S)
UPDATE_UNIT_BACKUP_DIR="$UPDATE_STATE_DIR/units"
mkdir -p /mnt/c/Users/brenoperucchi/production/.rastro-irado-state

# Enquanto as units ainda estão carregadas, capture suas definitions.
"$RUNTIME_ROOT/scripts/systemd/snapshot-runtime-units.sh" \
  --backup-dir "$UPDATE_UNIT_BACKUP_DIR" --apply

systemctl --user stop rastro-irado-gex.timer rastro-irado-p-dynamic-ledger.timer
systemctl --user stop rastro-irado-gex.service || true
systemctl --user stop rastro-irado-api.service rastro-irado-win-ticks.service \
  rastro-irado-frontend.service rastro-irado-collector.service

# Requer todas as units inativas, checkpoint WAL e snapshot verificado de data/.
"$RUNTIME_ROOT/scripts/systemd/snapshot-runtime-state.sh" \
  --runtime-root "$RUNTIME_ROOT" --state-dir "$UPDATE_STATE_DIR" --apply

"$RUNTIME_ROOT/scripts/systemd/update-runtime-ref.sh" \
  --runtime-root "$RUNTIME_ROOT" --state-dir "$UPDATE_STATE_DIR" \
  --ref "$RUNTIME_REF" --fetch --apply
"$RUNTIME_ROOT/scripts/systemd/provision-runtime-frontend.sh" \
  --runtime-root "$RUNTIME_ROOT" --apply
"$RUNTIME_ROOT/scripts/systemd/install-runtime-units.sh" \
  --runtime-root "$RUNTIME_ROOT" --unit-dir "$UNIT_DIR" \
  --backup-dir "$UPDATE_UNIT_BACKUP_DIR" --apply --daemon-reload
```

Repita a validação do passo 4: preflight do disco, API somente, preflight da
API e frontend. Só então inicie writers/timers. Se a validação falhar antes
desse ponto, pare a API/frontend nova e restaure **código, dados e units**:

```bash
systemctl --user stop rastro-irado-api.service rastro-irado-frontend.service || true
"$UPDATE_STATE_DIR/rollback-bin/restore-runtime-state.sh" \
  --runtime-root "$RUNTIME_ROOT" --state-dir "$UPDATE_STATE_DIR" \
  --unit-dir "$UNIT_DIR" --apply
```

O snapshot inclui `rollback-bin/` e seu manifesto SHA-256, capturados do
runtime conhecido antes do checkout candidato. Execute o restore somente por
esse bootstrap: o comando recusa rodar o script que estiver no runtime
candidato. O rollback deixa `data-after-failed/` dentro de `UPDATE_STATE_DIR`
para auditoria. Valide a API restaurada com o SHA de `old-commit` e só então
religue as units antigas. Se writers/timers já foram iniciados, não execute
rollback automático: preserve o estado e faça reconciliação explícita de dados
antes de escolher uma direção.

## Evidência de Conclusão

Anexar ao IRAI-25, após a execução humana, os SHAs de runtime e rollback, os
manifestos, `integrity_check`, `runtime-preflight` antes/depois da API, status
das units e a confirmação de que o frontend persistente responde em `:5175`.
