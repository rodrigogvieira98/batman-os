"""Vol. I, Cap. 4 — vocabulario oficial do Kernel: IDs e tipos compartilhados.

Fonte da verdade: docs/spec/01-foundation/04-terminology.md

Regra do Cap.4 (nao negociavel): estes termos nao podem ser usados com outro
sentido em nenhum outro modulo. Nomenclatura do Batman atual (agente, ledger,
sweep, patrol, Alfred, Robin, IDs de regra) e preservada ao redor deste nucleo,
nunca substituindo-o — ver README.md, secao "Convencao de nomenclatura".
"""

from __future__ import annotations

import secrets
import time
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, NewType

from pydantic import BaseModel, Field

Timestamp = datetime


def agora() -> Timestamp:
    """Instante atual em UTC — default de todo campo Timestamp do Kernel."""
    return datetime.now(UTC)


def novo_uuid7() -> str:
    """UUID v7 (ordenavel por tempo de criacao) — exigido pelo campo `id` de
    Mission (Vol.II Cap.6, secao 6.2: "UUID v7 — ordenavel por tempo de criacao")."""
    ts_ms = time.time_ns() // 1_000_000
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    high = ((ts_ms & 0xFFFFFFFFFFFF) << 16) | (0x7 << 12) | rand_a
    low = (0b10 << 62) | rand_b
    return str(uuid.UUID(int=(high << 64) | low))


def novo_ulid_like() -> str:
    """Identificador ordenavel para EventId (Vol.II Cap.10 pede ULID). Reaproveita
    o gerador de UUID7 — tambem ordenavel por tempo — ate uma biblioteca de ULID
    real ser adotada (Volume VIII, Infrastructure, ainda nao escrito)."""
    return novo_uuid7()


# IDs — NewType sobre str para o mypy impedir troca acidental entre eles
# (ex.: passar um CapabilityId onde se espera um SkillId nao deve compilar).
MissionId = NewType("MissionId", str)
MissionTypeId = NewType("MissionTypeId", str)
PlanId = NewType("PlanId", str)
StepId = NewType("StepId", str)
DecisionId = NewType("DecisionId", str)
DecisionPointId = NewType("DecisionPointId", str)
EventId = NewType("EventId", str)
WorkflowRunId = NewType("WorkflowRunId", str)
CapabilityId = NewType("CapabilityId", str)
SkillId = NewType("SkillId", str)
ToolId = NewType("ToolId", str)
OperatorId = NewType("OperatorId", str)
PlaybookId = NewType("PlaybookId", str)
TenantId = NewType("TenantId", str)
RuleId = NewType("RuleId", str)
AdrId = NewType("AdrId", str)
EvidenceId = NewType("EvidenceId", str)
RecordId = NewType("RecordId", str)  # OperationalRecord (Vol.III Cap.13)
HumanReviewRef = NewType("HumanReviewRef", str)  # Vol.VII Cap.28, referenciado desde Vol.V Cap.21
ProposalId = NewType("ProposalId", str)  # Vol.VI Cap.25 (WorkflowEvolutionProposal)
AlertId = NewType("AlertId", str)  # Vol.VII Cap.27 (GovernanceAlert)


class KnowledgeAssetKind(StrEnum):
    """Vol.I Cap.4, secao 4.7 — tipos de Knowledge Asset (guarda-chuva do
    Principio 7, Learn Forever)."""

    REGRA = "regra"
    TESTE = "teste"
    WORKFLOW = "workflow"
    CAPABILITY = "capability"
    SKILL = "skill"
    EVIDENCIA = "evidencia"
    ADR = "adr"
    PLAYBOOK = "playbook"


class KnowledgeAssetRef(BaseModel):
    """Referencia opaca a um Knowledge Asset — usada por `Mission.
    knowledge_assets_produced` (Vol.II Cap.6) e pelo Knowledge Graph
    (Vol.VI Cap.23, `KnowledgeNode`)."""

    tipo: KnowledgeAssetKind
    ref_id: str


class Medicao(BaseModel):
    """O NUMERO que sustenta um alerta, estruturado — SAIDA-003.

    ⚠️ **Existe porque o numero so chegava como TEXTO.** Medido em producao em
    2026-09-08: os 11 alertas de `AlertRule:observe.cpu:*` tinham
    `measured_value=None`, `threshold=None` e `unit=None`, e o limiar aparecia
    apenas dentro de `Evidence.evidencias`, como a frase `"threshold=90.0"`.
    Nenhum dos 11 podia ser reexaminado depois: **o alerta nao guardava o que
    mediu**, e por isso a pergunta do DEV -- *"meus alertas de infra sao
    reais?"* -- nao tinha resposta possivel.

    ⚠️ **`valor=None` significa NAO MEDIDO, e nunca pode virar `0.0`.** Zero e
    uma leitura saudavel; ausencia e cegueira. Colapsar as duas faria a metrica
    cega passar por metrica boa -- exatamente o defeito que
    `EstadoColeta`/`ResultadoColeta` existem para impedir noutro eixo.

    ⚠️ **`janela_s` e `capacidade` nao sao enfeite.** Medido em 2026-09-08:
    `psutil.cpu_percent(interval=1.0)` observa **1 segundo a cada 300**, e numa
    maquina parada amostras de 1 s variaram de 2,0 % a 6,3 %. Um alerta de
    "CPU 92 %" descreve um segundo, nao o ciclo -- e sem a janela registrada
    ninguem consegue distinguir pico de saturacao sustentada. `capacidade` diz
    de que todo o percentual fala: 85 % de 4 vCPUs e outra coisa que 85 % de um
    nucleo.
    """

    #: Identificador da base de metrica, ex.: `observe.cpu`.
    base: str
    #: O valor medido. `None` = nao medido. **Nunca preencher com zero.**
    valor: float | None
    #: `%`, `ms`, `req/s`... Sem unidade, o numero nao significa nada.
    unidade: str
    #: Quanto tempo a amostra observou. `0.0` = instantaneo (leitura pontual).
    janela_s: float = 0.0
    #: Capacidade de referencia, em texto legivel: "4 vCPUs", "15,6 GiB".
    capacidade: str = ""
    #: Quantas observacoes sustentam o valor. Para taxa, e o DENOMINADOR.
    #:
    #: ⚠️ Ausencia de requisicoes nao equivale a recuperacao: uma taxa de erro
    #: calculada sobre zero requisicoes daria 0 % e cruzaria qualquer limiar de
    #: recuperacao para baixo, declarando "resolvido" justamente quando o
    #: servico parou de receber trafego.
    amostras: int | None = None
    #: Pico instantaneo observado dentro da janela, quando existir — `DIV-CPU-001`.
    #:
    #: ⚠️ Nao e o valor que decide o alerta, e existe para NAO se perder. Era ele
    #: que decidia ate 16/09, e foi o que publicou "Servidor sem folga de
    #: recursos" 20 vezes numa maquina que o `sar` media 96 % ociosa: descrevia 1
    #: segundo de arranque de cron e era lido como saturacao sustentada.
    #:
    #: Guardado ao lado da media, ele deixa de mentir e passa a informar --
    #: rajada e sinal legitimo (`DIV-REBOOT-001` depende dele), desde que
    #: apresentada como rajada.
    pico: float | None = None
    pico_janela_s: float = 0.0
    #: A coleta foi boa? Nome da fonte e estado, no vocabulario do Apendice A.
    fonte: str = ""
    coleta_ok: bool = True
    #: Limiares vigentes quando a medicao ocorreu, para o registro se sustentar
    #: sozinho depois de alguem mexer nas constantes.
    limiar_warning: float | None = None
    limiar_critical: float | None = None
    limiar_recuperacao: float | None = None
    #: O limiar veio do AMBIENTE, e nao do codigo.
    #:
    #: ⚠️ Vai para o alerta porque limiar sobreposto muda o que o sistema
    #: AFIRMA sobre seguranca. Mudanca silenciosa de criterio e o defeito
    #: que esta frente existe para eliminar: quem le precisa saber que o
    #: numero nao e o do codigo.
    limiar_do_ambiente: bool = False

    @property
    def valida(self) -> bool:
        """Ciclo VALIDO: mediu de verdade.

        ⚠️ E o que a contagem de recuperacao consome. Decisao do DEV em
        2026-09-08: *"leitura ausente ou com erro nunca conta como
        recuperacao"*, e ciclo cego **zera** o contador em vez de pausa-lo --
        pausar permitiria juntar leituras saudaveis separadas por um periodo
        desconhecido.
        """
        return self.coleta_ok and self.valor is not None


class RespostaAvaliada(BaseModel):
    """O que a resposta automatica DECIDIU num ciclo — em campo, nunca em texto.

    ⚠️ **Existe para que o veredito fique FORA da assinatura de dedupe.** A
    decisao muda entre ciclos por motivos legitimos: o teto de acoes do ciclo,
    reincidencia que expira, allowlist que ganha um IP. Se `bloquearia` ou
    `motivo` vivessem nas linhas estaveis do alerta, cada uma dessas variacoes
    reabriria o mesmo incidente — que e exatamente o flood que este projeto ja
    removeu duas vezes, voltando por uma porta nova.

    Entao o alerta carrega UMA linha estavel dizendo que a resposta foi
    avaliada, e o veredito vive aqui e no `historico`. Identidade do incidente
    preservada; auditoria completa.

    ⚠️ Mudanca EFETIVA de acao — quando houver executor de verdade — nao e isto.
    Sera evento proprio e auditado, nao um campo que mudou de valor em silencio.
    """

    #: IP avaliado. Tem de ser o MESMO que a regra apresenta no alerta.
    ip: str
    #: O veredito. Fora da assinatura, de proposito.
    bloquearia: bool
    #: Por que — inclui a recusa por teto, por allowlist e por IP reservado.
    motivo: str
    #: `shadow`, `assistido` ou `autonomo`. Hoje sempre `shadow`.
    modo: str
    #: Duracao que o bloqueio teria. `None` quando nao bloquearia.
    ttl_s: int | None = None
    #: Se seria reincidencia (janela maior).
    reincidente: bool = False
    #: Quem executaria. No shadow, ninguem.
    executor: str = ""
    #: Recusa por teto de acoes do ciclo — o caso que mais varia entre ciclos.
    recusa_por_teto: bool = False


class DivergenciaDeAlvo(BaseModel):
    """A decisao avaliada nao era sobre o alvo que este alerta apresenta.

    ⚠️ **Existe para que a guarda de identidade nao seja silenciosa.** A guarda
    faz a coisa certa — nao associa a decisao ao alvo errado — mas recusar em
    silencio produz exatamente a cegueira que este programa existe para
    eliminar: ninguem descobre que a avaliacao esta mirando outro endereco.

    O caso real e o ramo de *auth bypass*: ele apresenta o IP do sucesso
    anomalo, enquanto o watcher avalia `auth.ip_top`. Quando os dois divergem, a
    decisao nao vale para este alerta — e o registro tem de dizer isso.
    """

    #: O alvo que o alerta apresenta ao operador.
    alvo_apresentado: str
    #: O alvo sobre o qual a decisao foi de fato calculada.
    alvo_avaliado: str
    #: O protocolo do incidente, quando ha um.
    protocolo: str = ""
    #: Por que os dois divergiram, em uma linha.
    contexto: str = ""


class Evidence(BaseModel):
    """Vol.I Cap.3, secao 3.4 — Principio 3 (Evidence First). Toda Decision
    (Vol.II Cap.8) carrega evidencia rastreavel; nunca pode existir uma decisao
    com evidencia vazia (AT-8.1)."""

    origem: str
    evidencias: list[str] = Field(default_factory=list)
    confianca: float | None = None
    historico: list[str] = Field(default_factory=list)
    #: Identidade ESTAVEL do assunto, para ligar abertura e fechamento.
    #:
    #: ⚠️ Existe porque `incident_key` NAO liga os dois lados quando a fonte
    #: muda: a abertura de CPU e `infra-saturation|...|AlertRule:observe.cpu:crit`
    #: e o fechamento e `feature|...|observe:metrica-recuperada` -- chaves
    #: diferentes para o mesmo assunto. Sem um elo explicito, a recuperacao nao
    #: teria como encontrar a mensagem que precisa EDITAR.
    #:
    #: Vazio significa "sem elo", e o efeito e honesto: nao ha edicao, e o
    #: fechamento seguiria por outro caminho. Nunca inventar o elo por
    #: heuristica sobre o texto -- seria o registro perseguindo a grafia outra
    #: vez.
    assunto: str = ""
    #: A medicao estruturada que sustenta esta evidencia, quando ha uma.
    #:
    #: ⚠️ Vive aqui, e nao no `GovernanceAlert`, porque um alerta pode carregar
    #: varias evidencias de origens diferentes -- e o numero pertence a origem
    #: que o mediu, nao ao alerta inteiro.
    medicao: Medicao | None = None
    #: A decisao de resposta automatica que esta origem avaliou, quando avaliou.
    #:
    #: ⚠️ Estruturada de proposito: o journal precisa auditar o veredito de cada
    #: ciclo, e o throttle do Discord nao pode apagar essa auditoria. Alerta
    #: suprimido continua sendo alerta registrado.
    resposta: RespostaAvaliada | None = None
    #: Registrada quando a decisao avaliada NAO era sobre o alvo deste
    #: alerta. Presenca dela significa: houve avaliacao, e ela nao se
    #: aplica aqui. Diferente de `resposta=None`, que significa "nao houve
    #: avaliacao" — colapsar os dois esconderia a divergência.
    divergencia: DivergenciaDeAlvo | None = None


class CognitiveDebtFlag(StrEnum):
    """Vol.I Cap.4, secao 4.9.1 — dado bruto do KPI de Cognitive Debt. So e
    atribuido pelo Mission Runtime (Vol.II Cap.6, secao 6.2, nota de design),
    mas consumido tambem pelo Learning Engine (Vol.VI Cap.26) — colocado
    aqui pelo mesmo motivo dos demais tipos desta secao (evitar import
    circular learning <-> kernel, Vol.VIII Cap.32 secao 32.3)."""

    AUTONOMOUS = "autonomous"
    HUMAN = "human"
    LLM = "llm"


class Criticidade(StrEnum):
    """Vol.V Cap.20, secao 20.2/20.3 — `MissionTypeDefinition.criticality`.
    Referenciada de forma cruzada pelo Decision Engine (Vol.II Cap.8, secao
    20.3: `critical` nunca escala a LLM sem humano intermediario) e pelo
    Scheduler (Vol.II Cap.10: prioridade base) antes do Volume V ter seu
    proprio modulo — colocada aqui pelo mesmo motivo dos demais tipos desta
    secao (evitar import circular kernel <-> workflow, Vol.VIII Cap.32
    secao 32.3: `workflow` depende de `kernel`, nunca o contrario)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Reversibilidade(StrEnum):
    """Vol.II Cap.8, secao 8.3 — `EscalationPolicy.reversibility`. Decisoes
    irreversiveis nunca vao direto a LLM sem escalonamento humano intermediario
    (AT-8.3)."""

    REVERSIVEL = "reversible"
    IRREVERSIVEL = "irreversible"


# Os tipos abaixo (CapabilityRef, DecisionOption, EscalationPolicy,
# RecoveryStrategy) sao definidos formalmente em capitulos posteriores
# (Vol.III Cap.11, Vol.II Cap.8, Vol.II Cap.9) mas sao referenciados de forma
# cruzada por Cap.7 (Planning Engine) e Cap.8/9 antes de cada um ter seu
# proprio modulo. Colocados aqui, na Foundation, especificamente para evitar
# import circular entre kernel/planning_engine.py, kernel/decision_engine.py
# e kernel/workflow_engine.py — nao e uma reinterpretacao do glossario do
# Cap.4, e sim uma escolha de organizacao de modulos.


class CapabilityRef(BaseModel):
    """Vol.II Cap.7 (`PlanStep.capability`) / Vol.III Cap.11, secao 11.3.1 —
    um `ExecutionPlan` ja gerado referencia uma versao especifica de
    Capability, nunca "a mais recente" implicitamente (regra de ouro do
    capitulo, o que garante `planHash` estavel mesmo se o catalogo evoluir)."""

    capability_id: CapabilityId
    versao: str


class DecisionOption(BaseModel):
    """Vol.II Cap.7 (`DecisionPoint.options`) / Cap.8 (`Decision.chosenOption`)
    — uma alternativa concreta que um DecisionPoint pode resolver."""

    id: str
    descricao: str
    payload: dict[str, Any] = Field(default_factory=dict)


class EscalationPolicy(BaseModel):
    """Vol.II Cap.8, secao 8.3 — configuravel por tipo de missao/decisao,
    nunca hardcoded no Kernel."""

    confidence_threshold: float
    preferred_escalation: Literal["human", "llm"]
    max_llm_retries: int
    reversibility: Reversibilidade


class TipoRecoveryStrategy(StrEnum):
    """Vol.II Cap.9, secao 9.5; `FALLBACK_CAPABILITY` adicionado no Vol.V
    Cap.22, secao 22.2 (estende, nunca substitui, o conjunto original)."""

    RETRY = "retry"
    COMPENSATE = "compensate"
    SKIP_IF_OPTIONAL = "skip-if-optional"
    ESCALATE = "escalate"
    FALLBACK_CAPABILITY = "fallback-capability"


class ImpactoDegradacao(StrEnum):
    """Vol.V Cap.22, secao 22.4 — `DegradationRecord.impact`."""

    COSMETIC = "cosmetic"
    REDUCED_FUNCTIONALITY = "reduced-functionality"
    REQUIRES_FOLLOW_UP = "requires-follow-up"


class OperatorRef(BaseModel):
    """Vol.III Cap.12 (`ExecutionEngine.invoke`) / Vol.IV Cap.15 — referencia
    opaca a um Operador. Colocada aqui pelo mesmo motivo dos demais tipos
    desta secao: Cap.12 (Runtime) precisa dela antes de Cap.15 (Capabilities)
    ter seu proprio modulo."""

    operator_id: OperatorId


class SkillRef(BaseModel):
    """Vol.III Cap.11 (`CapabilityDefinition.requiredSkills`) / Vol.IV Cap.17
    — referencia a uma Skill que uma Capability usa internamente. Colocada
    aqui pelo mesmo motivo dos demais tipos desta secao: Cap.11 (Runtime) e
    Cap.17 (Capabilities) se referenciam mutuamente."""

    skill_id: SkillId


class RecoveryStrategy(BaseModel):
    """Vol.II Cap.9, secao 9.5 — uniao discriminada por `tipo`, representada
    aqui como um unico modelo com campos opcionais por variante (mais simples
    de validar em Pydantic que uma uniao discriminada completa). Campos
    irrelevantes ao `tipo` corrente ficam `None`."""

    tipo: TipoRecoveryStrategy
    max_tentativas: int | None = None  # retry
    backoff: Literal["fixed", "exponential"] | None = None  # retry
    compensation_step_id: StepId | None = None  # compensate
    alternative_capability: CapabilityRef | None = None  # fallback-capability (Vol.V Cap.22)
    escalation_policy: EscalationPolicy | None = None  # escalate


class DegradationRecord(BaseModel):
    """Vol.V Cap.22, secao 22.4 — referenciada por `Mission` (Vol.II Cap.6)
    antes do Volume V ter modulo proprio; mesmo motivo de `Criticidade`
    (evitar import circular kernel <-> workflow, Vol.VIII Cap.32 secao 32.3)."""

    step_id: StepId
    exhausted_chain: list[RecoveryStrategy] = Field(default_factory=list)
    impact: ImpactoDegradacao


class DateRange(BaseModel):
    """Vol.VI Cap.25 (`EvolutionEvidence.observationWindow`) / Vol.VII
    Cap.29 (`LLMUsageAudit.period`) — usada por mais de um capitulo,
    movida para a Foundation para evitar duplicacao (mesmo motivo dos
    demais tipos desta secao)."""

    inicio: Timestamp
    fim: Timestamp
