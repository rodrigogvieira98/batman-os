# Batman OS

[![CI](https://github.com/rodrigogvieira98/batman-os/actions/workflows/ci.yml/badge.svg)](https://github.com/rodrigogvieira98/batman-os/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE) ![Python](https://img.shields.io/badge/python-3.11%2B-blue)

**Governança de engenharia como sistema operacional.** 283 regras determinísticas que
auditam um repositório inteiro — segurança, infraestrutura, qualidade, dívida, dados —
sem chamar um modelo de linguagem. O LLM entra por último, só quando a regra
determinística não decide.

🇧🇷 **Português** · 🇪🇸 [Español](README.es.md) · 🇺🇸 [English](README.en.md)

> "Um sistema inteligente não é aquele que responde todas as perguntas. É aquele que
> reduz continuamente a quantidade de perguntas que precisam ser feitas."

| | |
|---|---|
| **165+ commits** · autor único | **1.500+ testes** de aceitação, um por capítulo da spec |
| **283 specs** de regra | `mypy --strict` e `ruff` limpos, bloqueantes no CI |
| **39 capítulos** de especificação escrita antes do código | roda em produção contra um produto real, a cada 5 minutos |

## Por que isto não é um linter

Um linter responde *"esta linha está errada?"*. O Batman OS responde *"este repositório
está sendo desenvolvido de um jeito que vai falhar?"* — e trata a própria resposta como
algo que pode estar mentindo.

```mermaid
flowchart TB
    R["repositório alvo"] --> S["batman scan --root REPO"]
    S --> E["283 regras determinísticas<br/>segurança · infra · qualidade · dívida · dados"]
    E --> D{"a regra decide?"}
    D -->|sim| V["achado com severidade,<br/>evidência e caminho"]
    D -->|"não decide"| L["escalação para LLM<br/>o último recurso, nunca o primeiro"]
    L --> V
    V --> K{"placar zero?"}
    K -->|sim| CAN["canário REPROVA<br/>silêncio não é aprovação"]
    K -->|não| FO{"--fail-on high"}
    FO -->|"há high ou critical"| X["saída 1 — o CI barra"]
    FO -->|"nenhum"| OKN["saída 0"]

    classDef det fill:#e0e7ff,stroke:#4f46e5,color:#1e1b4b
    classDef gate fill:#fef3c7,stroke:#d97706,color:#78350f
    classDef bad fill:#fee2e2,stroke:#dc2626,color:#7f1d1d
    classDef ok fill:#dcfce7,stroke:#16a34a,color:#14532d
    classDef base fill:#f8fafc,stroke:#94a3b8,color:#0f172a
    class E,V det
    class D,K,FO gate
    class CAN,X bad
    class OKN ok
    class R,S,L base
```

<sub>O LLM é o <b>último</b> passo, nunca o primeiro — e o canário roda antes do código de saída, porque <code>0 achados</code> e <code>tudo certo</code> são indistinguíveis.</sub>

Dois defeitos reais que o projeto encontrou **em si mesmo**, e as defesas que nasceram deles:

**1. O portão não enxergava o que foi commitado.** `pytest`, `mypy` e `ruff` rodam na
*árvore de trabalho*. Um arquivo importado pelo código e nunca versionado passa nos
quatro — e quebra no primeiro clone limpo. Aconteceu duas vezes. A defesa
(`scripts/verificar_head_autocontido.sh`) materializa o HEAD num worktree efêmero e
exige que `(tipos, specs)` bata com a árvore.

**2. "Zero achados" e "tudo certo" são indistinguíveis.** Uma execução no CI enumerou
1.382 arquivos, reportou `0 achado(s)` e saiu com código 0 — porque o pacote foi
publicado sem as 283 specs, e `glob` num diretório inexistente não levanta erro. Um
portão que só olha o código de saída teria dado verde. A defesa é um canário que
**reprova placar zero**: silêncio deixou de ser aprovação.

Essa é a tese do projeto inteiro — **falha silenciosa é o inimigo**. Um processo que
trava é pior que um que descarta, porque descarte aparece no relatório e trava parece
lentidão.

```mermaid
flowchart LR
    subgraph local["Portão local — pre-push"]
        direction TB
        T1["pytest"] --> T2["mypy src/ tests/"] --> T3["ruff check + ruff format --check"] --> T4["verificar_head_autocontido.sh<br/>materializa o HEAD num<br/>worktree efêmero"]
    end
    subgraph ci["Portão no CI"]
        direction TB
        S1["os mesmos quatro"] --> S2["batman scan --fail-on high"] --> S3["canário: placar zero REPROVA"]
    end
    local -->|"só empurra se passar"| ci

    classDef step fill:#e0e7ff,stroke:#4f46e5,color:#1e1b4b
    classDef guard fill:#fef3c7,stroke:#d97706,color:#78350f
    class T1,T2,T3,S1,S2 step
    class T4,S3 guard
    style local fill:#f8fafc,stroke:#cbd5e1,color:#0f172a
    style ci fill:#f8fafc,stroke:#cbd5e1,color:#0f172a
```

## Arquitetura em uma passada

```
docs/spec/       especificação (39 capítulos, 10 volumes, 17 ADRs) — vence o código
                 em caso de divergência, por regra explícita
src/batman_os/
  foundation/    tipos do glossário oficial (Mission, Capability, Operator, ...)
  kernel/        Mission Runtime, Planning/Decision/Workflow Engine, Event Bus, Scheduler
  runtime/       Capability/Execution Engine, Operational Memory (SQLite), concorrência
  capabilities/  operadores, certificação, skills, tools — e as 283 regras migradas
  workflow/      missões formais, playbooks multi-step, recuperação e fallback
  learning/      Knowledge Graph, evolução de regra/workflow, aprendizado operacional
  governance/    Governance Engine, Human Review, escalação para LLM, observabilidade
  api/           API HTTP (FastAPI) — tenant derivado da chave, nunca do corpo
tests/           1 arquivo por capítulo, nomeado pelos critérios de aceitação da spec
```

**Isolamento multi-tenant é estrutural**, em leitura e mutação — não um `WHERE tenant_id`
espalhado pelas queries. O tenant aplicado a uma Missão vem 100% da chave de API usada;
não existe endpoint que aceite o tenant como campo do payload.

---

Este repositório é a **implementação de referência** do Batman OS, cuja especificação
de arquitetura completa vive em [`docs/spec/`](docs/spec/README.md). O Batman OS é a
evolução natural do Batman em `radar-preditivo/Batman/` (270 regras de governança,
46 agentes, ledger, patrulha, sweep) — desacoplado para poder operar em outros
projetos, sem perder nada do que já funciona.

**Regra de ouro** (herdada da especificação): sempre que houver divergência entre o
código deste repositório e `docs/spec/`, a especificação vence, até que uma nova ADR
aprove formalmente a mudança.

## Estado

**Especificação completa: 39 capítulos, 10 volumes, 17 ADRs, 6 Anexos** (Volumes
I–X). Ver `docs/spec/SUMMARY.md` para o índice completo. Volumes VIII
(Infrastructure) e X (Appendices) são volumes de topologia física e consolidação
— não introduzem componentes de código novos a implementar (ver nota sobre o
Cap.32 abaixo).

**Migração do catálogo Batman: 271/271 regras** (concluída em 2026-07-10),
validada por comparação de fingerprint byte-a-byte contra o motor legado
(`scripts/compare_migracao.py`) rodando contra o `radar-preditivo` real.

**Roadmap de plataforma: Fases 1–11 concluídas** (2026-07-12) — de "Scanner
Determinístico" a "Plataforma Operacional de Engenharia": Kernel/Runtime com
persistência real e paralelismo (`EventBus`/`OperationalMemory` em SQLite,
`runtime/dispatcher.py`), isolamento multi-tenant estrutural em leitura e
mutação, Playbooks multi-step com decision points reais na autoria, Mission
Graph (`learning/mission_reconciliation.py`), e uma API HTTP completa e
autenticada (ver seção "API HTTP" abaixo). Status detalhado, o que existe hoje e
o backlog daquela fase (com contexto e justificativa por item) em
[`docs/PLATFORM_ROADMAP_BACKLOG.md`](docs/PLATFORM_ROADMAP_BACKLOG.md).

**Frente vigente: Cobertura Total** ([`docs/PLANO_COBERTURA_TOTAL.md`](docs/PLANO_COBERTURA_TOTAL.md),
ordem do DEV de 2026-07-30) — 8 agentes para as dimensões em que o scanner é
cego, em 3 ondas. **Onda 1 concluída** (`dados-sentinela`, `qa-visual` e os
consertos de QA-RUN/SD/FUI); Ondas 2 (`sec-dinamica`, `infra-sentinela`,
`dep-auditoria`) e 3 (`gov-verdade`, `ml-guarda`, integração ao CI) **abertas**.
O roadmap de plataforma acima está encerrado e não é mais a direção do trabalho.

**Hoje: 1.510 testes, 283 specs de regra, `mypy --strict`/`ruff` limpos.**

**Portão local** (`scripts/git-hooks/pre-push`): `pytest` · `mypy src/ tests/` ·
`ruff check` · `ruff format --check` · **`scripts/verificar_head_autocontido.sh`**.
Este último existe porque os quatro anteriores rodam na *árvore de trabalho* e
nenhum deles enxergava o que foi realmente commitado — defeito que já ocorreu
duas vezes (arquivo importado pelo HEAD e nunca versionado; spec faltante que
sumia em silêncio, porque os loaders usam `glob` e uma regra ausente não levanta
erro). Ele materializa o HEAD num worktree efêmero e exige que `(tipos, specs)`
bata com a árvore. Escape consciente: `BATMAN_SKIP_HEADCHECK=1`.

## Estrutura

```
docs/spec/          # especificação completa (fonte da verdade — só leitura para o código)
src/batman_os/
  foundation/        # Vol. I — tipos oficiais do glossário (Mission, Capability, etc.)
  kernel/            # Vol. II — Mission Runtime, Planning/Decision/Workflow Engine, Event Bus, Scheduler
  runtime/           # Vol. III — Capability Engine, Execution Engine, Operational Memory, Concorrência
  capabilities/      # Vol. IV — Operator, certificação de Capability, Skills, Tools, Cooperação
    rules/           # Capabilities migradas do Batman atual (não é um Volume da spec)
  workflow/          # Vol. V — Missões formais, Playbooks, Recuperação/Fallback
  learning/          # Vol. VI — Knowledge Graph, Rule/Workflow Evolution, Operational Learning
  governance/        # Vol. VII — Governance Engine, Human Review, LLM Escalation, Observability Engine
  orchestration/     # canalização Kernel+Runtime+Capabilities -> fluxo executável (não é um Volume)
  cli/               # entry point `batman` (Vol. IX Cap.34, Fase 0 — Walking Skeleton)
tests/               # 1 arquivo de teste por capítulo, nomeado pelos próprios AT-X.Y da spec
scripts/
  compare_migracao.py  # compara fingerprints do batman-os com o motor legado, no mesmo alvo real
```

**Nota sobre o Volume VIII, Cap.32 (Estrutura de Diretórios):** aquele capítulo
propõe pastas de topo kebab-case (`kernel/`, `runtime/`, `workflow/`, `learning/`,
`governance/`, `shared/`, uma subpasta por capítulo). Decisão do autor (2026-07-04):
manter a estrutura acima como a **tradução Python** desse mapeamento — mesmo
componente lógico → mesmo local, adaptado ao empacotamento idiomático da linguagem
(`src/<pacote>/`, arquivos `snake_case.py`, já que nomes kebab-case não são
identificadores Python válidos), o mesmo raciocínio já aplicado ao pseudocódigo
TypeScript-like da spec. `foundation/` cumpre o papel que o Cap.32 chama de
`shared/`. Isso não é dívida a resolver — é a mesma "regra de ouro" (spec vence)
aplicada com bom senso de tradução de convenção, não de estrutura.

## Convenção de nomenclatura

O vocabulário oficial do Kernel (`Mission`, `Capability`, `Skill`, `Operator`,
`Workflow`, `Playbook`, `Knowledge Asset`, `Decision`, `Event`) segue exatamente a
definição do Volume I, Capítulo 4 — **não pode ser usado com outro sentido em
nenhum lugar do código** (a própria especificação trata divergência de nomenclatura
como dívida técnica, não sinônimo válido).

Ao redor desse núcleo, por decisão do autor, a nomenclatura já usada no Batman
atual é preservada o máximo possível:

| Termo oficial da spec (imutável) | Nomenclatura do Batman atual preservada como... |
|---|---|
| `Mission`, `Capability`, `Skill`, `Operator`, `Workflow`, `Playbook`, `Knowledge Asset`, `Decision`, `Event` | — núcleo do Kernel, fixado pelo Cap.4 |
| — | CLI continua `batman`, mesmos subcomandos (`scan`, `patrol`, `sweep`, `fp`, `agents`, `init`) |
| — | **"Alfred"** — nome do Operador/Capability de relatório e observabilidade |
| — | **"Robin"** — nome do Operador dinâmico que roda testes de verdade |
| — | Os **46 "agentes"** atuais (`sre`, `security_engineer`, `ai_engineer`, `vps_infra`, `ethical_hacker`, `red_team`, ...) viram os nomes dos 46 Operadores |
| — | Convenção de ID de regra (`PREFIXO-NNN`: `SRE-002`, `EH-003`, `GOVDEBT-001`, `SWEEP-001`, `META-001/002`) preservada em `RuleDefinition.id` |
| — | **"ledger"**, **"gate"**, **"sweep"**, **"patrol"**, **"deferred"**, **"supressoes"** preservados como nomes informais dos mecanismos equivalentes |

## Idioma

Documentação, comentários e docstrings em português — mesma língua da
especificação e do Batman atual. Identificadores de código (classes, funções,
variáveis) em inglês, espelhando o pseudocódigo da especificação.

## Comandos

```bash
python3.11 -m venv .venv
.venv\Scripts\activate            # Windows

pip install -e ".[dev]"

pytest                             # todos os testes de aceitação (AT-X.Y)
mypy src/
ruff check src/ tests/

batman scan --root <repo>          # roda o primeiro lote de Capabilities migradas contra um repo real
batman scan --root <repo> --fail-on high   # saida 1 se houver achado high/critical
```

## API HTTP

Fases 6-8 do roadmap de plataforma ([`docs/PLATFORM_ROADMAP_BACKLOG.md`](docs/PLATFORM_ROADMAP_BACKLOG.md))
adicionaram uma API HTTP (FastAPI) sobre o mesmo Kernel — `pip install -e ".[api]"`
para instalar `fastapi`/`uvicorn`/`httpx`.

```bash
uvicorn batman_os.api.app:criar_app --factory
```

Toda requisição a `/missions/*` e `/jobs/*` exige autenticação real via
`Authorization: Bearer <chave>` — não existe mais seleção de tenant sem prova
(Fase 8). Configure `BATMAN_API_KEYS` no `.env` (objeto JSON `tenant->chave`, ver
`.env.example`) antes de subir o servidor; gere uma chave nova com:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Sem header válido, todo endpoint responde `401`. O tenant aplicado à Missão é
derivado 100% da chave usada — nunca de um campo do corpo da requisição.

```bash
curl -X POST http://localhost:8000/missions/security-audit \
  -H "Authorization: Bearer <sua-chave>" \
  -H "Content-Type: application/json" \
  -d '{"root": "/caminho/do/repo"}'
# -> 202 {"mission_id": "..."}

curl http://localhost:8000/jobs/<mission_id> \
  -H "Authorization: Bearer <sua-chave>"
```

## Portão automático (CI)

Dois mecanismos rodam `pytest` + `mypy` + `ruff check` + `ruff format --check`
automaticamente, não só manualmente (mesmo padrão já usado no `radar-preditivo`):

- **`.github/workflows/ci.yml`** — roda em todo push/PR para `main` assim que o
  repositório tiver um remoto no GitHub (não precisa de remoto para o arquivo
  existir, só para o workflow disparar).
- **Hook `pre-push` local** — bloqueia o `git push` se qualquer verificador falhar.
  Não é versionado pelo git (`.git/hooks/` fica de fora do repositório) — instalar
  em cada clone novo:
  ```bash
  cp scripts/git-hooks/pre-push .git/hooks/pre-push
  chmod +x .git/hooks/pre-push
  ```
