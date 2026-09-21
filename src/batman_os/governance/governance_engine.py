"""Vol. VII, Cap. 27 — Governance Engine.

Consolida, expõe e alarma sobre a saúde de conformidade do Batman OS em
relação aos seus próprios princípios (Volume I) — sem executar nenhuma
ação do Kernel diretamente (ADR-0012). A mesma relação que a Operational
Memory (ADR-0004) tem com o comportamento do Kernel: informa e aciona,
nunca decide sozinho por substituição.

ADR-0012: Governance Engine sem autoridade executiva direta — este módulo
NUNCA importa de `batman_os.kernel` (AT-27.3).

Fonte da verdade: docs/spec/07-governance/01-governance-engine.md
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from batman_os.foundation.types import (
    AlertId,
    Evidence,
    HumanReviewRef,
    MissionId,
    TenantId,
    Timestamp,
    agora,
    novo_uuid7,
)

if TYPE_CHECKING:
    from batman_os.governance.alert_sinks import AlertSink

logger = logging.getLogger(__name__)


class FonteAlerta(StrEnum):
    """Vol.VII Cap.27, secao 27.4 — `GovernanceAlert.source`."""

    SLA_BREACH = "sla-breach"
    LLM_CIRCUIT_BREAKER = "llm-circuit-breaker"
    TENANT_ISOLATION_INCIDENT = "tenant-isolation-incident"
    HUMAN_REVIEW_BACKLOG = "human-review-backlog"
    RULE_DRIFT = "rule-drift"
    ADDENDUM_REVIEW_REQUEST = "addendum-review-request"
    # Observabilidade de runtime/infra (Anexo Cap.27/30 — subsistema
    # `batman_os.observe`, paridade com o observe/daemon do Batman legado).
    # Extensao do StrEnum: nunca reinterpreta valores existentes.
    INFRA_SATURATION = "infra-saturation"
    ENDPOINT_DOWN = "endpoint-down"
    ENDPOINT_LATENCY = "endpoint-latency"
    ENDPOINT_ERROR_RATE = "endpoint-error-rate"
    SERVICE_DOWN = "service-down"
    SECURITY_INTRUSION = "security-intrusion"
    OBSERVE_HEARTBEAT = "observe-heartbeat"
    # Monitor funcional / sintetico (docs/observe/MONITOR_FUNCIONAL.md — exercita
    # a jornada do usuario e falha quando a funcionalidade nao e entregue, mesmo
    # com CPU baixo e HTTP 200). Extensao do StrEnum: nunca reinterpreta valores
    # existentes.
    FEATURE_DOWN = "feature-down"
    FEATURE_RECOVERED = "feature-recovered"
    MANIFEST_DRIFT = "manifest-drift"
    #: O monitor NAO CONSEGUIU VERIFICAR — distinto de "a feature caiu".
    #:
    #: Nasceu em 2026-09-05, de um episodio de 18 h: o login sintetico passou a
    #: ser recusado, e os 7 checks que dependem da sessao pararam de rodar
    #: emitindo apenas um WARNING de log dizendo "config, nao outage" -- frase
    #: que o codigo nao tinha como sustentar, porque nao distinguia credencial
    #: ausente de login reprovado. No Discord, "nao rodou" e "passou" ficaram
    #: indistinguiveis.
    #:
    #: Nao reusa FEATURE_DOWN de proposito: as features podiam estar intactas.
    #: Dizer que cairam seria o alerta afirmando mais do que mediu.
    MONITOR_CEGO = "monitor-cego"
    # dados-sentinela (Onda 1, Plano Cobertura Total, S162) -- observe/
    # data_sentinela.py: saude de DADOS em producao (frescor/integridade),
    # cegueira nº2 do plano ("pipeline error sem alerta" -- PIPE_FSIM_MTM01
    # do radar-preditivo). Extensao do StrEnum: nunca reinterpreta valores
    # existentes.
    DATA_PIPELINE_ERROR = "data-pipeline-error"
    DATA_SOURCE_STALE = "data-source-stale"
    DATA_SOURCE_MISSING = "data-source-missing"
    DATA_ROW_COUNT_DROP = "data-row-count-drop"
    #: A ORIGEM aceitou trafego que nao veio pelo proxy da nossa zona —
    #: `DIV-REALIP-002` e `WEB-06`. Extensao do StrEnum, como as anteriores:
    #: nunca reinterpreta valores existentes.
    #:
    #: ⚠️ Valor NOVO em vez de reusar `SECURITY_INTRUSION`, e a distincao e o
    #: ponto: nao ha intrusao, ha EXPOSICAO. O relatorio diario agrupa por
    #: fonte, entao classificar postura como intrusao contaminaria a contagem de
    #: intrusoes — e um numero de intrusoes que cresce sem intrusao nenhuma e
    #: exatamente o tipo de mentira que esta frente existe para eliminar.
    ORIGEM_ALCANCAVEL = "origem-alcancavel"


class SeveridadeAlerta(StrEnum):
    """Vol.VII Cap.27, secao 27.4."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class StatusAlerta(StrEnum):
    """Vol.VII Cap.27, secao 27.4."""

    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class GovernanceAlert(BaseModel):
    """Vol.VII Cap.27, secao 27.4."""

    id: AlertId = Field(default_factory=lambda: AlertId(novo_uuid7()))
    source: FonteAlerta
    severity: SeveridadeAlerta
    evidence: list[Evidence]
    related_mission_id: MissionId | None = None
    related_tenant_id: TenantId | None = None
    status: StatusAlerta = StatusAlerta.OPEN
    created_at: Timestamp = Field(default_factory=agora)
    acknowledged_by: HumanReviewRef | None = None
    resolution: str | None = None


class EvidenciaObrigatoriaParaAlerta(Exception):
    """Vol.VII Cap.27, secao 27.9 (AT-27.1) — nenhum `GovernanceAlert` pode
    ser criado sem `evidence` associada (Evidence First)."""


class AlertaDesconhecido(Exception):
    """`acknowledge`/`resolve` chamado com um `AlertId` não registrado."""


class GovernanceEngine:
    """Vol.VII Cap.27, secao 27.4 — só consome eventos/registros de outros
    componentes (Event Bus, Operational Memory, Knowledge Graph); nunca
    publica algo que o Kernel consome de volta (secao 27.5)."""

    def __init__(self, sink: AlertSink | None = None) -> None:
        self._alertas: dict[AlertId, GovernanceAlert] = {}
        # Entrega externa opcional (Discord etc.). None = comportamento
        # original: alerta so vive em memoria. O sink NUNCA e do kernel
        # (ADR-0012) — recebe um GovernanceAlert ja pronto.
        self._sink = sink

    def raise_alert(self, alert: GovernanceAlert) -> str | None:
        """AT-27.1. Devolve o DESFECHO da entrega, quando o sink o informa.

        ⚠️ **Devolver e o conserto do `DIV-PROVA-001`.** Antes isto era `-> None`
        e o emissor nao tinha como distinguir "entregue" de "suprimido pelo
        throttle". Medido em 11/09: a prova de vida das 06:00 caiu em
        `suprimido_por_janela` e o emissor gravou a janela como relatada --
        falha silenciosa dentro do mecanismo feito para detectar silencio.

        `None` significa "nao ha sink" ou "o sink nao informa desfecho", e NAO
        "entregue". Quem trata o retorno precisa distinguir os dois: assumir
        entrega na ausencia de informacao seria recriar o mesmo defeito.
        """
        if not alert.evidence:
            raise EvidenciaObrigatoriaParaAlerta(
                f"GovernanceAlert (source='{alert.source.value}') nao pode ser "
                "levantado sem evidence (Evidence First)"
            )
        self._alertas[alert.id] = alert
        if self._sink is None:
            return None
        # Entrega best-effort: uma falha de webhook JAMAIS pode impedir
        # que o alerta seja registrado/consultavel (mesma disciplina do
        # `_post` legado e do CostTracker.registrar).
        try:
            return self._sink.enviar(alert)
        except Exception as exc:  # defesa em profundidade
            logger.error("sink de alerta falhou (alerta persiste): %s", exc)
            return "falha_de_sink"

    def get_open_alerts(
        self,
        source: FonteAlerta | None = None,
        severity: SeveridadeAlerta | None = None,
    ) -> list[GovernanceAlert]:
        return [
            a
            for a in self._alertas.values()
            if a.status == StatusAlerta.OPEN
            and (source is None or a.source == source)
            and (severity is None or a.severity == severity)
        ]

    def acknowledge(self, alert_id: AlertId, by: HumanReviewRef) -> GovernanceAlert:
        alerta = self._exigir(alert_id)
        atualizado = alerta.model_copy(
            update={"status": StatusAlerta.ACKNOWLEDGED, "acknowledged_by": by}
        )
        self._alertas[alert_id] = atualizado
        return atualizado

    def resolve(self, alert_id: AlertId, resolution: str) -> GovernanceAlert:
        alerta = self._exigir(alert_id)
        atualizado = alerta.model_copy(
            update={"status": StatusAlerta.RESOLVED, "resolution": resolution}
        )
        self._alertas[alert_id] = atualizado
        return atualizado

    def _exigir(self, alert_id: AlertId) -> GovernanceAlert:
        if alert_id not in self._alertas:
            raise AlertaDesconhecido(f"GovernanceAlert desconhecido: {alert_id}")
        return self._alertas[alert_id]


class StatusAnexo(StrEnum):
    """Vol.VII Cap.27, secao 27.6 — ciclo de vida de um Anexo (`ADDENDA.md`)."""

    PROPOSED = "proposed"
    UNDER_REVIEW = "under-review"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class AddendumRecord(BaseModel):
    """Vol.VII Cap.27, secao 27.6 — registro mínimo de um Anexo do ponto de
    vista do Governance Engine; o conteúdo do Anexo em si continua vivendo
    em `ADDENDA.md` (fora do escopo de código desta construção)."""

    add_id: str
    status: StatusAnexo = StatusAnexo.PROPOSED
    reviewed_by: HumanReviewRef | None = None


class RevisaoHumanaObrigatoriaParaAnexo(Exception):
    """Vol.VII Cap.27, secao 27.6 (AT-27.2) — nenhum Anexo transiciona para
    `Accepted` sem `HumanReviewRef` registrado, nem mesmo quando o revisor
    humano é o próprio autor da obra atuando nesse papel (secao 27.6)."""


def aceitar_anexo(anexo: AddendumRecord, reviewed_by: HumanReviewRef | None) -> AddendumRecord:
    """Vol.VII Cap.27, secao 27.6 (AT-27.2)."""
    if reviewed_by is None:
        raise RevisaoHumanaObrigatoriaParaAnexo(
            f"Anexo '{anexo.add_id}' nao pode transicionar para Accepted sem "
            "HumanReviewRef registrado"
        )
    return anexo.model_copy(update={"status": StatusAnexo.ACCEPTED, "reviewed_by": reviewed_by})
