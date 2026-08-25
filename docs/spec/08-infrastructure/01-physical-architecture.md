# Capítulo 31 — Arquitetura Física

**Volume:** VIII — Infrastructure
**Status da especificação:** v0.1 (Draft)
**Depende de:** Toda a obra até aqui — este capítulo mapeia componentes lógicos (Volumes I–VII) para topologia de execução real

---

## 31.0 Objetivo do capítulo

Todos os volumes anteriores especificaram componentes lógicos (Mission Runtime, Decision Engine, Knowledge Graph, Governance Engine...) sem dizer onde e como eles rodam fisicamente. Este capítulo faz essa tradução: qual componente é um serviço próprio, qual é uma biblioteca embutida, qual armazenamento cada um exige, e como o conjunto escala.

## 31.1 Motivação

Uma especificação de arquitetura sem mapeamento físico corre o risco de permanecer uma peça acadêmica. Este capítulo garante que cada decisão dos Volumes I–VII (event sourcing, isolamento multi-tenant, separação de camadas) tenha uma resposta concreta de "isso roda onde, e com qual armazenamento".

## 31.2 Princípio de mapeamento: um serviço físico pode agrupar múltiplos componentes lógicos, nunca o contrário

> **Regra estrutural:** é aceitável que dois componentes lógicos definidos em volumes diferentes sejam implantados como o mesmo processo físico (por razões de latência ou custo operacional). **Nunca** é aceitável que um único componente lógico seja fragmentado em múltiplos processos físicos sem um contrato de consistência explícito entre eles — isso reintroduziria o risco de múltiplas fontes de verdade que as ADRs 0003, 0004 e 0010 já rejeitaram.

## 31.3 Mapeamento componente lógico → unidade física

| Componente lógico | Volume/Cap. | Unidade física recomendada | Armazenamento |
|---|---|---|---|
| Mission Runtime, Planning Engine, Decision Engine, Workflow Engine | Vol. II, Cap. 6–9 | Serviço único "Kernel Service" (acoplamento forte, latência crítica entre eles) | Nenhum estado próprio — deriva do Event Store |
| Event Bus | Vol. II, Cap. 10 | Log distribuído append-only dedicado | Event Store (ex.: log particionado por `tenantId`) |
| Scheduler | Vol. II, Cap. 10 | Processo dentro do Kernel Service, com fila própria | Fila persistente (não apenas em memória, para sobreviver a reinício) |
| Capability Registry, Playbook Registry, Rule Registry | Vol. III Cap. 11; Vol. V Cap. 21; Vol. VI Cap. 24 | Serviço "Catalog Service" — os três catálogos compartilham o mesmo padrão de certificação e versionamento | Banco relacional com versionamento (histórico nunca sobrescrito) |
| Execution Engine + Operadores | Vol. III, Cap. 12; Vol. IV, Cap. 15 | Processos isolados por Operador (bulkhead físico, não apenas lógico) — um Operador nunca compartilha processo com outro de criticidade distinta | Sem estado persistente próprio; usa Tools (Cap. 18) para I/O externo |
| Operational Memory | Vol. III, Cap. 13 | Projeção derivada do Event Store, materializada em banco analítico | Banco colunar/analítico, otimizado para consulta agregada, não transacional |
| Knowledge Graph | Vol. VI, Cap. 23 | Serviço de grafo dedicado, reconciliado periodicamente | Banco de grafo (ou relacional com views recursivas, a critério de escala) |
| Governance Engine, Observability Engine | Vol. VII, Cap. 27, 30 | Serviço "Governance Service", consumidor read-only do Event Bus e dos catálogos | Séries temporais para métricas; nenhum estado autoritativo próprio |
| World Model (ADD-0002, `Proposed`) | Anexo | Serviço próprio, se aceito — nota de compatibilidade futura, seção 31.6 | A definir se/quando aceito |

## 31.4 Diagrama de topologia física

```mermaid
flowchart TB
    subgraph KS["Kernel Service"]
        MR[Mission Runtime]
        PE[Planning Engine]
        DE[Decision Engine]
        WE[Workflow Engine]
        SC[Scheduler]
    end
    subgraph CS["Catalog Service"]
        CR[Capability Registry]
        PR[Playbook Registry]
        RR[Rule Registry]
    end
    subgraph EL["Execution Layer"]
        Op1[Operador 1<br/>processo isolado]
        Op2[Operador 2<br/>processo isolado]
        Op3[Operador N...]
    end
    subgraph GS["Governance Service"]
        GE[Governance Engine]
        OE[Observability Engine]
        HR[Human Review UI/API]
    end

    ES[(Event Store<br/>append-only)]
    OM[(Operational Memory<br/>banco analítico)]
    KG[(Knowledge Graph<br/>banco de grafo)]

    KS --> ES
    KS --> CR
    KS --> Op1
    KS --> Op2
    KS --> Op3
    ES --> OM
    ES --> KG
    CR --> KG
    PR --> KG
    RR --> KG
    ES --> GE
    OM --> GE
    KG --> GE
    GE --> OE
```

## 31.5 Escalabilidade e modelo de deployment

- **Kernel Service:** escalado horizontalmente por `tenantId` (Vol. III, Cap. 14) — cada réplica processa um subconjunto de tenants, nunca o mesmo tenant em duas réplicas simultaneamente (evita condições de corrida na máquina de estados de uma mesma Missão).
- **Execution Layer:** escalado horizontalmente por classe de Operador, com o Resource Limiter (Vol. III, Cap. 12) aplicado por instância física, não apenas logicamente.
- **Catalog Service:** read-heavy, write-raro (certificações não são operações de alta frequência) — otimizado para réplicas de leitura, com escrita centralizada para preservar a garantia de versionamento sem condição de corrida.
- **Governance Service:** stateless na maior parte, pode escalar livremente — nunca é caminho crítico de latência do Kernel (consistente com ADR-0012 e ADR-0010, "nunca fonte primária").

## 31.6 Nota de compatibilidade futura: World Model (ADD-0002)

Se o ADD-0002 (World Model) for aceito (Vol. VII, Cap. 27, seção 27.6), ele se encaixaria como um novo serviço consultado pelo Kernel Service (Planning Engine) e pelo eventual Goal Engine (ADD-0001) — análogo em posição ao Catalog Service, mas com fonte de dados externa e observação assíncrona (Vol. III, ADD-0002, seção 5), em vez de certificação humana. Nenhuma dependência existe hoje; esta nota é apenas para não exigir retrofitting completo de topologia se o anexo for aceito no futuro.

## 31.7 Casos de falha e recuperação

| Cenário | Tratamento |
|---|---|
| Kernel Service de um tenant fica indisponível | Missões daquele tenant ficam pausadas (não corrompidas — o Event Store preserva todo o estado); retomam do último evento publicado ao religar, consistente com `replay` (Vol. II, Cap. 10) |
| Catalog Service indisponível | Planning Engine não consegue resolver novas Capabilities/Playbooks — missões em andamento com plano já resolvido continuam executando; novas missões ficam em `Created` até o catálogo voltar |
| Event Store atinge limite de capacidade de uma partição | Estratégia de particionamento adicional por `tenantId` e por janela de tempo, com arquivamento frio para partições antigas (mantendo `replay` funcional via camada de arquivamento) |
| Processo de um Operador falha (crash) a meio de uma invocação | Tratado exatamente como timeout (Vol. III, Cap. 12) do ponto de vista do Execution Engine — o isolamento de processo garante que o crash não corrompe o Kernel Service |

## 31.8 Testes de aceitação

1. **AT-31.1:** Nenhuma réplica do Kernel Service pode processar o mesmo `tenantId` simultaneamente a outra réplica — verificável por teste de particionamento sob carga.
2. **AT-31.2:** Reinício completo do Kernel Service não deve causar perda ou duplicação de estado de nenhuma Missão em andamento — verificável via `replay` (Vol. II, Cap. 10, AT-10.1) após o reinício.
3. **AT-31.3:** Falha de um único processo de Operador nunca deve afetar a disponibilidade do Kernel Service ou de outros Operadores (teste de isolamento físico, complementando o isolamento lógico já testado no Vol. III, Cap. 12, AT-12.3).

## 31.9 KPIs deste componente

- **Disponibilidade por serviço físico** (Kernel, Catalog, Execution, Governance) — SLA de infraestrutura independente do SLA de missão (Vol. V, Cap. 20).
- **Tempo de recuperação após reinício do Kernel Service** — mede eficácia do `replay` em escala real, não apenas em teste unitário.
- **Custo de armazenamento do Event Store ao longo do tempo** — insumo direto para a estratégia de arquivamento (seção 31.7).

## 31.10 Status da Implementação

| Já existe | Precisa refatorar | Ainda não existe |
|---|---|---|
| — | — | Todos os serviços físicos mapeados; Event Store particionado; estratégia de arquivamento frio |

---

**Capítulo anterior:** [Capítulo 30 — Observability Engine](../07-governance/04-observability-engine.md)
**Próximo capítulo:** [Capítulo 32 — Estrutura de Diretórios](./02-directory-structure.md)
