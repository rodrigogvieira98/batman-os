# ADR-0018 — `GovernanceEngine.resolve` é superfície intra-ciclo

| Campo | Valor |
|---|---|
| **Status** | Accepted |
| **Volume** | VII — Governance |
| **Capítulos relacionados** | 27 (Governance Engine), seção 27.4 |
| **Princípios invocados** | Determinism First, Full Governance |
| **Data de referência** | 2026-09-18 |
| **Card** | `DIV-FIO-004` |

## Contexto

A seção 27.4 declara na interface do Governance Engine:

```ts
resolve(alertId: AlertId, resolution: string): void;
```

O card `DIV-FIO-004` nasceu com o diagnóstico *"existe e ninguém chama"*. A
remedição de 2026-09-17 mostrou que o diagnóstico estava incompleto, e que a
conclusão que ele sugeria — *código morto, remova* — estava errada. Três fatos,
todos medidos:

1. **A spec exige o método.** Ele está na interface do capítulo 27. Pela regra de
   ouro do projeto, divergência entre código e spec resolve a favor da spec:
   apagá-lo exigiria uma ADR, não um commit.

2. **`StatusAlerta.RESOLVED` é escrito por `resolve` e lido por ninguém.**
   Nenhum consumidor em `src/`, e a API HTTP não o expõe.

3. **⚠️ O método é inutilizável através de ciclos — e esse é o único uso real.**
   O `GovernanceEngine` guarda os alertas num `dict` de instância, sem nenhuma
   persistência: zero `json`, zero `Path`, zero `open()` no módulo inteiro. O
   monitor roda por `cron */5`, então **cada ciclo é um processo novo**: o engine
   nasce vazio e resolver um alerta de ciclo anterior levanta `AlertaDesconhecido`
   por construção.

O ponto que fecha o argumento: **resolução humana acontece depois, por
definição.** Ninguém resolve um alerta no mesmo processo de 5 minutos que o
levantou. Logo o único uso que o método teria é exatamente o que ele não suporta.

Isto é a armadilha nº 2 do projeto — contador em memória num processo que
reinicia — pela sexta vez. As cinco anteriores foram consertadas dando estado em
disco. Esta ADR existe porque aqui essa resposta estaria errada.

## Decisão

`resolve` é declarado **superfície intra-ciclo**: válido apenas dentro do
processo que levantou o alerta, e **não** é o mecanismo de resolução humana.

Resolução que atravessa ciclos continua sendo responsabilidade dos mecanismos
**já persistentes** — o journal de alertas e o registro de incidentes —, que já
carregam estado em disco e já são as fontes auditadas do que aconteceu.

O método permanece na implementação, conforme a spec exige, com a restrição
declarada aqui e presa por teste.

## Alternativas consideradas

1. **Dar persistência ao `GovernanceEngine`** — rejeitada. Criaria um **terceiro**
   arquivo de estado sobrepondo o journal e o registro de incidentes, os dois já
   persistentes e já cobrindo o caso. A reconciliação entre três fontes viraria
   dívida nova, e o sistema passaria a ter três respostas possíveis para "este
   alerta foi resolvido?". Responder a armadilha nº 2 acrescentando estado, em vez
   de nomear a fronteira, é o reflexo errado quando o estado já existe em outro
   lugar.

2. **Remover o método** — rejeitada. A seção 27.4 o declara na interface, e a
   regra de ouro faz a remoção exigir ADR do mesmo jeito. O resultado seria
   encolher a spec para caber num acidente de implementação, que é a direção
   proibida: a spec manda no código, não o contrário.

3. **Declará-lo superfície intra-ciclo, mantendo-o implementado** — **decisão
   aceita.**

## Consequências

**Positivas:**

- A fronteira passa a estar escrita onde alguém tropeçaria nela. Quem for ligar
  `resolve` descobre a restrição na spec e no teste, e não em produção, três
  semanas depois, num `AlertaDesconhecido` silencioso.
- Nenhum terceiro arquivo de estado. Journal e registro de incidentes seguem
  como as únicas fontes persistentes do que aconteceu com um alerta.
- A decisão é **presa por teste**, não só documentada:
  `tests/governance/test_resolve_nao_atravessa_o_processo.py` prende os quatro
  fatos, e dois deles são guardas de duas pontas — se alguém der persistência ao
  engine, ou passar a ler `RESOLVED`, os testes **reprovam** pedindo atualização
  do card, em vez de deixar esta ADR envelhecer calada.

**Negativas:**

- `StatusAlerta.RESOLVED` continua sendo escrito e não lido. É um valor sem
  consumidor — agora uma condição declarada, não um descuido, mas ainda um valor
  sem consumidor.
- Resolução humana através de ciclos não tem representação no Governance Engine.
  Quem precisar dela usa o journal ou o registro de incidentes, e essa indireção
  não é óbvia para quem lê só a interface.
- ⚠️ **A interface da seção 27.4 continua declarando `resolve` sem dizer seu
  tempo de vida.** Esta ADR fornece o que a interface omite, então ler a interface
  sozinha ainda engana. A correção da assinatura na spec — anotar o escopo
  intra-ciclo no próprio capítulo 27 — fica como trabalho derivado desta decisão.

## Conformidade com princípios

| Princípio | Conformidade |
|---|---|
| Determinism First | ✅ Nenhum caminho novo de mudança de comportamento, e nenhuma quarta fonte de verdade sobre o estado de um alerta |
| Full Governance | ✅ Resolução que atravessa ciclos continua passando pelos mecanismos persistentes e auditados, não por um estado de processo |

## Revisão futura

Esta ADR deve ser revisitada se a API HTTP passar a expor resolução de alerta a
um humano fora do ciclo — o caso que hoje não existe e que motivaria o método.

⚠️ E a revisão não deve começar por "dar persistência ao engine". O caminho
provável continua sendo o oposto: **dar a resolução ao journal**, que já persiste,
em vez de dar persistência a quem não tem. A alternativa 1 foi rejeitada por um
motivo que não expira com o tempo — duas fontes para o mesmo fato não melhoram
por ganharem uma terceira.

---

**Voltar:** [Capítulo 27 — Governance Engine](../01-governance-engine.md)
