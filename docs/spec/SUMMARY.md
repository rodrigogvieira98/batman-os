# Sumário — Batman OS Engineering Specification

## Volume I — Foundation ✅ (este commit)

1. [O Problema](./01-foundation/01-introduction.md)
2. [A Filosofia Batman](./01-foundation/02-philosophy.md)
3. [Princípios Fundamentais](./01-foundation/03-principles.md)
4. [Definições Oficiais (Glossário)](./01-foundation/04-terminology.md)
   - [ADR-0001 — Batman será um Sistema Cognitivo Determinístico](./01-foundation/ADR/ADR-0001-deterministic-cognitive-system.md)

## Volume II — Kernel Architecture ✅ (este commit)

5. [Kernel — visão geral e responsabilidades](./02-kernel/01-kernel-overview.md)
6. [Mission Runtime — ciclo de vida de uma Missão](./02-kernel/02-mission-runtime.md)
7. [Planning Engine — planejamento determinístico](./02-kernel/03-planning-engine.md)
8. [Decision Engine — tomada de decisão baseada em conhecimento](./02-kernel/04-decision-engine.md)
9. [Workflow Engine](./02-kernel/05-workflow-engine.md)
10. [Event Bus & Scheduler](./02-kernel/06-event-bus-scheduler.md)
    - [ADR-0002 — Separação estrita entre Planning, Decision e Execution](./02-kernel/ADR/ADR-0002-separation-planning-decision-execution.md)
    - [ADR-0003 — Event Sourcing como padrão de auditabilidade](./02-kernel/ADR/ADR-0003-event-sourcing.md)

## Volume III — Runtime ✅ (este commit)

11. [Capability Engine](./03-runtime/01-capability-engine.md)
12. [Execution Engine](./03-runtime/02-execution-engine.md)
13. [Operational Memory](./03-runtime/03-operational-memory.md)
14. [Concorrência e Isolamento de Missões](./03-runtime/04-concurrency-isolation.md)
    - [ADR-0004 — Operational Memory não é fonte de verdade comportamental](./03-runtime/ADR/ADR-0004-operational-memory-vs-knowledge.md)
    - [ADR-0005 — Isolamento multi-tenant como propriedade estrutural](./03-runtime/ADR/ADR-0005-multitenant-isolation.md)

## Volume IV — Capabilities ✅ (este commit)

15. [O que é um Operador](./04-capabilities/01-operator.md)
16. [Capabilities — contrato e ciclo de vida](./04-capabilities/02-capability-contract.md)
17. [Skills](./04-capabilities/03-skills.md)
18. [Ferramentas (Tools)](./04-capabilities/04-tools.md)
19. [Cooperação entre Operadores](./04-capabilities/05-cooperation.md)
    - [ADR-0006 — Menor privilégio e sandboxing obrigatório](./04-capabilities/ADR/ADR-0006-operator-least-privilege.md)
    - [ADR-0007 — Cooperação mediada como único padrão de comunicação](./04-capabilities/ADR/ADR-0007-mediated-cooperation.md)

## Volume V — Workflow Engine ✅ (este commit)

20. [Missões — modelagem formal](./05-workflow/01-missions-formal-model.md)
21. [Playbooks](./05-workflow/02-playbooks.md)
22. [Estratégias de recuperação e fallback](./05-workflow/03-recovery-fallback-strategies.md)
    - [ADR-0008 — Resolução determinística de conflito entre Playbooks](./05-workflow/ADR/ADR-0008-playbook-conflict-resolution.md)
    - [ADR-0009 — Sucesso parcial como estado de primeira classe](./05-workflow/ADR/ADR-0009-partial-success-state.md)

## Volume VI — Learning Engine ✅ (este commit)

23. [Knowledge Graph](./06-learning/01-knowledge-graph.md)
24. [Rule Evolution](./06-learning/02-rule-evolution.md)
25. [Workflow Evolution](./06-learning/03-workflow-evolution.md)
26. [Operational Learning](./06-learning/04-operational-learning.md)
    - [ADR-0010 — Knowledge Graph como projeção derivada, nunca fonte primária](./06-learning/ADR/ADR-0010-knowledge-graph-derived-projection.md)
    - [ADR-0011 — Shadow mode obrigatório antes da ativação de qualquer regra](./06-learning/ADR/ADR-0011-shadow-mode-mandatory.md)

## Volume VII — Governance ✅ (este commit)

27. [Governance Engine](./07-governance/01-governance-engine.md)
28. [Human Review](./07-governance/02-human-review.md)
29. [LLM Escalation](./07-governance/03-llm-escalation.md)
30. [Observability Engine](./07-governance/04-observability-engine.md)
    - [ADR-0012 — Governance Engine sem autoridade executiva direta](./07-governance/ADR/ADR-0012-governance-no-direct-authority.md)
    - [ADR-0013 — Política de LLM Escalation como artefato único e revisável](./07-governance/ADR/ADR-0013-llm-policy-single-artifact.md)
    - [ADR-0018 — `GovernanceEngine.resolve` é superfície intra-ciclo](./07-governance/ADR/ADR-0018-resolve-superficie-intra-ciclo.md)

## Volume VIII — Infrastructure ✅ (este commit)

31. [Arquitetura física](./08-infrastructure/01-physical-architecture.md)
32. [Estrutura de diretórios](./08-infrastructure/02-directory-structure.md)
33. [Segurança e isolamento](./08-infrastructure/03-security-isolation.md)
    - [ADR-0014 — Defesa em profundidade para isolamento de tenant](./08-infrastructure/ADR/ADR-0014-defense-in-depth-tenant-isolation.md)
    - [ADR-0015 — Verificação de integridade de artefato como bloqueio obrigatório](./08-infrastructure/ADR/ADR-0015-artifact-integrity-verification.md)

## Volume IX — Reference Implementation ✅ (este commit)

34. [Implementação de referência do Batman OS](./09-reference-implementation/01-reference-implementation.md)
35. [Casos de uso ponta a ponta](./09-reference-implementation/02-end-to-end-use-cases.md)
    - [ADR-0016 — Faseamento reduz escopo, nunca disciplina](./09-reference-implementation/ADR/ADR-0016-phasing-scope-not-discipline.md)
    - [ADR-0017 — Implementação de referência constrói apenas a especificação aceita](./09-reference-implementation/ADR/ADR-0017-reference-implementation-excludes-unaccepted-addenda.md)

## Volume X — Appendices ✅ (este commit)

36. [Glossário consolidado](./10-appendices/01-consolidated-glossary.md)
37. [Índice de ADRs e Anexos](./10-appendices/02-adr-addenda-index.md)
38. [Métricas e KPIs consolidados (Cognitive Debt, Patrimônio Cognitivo)](./10-appendices/03-consolidated-metrics.md)
39. [Roadmap de evolução](./10-appendices/04-evolution-roadmap.md)

---

**Legenda:** ✅ completo · 🟡 em progresso · ⚪ não iniciado

**Obra completa: 39 capítulos, 10 volumes, 17 ADRs e 6 Anexos.**
