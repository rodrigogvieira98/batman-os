"""Vol. VII, Cap. 28 — Human Review.

Formaliza o processo referenciado como "checkpoint obrigatório" em quase
todo mecanismo de promoção de conhecimento da obra (ADR-0004, Vol.III;
Rule/Workflow Evolution, Vol.VI; certificação de Capability irreversível,
Vol.IV Cap.16; aceite de Anexos, Cap.27) sem, até este capítulo, ter
especificação própria — evitando que "revisão humana" vire, na prática,
um clique sem critério.

Nota de implementação: a especificação define `HumanReviewRef` como uma
estrutura rica (`{requestId, decidedBy, decidedAt, rationale}`), mas todos
os capítulos anteriores (Cap.21/24/25/27) já usam `HumanReviewRef` como
referência opaca (`NewType(str)`, `foundation/types.py`) — mesmo padrão de
`CapabilityRef`/`SkillRef`/`OperatorRef` (uma referência enxuta, não o
registro completo embutido). Este módulo preserva essa convenção: a
estrutura rica vive aqui como `HumanReviewDecision`; `emitir_referencia()`
formaliza o ponto de conversão para o `HumanReviewRef` opaco que os
demais capítulos já consomem (gap mecânico de tipagem, não decisão nova).

Fonte da verdade: docs/spec/07-governance/02-human-review.md
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, NewType

from pydantic import BaseModel, Field

from batman_os.foundation.types import (
    Evidence,
    HumanReviewRef,
    TenantId,
    Timestamp,
    agora,
    novo_uuid7,
)
from batman_os.governance.governance_engine import (
    FonteAlerta,
    GovernanceAlert,
    GovernanceEngine,
    SeveridadeAlerta,
)

ReviewRequestId = NewType("ReviewRequestId", str)
ReviewerId = NewType("ReviewerId", str)

Decisao = Literal["approved", "rejected", "changes-requested"]


class ReviewerRole(StrEnum):
    """Vol.VII Cap.28, secao 28.2/28.3."""

    DOMAIN_EXPERT = "domain-expert"
    SECURITY_REVIEWER = "security-reviewer"
    ARCHITECTURE_REVIEWER = "architecture-reviewer"
    GOVERNANCE_LEAD = "governance-lead"


class TipoRevisao(StrEnum):
    """Vol.VII Cap.28, secao 28.2 — `HumanReviewRequest.kind`."""

    RULE_PROMOTION = "rule-promotion"
    PLAYBOOK_CERTIFICATION = "playbook-certification"
    CAPABILITY_IRREVERSIBLE_APPROVAL = "capability-irreversible-approval"
    ADDENDUM_ACCEPTANCE = "addendum-acceptance"
    GOVERNANCE_ALERT_RESOLUTION = "governance-alert-resolution"


class StatusRevisao(StrEnum):
    """Vol.VII Cap.28, secao 28.2."""

    PENDING = "pending"
    IN_REVIEW = "in-review"
    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes-requested"


# Vol.VII Cap.28, secao 28.3 — tabela de autoridade por ReviewerRole.
# governance-lead tambem pode sobrescrever qualquer outro papel (secao
# 28.3, ultima linha), representado aqui incluindo-o em todos os conjuntos.
_AUTORIZACAO: dict[TipoRevisao, set[ReviewerRole]] = {
    TipoRevisao.RULE_PROMOTION: {ReviewerRole.DOMAIN_EXPERT, ReviewerRole.GOVERNANCE_LEAD},
    TipoRevisao.PLAYBOOK_CERTIFICATION: {ReviewerRole.DOMAIN_EXPERT, ReviewerRole.GOVERNANCE_LEAD},
    TipoRevisao.CAPABILITY_IRREVERSIBLE_APPROVAL: {
        ReviewerRole.SECURITY_REVIEWER,
        ReviewerRole.GOVERNANCE_LEAD,
    },
    TipoRevisao.ADDENDUM_ACCEPTANCE: {
        ReviewerRole.ARCHITECTURE_REVIEWER,
        ReviewerRole.GOVERNANCE_LEAD,
    },
    TipoRevisao.GOVERNANCE_ALERT_RESOLUTION: {ReviewerRole.GOVERNANCE_LEAD},
}


def papeis_autorizados(kind: TipoRevisao) -> set[ReviewerRole]:
    """Vol.VII Cap.28, secao 28.3."""
    return _AUTORIZACAO[kind]


class HumanReviewRequest(BaseModel):
    """Vol.VII Cap.28, secao 28.2.

    `subject_ref` simplifica a união `KnowledgeNode | GovernanceAlert["id"]
    | AddendumRef` da especificação para uma referência opaca (`str`) — o
    que está sendo revisado varia por `kind`, e nenhuma decisão deste
    capítulo depende de inspecionar a estrutura interna do alvo.

    `tenant_id` (Fase 5 do roadmap de plataforma, `.claude/plans/
    peaceful-wondering-hearth.md`, Estagio 5.2) — obrigatório, sem
    default (mesma convenção ADR-0005/Fase 4 Estágio 4.1): retrofit que
    permite `solicitacoes_do_tenant()` filtrar o backlog de revisão por
    tenant. `verificar_sla_e_alarmar()` continua deliberadamente
    cross-tenant — ver docstring lá."""

    id: ReviewRequestId = Field(default_factory=lambda: ReviewRequestId(novo_uuid7()))
    tenant_id: TenantId
    kind: TipoRevisao
    subject_ref: str
    evidence: list[Evidence]
    required_reviewer_role: ReviewerRole
    sla_deadline: Timestamp
    status: StatusRevisao = StatusRevisao.PENDING


class RationaleObrigatorio(Exception):
    """Vol.VII Cap.28, secao 28.2 (AT-28.1) — nenhuma `HumanReviewDecision`
    pode ser gravada sem `rationale` preenchido, mesmo em `approved`: uma
    aprovação sem justificativa é epistemicamente equivalente a nenhuma
    revisão ter ocorrido."""


class PapelDeRevisorNaoAutorizado(Exception):
    """Vol.VII Cap.28, secao 28.3 (AT-28.2) — só decide quem tem o
    `ReviewerRole` exigido pela solicitação, nunca "qualquer humano
    disponível"."""


class HumanReviewDecision(BaseModel):
    """Vol.VII Cap.28, secao 28.2.

    `rationale` validado no construtor (não apenas documentado): string
    vazia ou só espaços é tratada como ausente (AT-28.1)."""

    request_id: ReviewRequestId
    reviewer_id: ReviewerId
    reviewer_role: ReviewerRole
    decision: Decisao
    rationale: str
    decided_at: Timestamp = Field(default_factory=agora)

    def model_post_init(self, __context: object) -> None:
        if not self.rationale.strip():
            raise RationaleObrigatorio(
                f"HumanReviewDecision para '{self.request_id}' nao pode ser gravada "
                "sem rationale preenchido, mesmo em 'approved' (AT-28.1)"
            )


def decidir(solicitacao: HumanReviewRequest, decisao: HumanReviewDecision) -> HumanReviewRequest:
    """Vol.VII Cap.28, secao 28.4 (AT-28.2) — aplica a `HumanReviewDecision`
    à `HumanReviewRequest`, validando que o `reviewer_role` corresponde ao
    `required_reviewer_role` antes de transicionar o status."""
    if decisao.reviewer_role != solicitacao.required_reviewer_role:
        raise PapelDeRevisorNaoAutorizado(
            f"Solicitacao '{solicitacao.id}' exige '{solicitacao.required_reviewer_role.value}', "
            f"mas foi decidida por '{decisao.reviewer_role.value}'"
        )

    novo_status = {
        "approved": StatusRevisao.APPROVED,
        "rejected": StatusRevisao.REJECTED,
        "changes-requested": StatusRevisao.CHANGES_REQUESTED,
    }[decisao.decision]
    return solicitacao.model_copy(update={"status": novo_status})


def reabrir_como_nova_solicitacao(
    original: HumanReviewRequest, evidencia_adicional: list[Evidence] | None = None
) -> HumanReviewRequest:
    """Vol.VII Cap.28, secao 28.4, nota crítica — `changes-requested`
    reabre o ciclo de submissão; não é rejeição definitiva nem aprovação
    condicional. Gera uma NOVA solicitação (novo `id`), preservando
    `kind`/`subject_ref`/`required_reviewer_role` — a cadeia de
    proveniência do artefato revisado (RulePromotion/PlaybookProvenance)
    continua responsabilidade do capítulo de origem, não deste módulo."""
    return HumanReviewRequest(
        tenant_id=original.tenant_id,
        kind=original.kind,
        subject_ref=original.subject_ref,
        evidence=original.evidence + (evidencia_adicional or []),
        required_reviewer_role=original.required_reviewer_role,
        sla_deadline=original.sla_deadline,
        status=StatusRevisao.PENDING,
    )


def emitir_referencia(decisao: HumanReviewDecision) -> HumanReviewRef:
    """Vol.VII Cap.28, secao 28.5 (AT-28.3) — converte uma
    `HumanReviewDecision` aprovada na referência opaca `HumanReviewRef` que
    `RulePromotion` (Cap.24), `PlaybookProvenance` (Cap.21) e
    `AddendumRecord` (Cap.27) já esperam. Só emite para decisões
    `approved` — `rejected`/`changes-requested` nunca geram referência
    válida para propagar a um artefato."""
    if decisao.decision != "approved":
        raise ValueError(
            f"Decisao '{decisao.request_id}' nao esta 'approved' "
            f"(esta '{decisao.decision}'), nao gera HumanReviewRef"
        )
    return HumanReviewRef(f"{decisao.request_id}:{decisao.reviewer_id}")


def solicitacoes_do_tenant(
    solicitacoes: list[HumanReviewRequest], tenant_id: TenantId
) -> list[HumanReviewRequest]:
    """Fase 5 do roadmap de plataforma (isolamento multi-tenant,
    `.claude/plans/peaceful-wondering-hearth.md`, Estagio 5.2) — filtro
    simples, mesmo padrão de `KnowledgeGraph.nos_por_tenant()`/
    `OperationalMemory._records_do_tenant()`."""
    return [s for s in solicitacoes if s.tenant_id == tenant_id]


_STATUS_AGUARDANDO_DECISAO = frozenset({StatusRevisao.PENDING, StatusRevisao.IN_REVIEW})


def verificar_sla_e_alarmar(
    solicitacoes: list[HumanReviewRequest],
    governance: GovernanceEngine,
    agora_: Timestamp | None = None,
) -> list[GovernanceAlert]:
    """Vol.VII Cap.28 + Cap.27 (achado de revisão, Milestone 4 desta
    construção) — fila de Human Review sem alarme de SLA real: nada
    disparava `GovernanceEngine.raise_alert()` quando `sla_deadline` era
    ultrapassado sem decisão, apesar de `HumanReviewRequest.sla_deadline`
    existir desde o Cap.28 e `FonteAlerta.HUMAN_REVIEW_BACKLOG` existir
    desde o Cap.27.

    Só solicitações ainda aguardando decisão (`pending`/`in-review`)
    alarmam — uma já decidida (`approved`/`rejected`/`changes-requested`)
    nunca dispara, mesmo que o prazo tenha passado depois: a SLA mede o
    tempo ATÉ a decisão, não depois dela.

    Deliberadamente NÃO filtra por tenant (Fase 5, Estágio 5.2) — opera
    sobre a lista completa que o chamador fornece. Um sweep de
    governança/operação (o uso pretendido, ver `orchestration/
    mission_resumption.py::executar_ciclo_watchdog`) plausivelmente
    PRECISA ver o backlog de SLA de todos os tenants para operar a
    plataforma — não é o mesmo tipo de leitura que `MissionRuntime.
    get_mission()` (Estágio 5.1), onde um chamador escopado a UM tenant
    nunca deveria ver dado de outro. Quem quiser um sweep por tenant usa
    `solicitacoes_do_tenant()` antes de chamar esta função."""
    momento = agora_ or agora()
    disparados: list[GovernanceAlert] = []
    for solicitacao in solicitacoes:
        if solicitacao.status not in _STATUS_AGUARDANDO_DECISAO:
            continue
        if momento <= solicitacao.sla_deadline:
            continue

        alerta = GovernanceAlert(
            source=FonteAlerta.HUMAN_REVIEW_BACKLOG,
            severity=SeveridadeAlerta.WARNING,
            evidence=[
                Evidence(
                    origem=f"HumanReviewRequest:{solicitacao.id}",
                    evidencias=[
                        f"kind={solicitacao.kind.value}",
                        f"status={solicitacao.status.value}",
                        f"sla_deadline={solicitacao.sla_deadline.isoformat()}",
                    ],
                )
            ],
        )
        governance.raise_alert(alerta)
        disparados.append(alerta)
    return disparados
