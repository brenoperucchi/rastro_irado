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
   provar que um processo externo não escreverá após a última checagem. A
   evidência executável dessa ausência está no fim do passo 2 (bloco
   pós-stop/pré-cópia); este pré-requisito segue sendo o ack humano do resíduo
   t+1 que nenhum comando cobre.
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

Antes de copiar, prove que nenhum writer **não gerenciado** sobreviveu ao stop.
`is-active == inactive` só prova que o systemd desligou a UNIT; o wrapper da API
dá `exec` num `py.exe` do Windows (`api-wsl.sh:19`) e o de ticks lança o terminal
MT5 pelo `powershell.exe` e só depois dá `exec` num `py.exe`
(`win-ticks-wsl.sh:22-26`) — um órfão Windows pode seguir vivo segurando
`irai.db`, **invisível ao `lsof` do WSL** (que só enxerga processos Linux). A
checagem **autoritativa** aqui é a sonda de *open exclusivo* (1): ela pega
QUALQUER handle Windows do arquivo — inclusive de outro usuário/SYSTEM e de um
writer "formato operador" (uma calibração, um `db.py --migrate` ou um REPL cuja
CommandLine não casa nenhum marker de serviço). As demais checagens corroboram e
ajudam a achar o PID. Rode este bloco DEPOIS do `is-active` e IMEDIATAMENTE antes
da cópia; ele é a evidência executável do Pré-requisito Humano 4 (o resíduo t+1
continua sendo o ack humano de janela).

> Rode as linhas **sem** `set -e`: o `is-active` acima retorna status ≠ 0
> justamente no caso esperado (`inactive`), e num shell com `set -e` isso
> abortaria antes deste bloco.

```bash
PS_BIN=/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe  # caminho absoluto: não depende do PATH interop
DB_ROOT="$SOURCE_ROOT"          # corte inicial checkpoint-a a ORIGEM (na "Atualização" abaixo, DB_ROOT="$RUNTIME_ROOT")
DB_PATH="$DB_ROOT/data/irai.db"
DB_WIN="$(wslpath -w "$DB_PATH")"   # caminho NTFS (C:\...) para as sondas do Windows

# (0) GUARDA DE IDENTIDADE: as units escrevem via o WorkingDirectory DELAS. Prove
#     que esse arquivo é o MESMO que DB_PATH (hoje é alias do mesmo inode). Se
#     divergir, o checkpoint/cópia tocaria um db diferente do que os writers
#     seguram, e (1)-(8) pareceriam limpas sobre o db errado.
UNIT_WD="$(systemctl --user show -p WorkingDirectory --value rastro-irado-api.service 2>/dev/null)"
[ -n "$UNIT_WD" ] || UNIT_WD=/home/brenoperucchi/Devs/rastro_irado
if [ "$(readlink -f "$UNIT_WD/data/irai.db" 2>/dev/null)" = "$(readlink -f "$DB_PATH" 2>/dev/null)" ]; then
    echo "OK: units e DB_ROOT apontam para o mesmo irai.db"
else
    echo "FALHA: WorkingDirectory das units ($UNIT_WD) != DB_ROOT ($DB_ROOT) — NÃO copie até resolver"
fi

# (1) SONDA AUTORITATIVA (Windows) — open EXCLUSIVO do irai.db. FileShare 'None'
#     lança IOException se QUALQUER processo Windows (qualquer usuário, incl.
#     SYSTEM) tiver o arquivo aberto — pega writer "formato operador"/SYSTEM que a
#     (3) por CommandLine e a (5) handle.exe sem admin NÃO enxergam. É read+close,
#     não muta. $p/$s são do PowerShell (\$ no bash); $DB_WIN é expandido pelo bash.
"$PS_BIN" -NoProfile -Command "
  \$p = '$DB_WIN'
  if (-not (Test-Path -LiteralPath \$p)) { 'NAO CHECADO: irai.db nao encontrado no Windows -> ' + \$p; exit 2 }
  try {
    ([IO.File]::Open(\$p,'Open','ReadWrite','None')).Close()
    'OK: irai.db LIVRE (0 handle Windows)'
    foreach (\$s in @(\$p + '-wal', \$p + '-shm')) {
      if (Test-Path -LiteralPath \$s) {
        try { ([IO.File]::Open(\$s,'Open','ReadWrite','None')).Close(); 'OK: livre ' + \$s }
        catch { 'SEGURADO no Windows: ' + \$s + ' -> ' + \$_.Exception.Message; exit 1 }
      }
    }
  } catch { 'SEGURADO no Windows: irai.db -> ' + \$_.Exception.Message + ' (use (3) p/ achar o PID)'; exit 1 }
"

# (2) SERVIÇOS NSSM: o repo tem scripts/install_nssm_services.ps1, que instala
#     IRAI_* como serviços Windows LocalSystem (SYSTEM) — deployment alternativo ao
#     systemd. Se existirem NESTE host, rodam à revelia do systemd e com CommandLine
#     nula sem admin (invisíveis a (3)). Esperado: nenhum. Se listar, pare/desinstale
#     em shell ELEVADO antes de prosseguir.
"$PS_BIN" -NoProfile -Command "\$s = Get-Service IRAI_* -EA SilentlyContinue; if (\$s) { (\$s | Format-Table -Auto Name,Status,StartType | Out-String); 'ATENCAO: serviços NSSM IRAI_* existem -> pare/desinstale ELEVADO' } else { 'OK: nenhum serviço NSSM IRAI_*' }"

# (3) IDENTIFICAÇÃO (Windows) — lista processos IRAI por CommandLine p/ achar o PID
#     a matar. NÃO é o gate (writer operador/SYSTEM não casa aqui — use (1)/(2)).
#     Aspas SIMPLES no bash: $_ e $PID são do PowerShell. Get-CimInstance/Win32_Process
#     traz CommandLine (legível p/ processos do mesmo usuário sem admin). $PID exclui
#     o próprio powershell (cuja CommandLine contém os markers do filtro). INSPECIONE.
"$PS_BIN" -NoProfile -Command 'Get-CimInstance Win32_Process | Where-Object { ($_.CommandLine -match "backend[\\/]workers|backend\.api\.main|uvicorn|collector_wsl|gex_worker|tick_collector_wsl") -and $_.ProcessId -ne $PID } | Select-Object ProcessId,ParentProcessId,Name,CommandLine | Format-List'

# (4) PORTA 8888 (API = py.exe Windows): Get-NetTCPConnection dá o OwningProcess,
#     que o curl NÃO dá. -ErrorVariable separa "sem listener" (NotFound = ok) de erro
#     REAL de consulta (=> NAO CHECADO), sem o fail-open do -EA SilentlyContinue mudo.
#     mirrored (/mnt/c/Users/brenoperucchi/.wslconfig:2): curl 127.0.0.1:8888 ATÉ
#     alcança a API Windows; só não identifica o dono.
"$PS_BIN" -NoProfile -Command "if (-not (Get-Command Get-NetTCPConnection -EA SilentlyContinue)) { 'NAO CHECADO: Get-NetTCPConnection ausente -> netstat -ano | findstr :8888'; exit 2 }; \$e=\$null; \$c = Get-NetTCPConnection -State Listen -LocalPort 8888 -EA SilentlyContinue -ErrorVariable e; if (\$c) { \$c | Select-Object LocalAddress,LocalPort,OwningProcess | Format-List; 'porta 8888: HA listener (OwningProcess acima)' } elseif (\$e -and \$e[0].FullyQualifiedErrorId -notmatch 'NotFound') { 'NAO CHECADO: consulta 8888 falhou -> ' + \$e[0].Exception.Message; exit 2 } else { 'porta 8888: nada escutando' }"

# (5) HANDLE (Sysinternals handle.exe) — CORROBORAÇÃO de (1), não autoritativa: SEM
#     shell elevado a cobertura é INCOMPLETA (pode não ver handles de outro usuário/
#     SYSTEM), e a (1) já cobre esses. Trate binário-ausente e status ≠ 0 como
#     "NÃO CHECADO", nunca como limpo.
if command -v handle.exe >/dev/null 2>&1; then
    handle.exe -accepteula -nobanner irai.db || echo "NÃO CHECADO: handle.exe status ≠ 0 (rode elevado; a autoritativa é (1))"
else
    echo "handle.exe indisponível (não bloqueante; a sonda (1) é a autoritativa)"
fi

# (6) LINUX — strays nativos (ledger python3; vite/node) + holder Linux do db.
if command -v pgrep >/dev/null 2>&1 && command -v grep >/dev/null 2>&1; then
    strays="$(pgrep -af 'rastro_irado|irai|uvicorn|vite|p[-_]dynamic|collector|win[-_]ticks' | grep -v -e pgrep -e '/verify-')"
    [ -n "$strays" ] && { printf '%s\n' "$strays"; echo "^ INSPECIONE strays Linux"; } || echo "(nenhum stray Linux por nome)"
else
    echo "NÃO CHECADO: pgrep/grep ausente"
fi
if command -v lsof >/dev/null 2>&1; then
    if [ -e "$DB_PATH" ]; then
        holders="$(lsof -- "$DB_PATH" 2>/dev/null)"   # lsof é Windows-cego; complementa (1)
        [ -n "$holders" ] && { printf '%s\n' "$holders"; echo "^ holder Linux do db"; } || echo "lsof: nenhum holder Linux do db"
    else
        echo "NÃO CHECADO: $DB_PATH inexistente (DB_ROOT errado?)"
    fi
else
    echo "NÃO CHECADO: lsof ausente"
fi

# (7) PORTAS 5175 (Vite) e 8888, lado LINUX (em mirrored o Get-NetTCPConnection pode
#     não mostrar um listener Linux). Captura o ss ANTES do grep p/ falha de ss virar
#     "NÃO CHECADO", não "fechado".
if command -v ss >/dev/null 2>&1; then
    if lst="$(ss -ltnH 2>/dev/null)"; then
        open="$(printf '%s\n' "$lst" | grep -E ':(5175|8888)\b' || true)"
        [ -n "$open" ] && { printf '%s\n' "$open"; echo "FALHA: 5175/8888 ainda aberto no lado Linux"; } || echo "OK: 5175/8888 fechados (Linux)"
    else
        echo "NÃO CHECADO: ss falhou"
    fi
else
    echo "NÃO CHECADO: ss ausente -> netstat -ltn | grep -E ':(5175|8888)'"
fi

# (8) Sinal barato e não-mutante do -wal (1 amostra: mede PRESENÇA/tamanho, NÃO
#     'crescendo'). -wal grande sugere writer recente; ausente/0 é normal. NÃO é o selo.
if [ -e "$DB_PATH" ]; then
    if [ -e "$DB_PATH-wal" ]; then ls -l "$DB_PATH-wal"; echo "^ -wal presente (tamanho; p/ tendência amostre 2x com sleep)"; else echo "sem -wal (quiescente)"; fi
else
    echo "NÃO CHECADO: $DB_PATH inexistente"
fi
```

**Não leia um `echo` de falha como prova de ausência:** um comando que não roda
(binário ausente, sem permissão, consulta com erro) reporta **"NÃO CHECADO"**, não
quiescência — as guardas acima separam "rodou e não achou" de "não rodou/falhou".
A checagem que MANDA é a sonda de open exclusivo (1); (3)/(5) só ajudam a achar o
PID. Qualquer processo listado em (3) que seja inequivocamente IRAI e que o systemd
já deveria ter parado: Windows → `"$PS_BIN" -NoProfile -Command "Stop-Process -Id
<PID> -Force"`; Linux → `kill <PID>`. **Nunca** mate um PID só porque o nome é
python/node — confirme a CommandLine primeiro. O terminal MT5 do win-ticks
(portable, path `ira_ticks`) pode remanescer: é fonte de dados, não writer do
`irai.db`, e é fechado à parte. Depois **re-rode (0)-(8)** até a (1) dar LIVRE.

O selo final NÃO é manual: `copy-runtime-data.sh --apply` executa
`runtime_require_all_units_inactive` e `wal_checkpoint(TRUNCATE)` exigindo
`0|0|0` + `-wal` vazio como PRIMEIRA ação, imediatamente antes do stage-read
(`runtime-data.sh:17-39`, `copy-runtime-data.sh:68,76-82`). Um checkpoint
`0|0|0` bem-sucedido prova quiescência **naquele instante** — se um writer
estiver de fato escrevendo ou segurando o write-lock, o `TRUNCATE` não retorna
`0|0|0` e a cópia ABORTA sem tocar o runtime. Duas ressalvas mantêm o
Pré-requisito Humano 4 como ack: (a) ele **não** prova que não exista uma conexão
ociosa capaz de escrever DEPOIS (os manifestos `source-before`/`source-after` só
detectam mudança DURANTE a cópia); (b) esse checkpoint roda no `sqlite3` **Linux
sobre 9p** (a origem é DrvFs), enquanto os writers são `py.exe` **Windows/NTFS** —
a interoperabilidade de lock 9p↔NTFS não é garantida, então o `0|0|0` não deve ser
o ÚNICO detector de um writer Windows vivo. É por isso que a sonda de open
exclusivo (1) roda ANTES, no mesmo domínio de lock dos writers: ela prova
zero-handle-Windows para o checkpoint Linux nunca disputar com um writer nativo.
Este bloco REDUZ, não elimina, a janela.

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
# reenable (não apenas start): recria os symlinks timers.target.wants a partir do
# fragment do runtime, tornando o enablement PERSISTENTE e repontando qualquer
# symlink herdado do checkout de dev. Isso cobre a forma que o CORTE poderia
# deixar o timer silenciosamente sem disparar. Risco residual à parte (NÃO coberto
# por reenable/linger): o ledger é Persistent=false de propósito — a captura das
# 17:56 é sensível ao wall-clock, então um catch-up tardio escreveria uma linha de
# sessão errada. Logo um dia com a máquina desligada às 17:56 é um buraco de UMA
# sessão, aceito por design; não é perda da acumulação já gravada nem deve virar
# Persistent=true.
systemctl --user reenable rastro-irado-gex.timer rastro-irado-p-dynamic-ledger.timer
systemctl --user start rastro-irado-gex.timer rastro-irado-p-dynamic-ledger.timer
systemctl --user enable rastro-irado-frontend.service
# Timers --user só disparam sem sessão interativa (distro headless) se o usuário
# tem linger. É estado PERSISTENTE do host (sobrevive a logout e boot); para o
# próprio usuário não exige root. Reverter: loginctl disable-linger "$USER".
loginctl enable-linger "$USER"
systemctl --user status rastro-irado-api.service rastro-irado-collector.service \
  rastro-irado-win-ticks.service rastro-irado-frontend.service \
  rastro-irado-gex.timer rastro-irado-p-dynamic-ledger.timer --no-pager

# Gate de durabilidade: falha fechado se algum timer não estiver persistentemente
# enabled, ativo, com o symlink wants resolvendo para dentro do runtime, com um
# próximo disparo agendado e (com --require-linger) linger ligado. Protege contra
# o CORTE deixar o timer silenciosamente desabilitado / apontando pro checkout de
# dev / runtime-only / inativo — não contra a máquina estar desligada às 17:56
# (esse buraco de uma sessão é aceito por design; ver comentário do reenable).
"$RUNTIME_ROOT/scripts/systemd/verify-runtime-units.sh" \
  --unit-dir "$UNIT_DIR" --require-linger
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

# Rode aqui as checagens (0)-(8) do bloco pós-stop/pré-cópia do passo 2, porém
# SUBSTITUINDO a atribuição por DB_ROOT="$RUNTIME_ROOT" (não é reexecução literal:
# o bloco fixa DB_ROOT="$SOURCE_ROOT") — inclusive a sonda de open exclusivo (1),
# que agora prova o RUNTIME db livre. A atualização checkpoint-a o próprio DB do
# runtime via snapshot-runtime-state.sh, não a origem.

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
manifestos, `integrity_check`, a saída do bloco de quiescência pós-stop
(sonda de open exclusivo do `irai.db` = LIVRE, serviços NSSM ausentes,
processos Windows/Linux, portas 8888/5175),
`runtime-preflight` antes/depois da API, status das units, a saída de
`verify-runtime-units.sh --require-linger` (prova de que os timers ficaram
persistentes, ativos e wants-linked no runtime) e a confirmação de que o
frontend persistente responde em `:5175`.
