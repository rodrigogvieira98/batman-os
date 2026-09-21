"""Vol. VII, Cap. 30 — Observability Engine.

Fecha o Volume VII — e o núcleo funcional completo do Batman OS (Volumes
I-VII). Consolida e expõe todos os KPIs já definidos capítulo a capítulo
como uma superfície única e consultável, com Cognitive Debt como métrica
mestre (Volume I, Cap. 4).

Secao 30.2: o Observability Engine NÃO calcula nada novo — é projeção
derivada (mesmo padrão de ADR-0010/ADR-0004). Secao 30.7: nenhum
componente do Kernel/Runtime depende dele para operar — este módulo,
como `governance_engine.py` (ADR-0012), nunca importa `batman_os.kernel`.

Fonte da verdade: docs/spec/07-governance/04-observability-engine.md
"""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum
from typing import Literal, NewType

from pydantic import BaseModel, Field

from batman_os.foundation.types import (
    Evidence,
    Medicao,
    MissionId,
    TenantId,
    Timestamp,
    agora,
)
from batman_os.governance.governance_engine import (
    FonteAlerta,
    GovernanceAlert,
    GovernanceEngine,
    SeveridadeAlerta,
)

MetricId = NewType("MetricId", str)
AlertRuleId = NewType("AlertRuleId", str)

CondicaoAlerta = Literal["above", "below", "trend-worsening"]


class NomeDoPainel(StrEnum):
    """Vol.VII Cap.30, secao 30.2 — os quatro `Dashboard`s nomeados."""

    COGNITIVE_DEBT = "cognitive-debt"
    SLA_HEALTH = "sla-health"
    LEARNING_THROUGHPUT = "learning-throughput"
    GOVERNANCE_BACKLOG = "governance-backlog"


# Vol.VII Cap.30, secao 30.3 — catalogo consolidado (mapa completo da
# obra). Amostra representativa das metricas ja especificadas em volumes
# anteriores; a tabela completa tem >20 entradas, cada uma ja definida em
# seu proprio capitulo-fonte (este modulo nao recalcula nenhuma).
PAINEL_DAS_METRICAS: dict[MetricId, NomeDoPainel] = {
    MetricId("cognitive-debt-global"): NomeDoPainel.COGNITIVE_DEBT,
    MetricId("resolved-by-llm-percentage"): NomeDoPainel.COGNITIVE_DEBT,
    MetricId("sla-cumprido-por-tipo"): NomeDoPainel.SLA_HEALTH,
    MetricId("taxa-partially-completed"): NomeDoPainel.SLA_HEALTH,
    MetricId("cobertura-de-playbook"): NomeDoPainel.LEARNING_THROUGHPUT,
    MetricId("regras-promovidas-por-periodo"): NomeDoPainel.LEARNING_THROUGHPUT,
    MetricId("tamanho-knowledge-graph"): NomeDoPainel.LEARNING_THROUGHPUT,
    MetricId("backlog-human-review"): NomeDoPainel.GOVERNANCE_BACKLOG,
}


class PontoDeSerie(BaseModel):
    """Vol.VII Cap.30, secao 30.2 — um ponto de um `TimeSeries`."""

    timestamp: Timestamp
    value: float


class TimeSeries(BaseModel):
    """Vol.VII Cap.30, secao 30.2/30.6.

    `janela_agregacao`/`reconciliado_em` SEMPRE presentes (campos
    obrigatórios, não opcionais) — a honestidade epistêmica da secao 30.6
    ("nunca ler como estado em tempo real absoluto") é estrutural aqui,
    não uma convenção de uso deixada ao consumidor do painel."""

    metric_id: MetricId
    points: list[PontoDeSerie] = Field(default_factory=list)
    janela_agregacao: timedelta
    reconciliado_em: Timestamp = Field(default_factory=agora)


def construir_serie(
    metric_id: MetricId, pontos: list[PontoDeSerie], janela_agregacao: timedelta
) -> TimeSeries:
    """Vol.VII Cap.30, secao 30.8 (AT-30.1) — função PURA: a mesma lista de
    pontos sempre produz a mesma `TimeSeries` (ordenada por tempo), o que é
    o que garante que o valor exposto por `get_metric` seja reproduzível
    independentemente a partir da mesma fonte de dados (aqui, uma lista de
    pontos já reconciliados a partir do Event Bus por quem chama — este
    módulo não faz `replay` ele mesmo, apenas garante que o resultado é
    determinístico dado o mesmo insumo)."""
    return TimeSeries(
        metric_id=metric_id,
        points=sorted(pontos, key=lambda p: p.timestamp),
        janela_agregacao=janela_agregacao,
    )


class ModeloDeAlerta(BaseModel):
    """Vol.VII Cap.30, secao 30.5 — `GovernanceAlert` sem `id`/`createdAt`/
    `status` (preenchidos por `GovernanceEngine.raise_alert` no disparo)."""

    source: FonteAlerta
    severity: SeveridadeAlerta
    related_mission_id: MissionId | None = None
    related_tenant_id: TenantId | None = None


class AlertRule(BaseModel):
    """Vol.VII Cap.30, secao 30.5."""

    id: AlertRuleId
    metric_id: MetricId
    condition: CondicaoAlerta
    threshold: float
    window: timedelta
    raises_alert_with: ModeloDeAlerta


def assunto_de_metrica(base: str) -> str:
    """A identidade do assunto "esta metrica", igual na abertura e no fim."""
    return f"metrica:{base}"


def _base_da_regra(regra_id: str) -> str:
    """`observe.cpu:crit` -> `observe.cpu`.

    ⚠️ Corta pela DIREITA, uma vez só: a base de latência é
    `observe.latency:https://exemplo.group`, que já tem dois-pontos dentro
    da URL. Cortar pela esquerda quebraria justamente a métrica mais comprida.
    """
    return regra_id.rsplit(":", 1)[0]


def _linhas_da_medicao(medicao: Medicao | None) -> list[str]:
    """A medição em texto legível, ao lado do campo estruturado.

    ⚠️ As duas coisas, e não uma. O campo é para máquina reler depois; a linha é
    para a pessoa que abre o Discord às 3 da manhã. E é aqui que a **capacidade
    de referência** aparece: sem ela, `85 %` não diz se são 85 % de 4 vCPUs ou
    de um núcleo — a objeção que abriu esta frente.
    """
    if medicao is None:
        return []
    if medicao.valor is None:
        # ⚠️ Cegueira é dita, nunca convertida em zero.
        return [f"valor NAO MEDIDO nesta janela (fonte {medicao.fonte or 'desconhecida'})"]
    linhas = [f"valor={medicao.valor:g}{medicao.unidade}"]
    if medicao.capacidade:
        linhas.append(f"capacidade de referencia: {medicao.capacidade}")
    if medicao.janela_s:
        linhas.append(f"janela da amostra: {medicao.janela_s:g}s")
    if medicao.pico is not None:
        # ⚠️ `DIV-CPU-001`. O pico e CONTEXTO, e a linha diz isso por extenso —
        # ele decidia o alerta ate 16/09 e publicou saturacao 20 vezes numa
        # maquina 96 % ociosa, porque descrevia 1 s de arranque de cron. Com a
        # janela ao lado, quem le distingue rajada de saturacao sem precisar
        # saber como o Batman mede.
        janela = f"{medicao.pico_janela_s:g}s" if medicao.pico_janela_s else "instantaneo"
        linhas.append(
            f"pico de {medicao.pico:g}{medicao.unidade} em {janela} dentro da janela"
            ", rajada, NAO e o valor que decidiu este alerta"
        )
    if medicao.amostras is not None:
        linhas.append(f"amostras no denominador: {medicao.amostras}")
    if medicao.limiar_do_ambiente:
        # ⚠️ Declarado, sempre. Um limiar vindo do ambiente muda o criterio, e
        # criterio que muda em silencio e pior que criterio errado.
        linhas.append("⚠️ LIMIAR SOBREPOSTO pelo ambiente, o numero nao e o do codigo")
    return linhas


class ObservabilityEngine:
    """Vol.VII Cap.30, secao 30.2."""

    def __init__(self, governance: GovernanceEngine) -> None:
        self._series: dict[MetricId, TimeSeries] = {}
        self._medicoes: dict[str, Medicao] = {}
        self._regras: list[AlertRule] = []
        self._governance = governance

    def registrar_serie(self, serie: TimeSeries) -> None:
        self._series[serie.metric_id] = serie

    def registrar_medicao(self, medicao: Medicao) -> None:
        """A medição ESTRUTURADA da base, para o alerta carregar o número.

        ⚠️ Existe porque o valor chegava ao alerta apenas como texto dentro de
        `Evidence.evidencias`, e o journal gravava `measured_value=None`. Medido
        em produção em 2026-09-08: nenhum dos 11 alertas de CPU podia ser
        reexaminado, porque nenhum guardava o que mediu.

        A base é a métrica sem o sufixo de banda: a regra `observe.cpu:crit`
        consome a medição de `observe.cpu`.
        """
        self._medicoes[medicao.base] = medicao

    def get_metric(self, metric_id: MetricId) -> TimeSeries | None:
        return self._series.get(metric_id)

    def get_dashboard(self, view: NomeDoPainel) -> dict[MetricId, TimeSeries]:
        """Vol.VII Cap.30, secao 30.2 (`getDashboard`)."""
        return {
            metric_id: serie
            for metric_id, serie in self._series.items()
            if PAINEL_DAS_METRICAS.get(metric_id) == view
        }

    def register_alert_rule(self, rule: AlertRule) -> None:
        self._regras.append(rule)

    def avaliar_regras(self, agora_: Timestamp | None = None) -> list[GovernanceAlert]:
        """Vol.VII Cap.30, secao 30.5 (AT-30.2) — avalia cada `AlertRule`
        contra a série atual da métrica que monitora; dispara
        `GovernanceAlert` via `GovernanceEngine.raise_alert` quando
        violada."""
        agora_ = agora_ or agora()
        disparados: list[GovernanceAlert] = []
        for regra in self._regras:
            serie = self._series.get(regra.metric_id)
            if serie is None:
                continue

            pontos_na_janela = [p for p in serie.points if agora_ - p.timestamp <= regra.window]
            if not self._violada(regra, pontos_na_janela):
                continue

            alerta = GovernanceAlert(
                source=regra.raises_alert_with.source,
                severity=regra.raises_alert_with.severity,
                evidence=[
                    Evidence(
                        origem=f"AlertRule:{regra.id}",
                        evidencias=[
                            f"metric={regra.metric_id}",
                            f"condition={regra.condition}",
                            f"threshold={regra.threshold}",
                        ]
                        + _linhas_da_medicao(self._medicoes.get(_base_da_regra(regra.id))),
                        # O elo com o fechamento: a recuperacao carimba o MESMO
                        # assunto, e e assim que ela acha a mensagem a editar.
                        assunto=assunto_de_metrica(_base_da_regra(regra.id)),
                        medicao=self._medicoes.get(_base_da_regra(regra.id)),
                    )
                ],
                related_mission_id=regra.raises_alert_with.related_mission_id,
                related_tenant_id=regra.raises_alert_with.related_tenant_id,
            )
            self._governance.raise_alert(alerta)
            disparados.append(alerta)
        return disparados

    def _violada(self, regra: AlertRule, pontos: list[PontoDeSerie]) -> bool:
        if not pontos:
            return False
        if regra.condition == "above":
            return any(p.value > regra.threshold for p in pontos)
        if regra.condition == "below":
            return any(p.value < regra.threshold for p in pontos)
        return self._piora_sustentada(pontos)

    def _piora_sustentada(self, pontos: list[PontoDeSerie]) -> bool:
        """Vol.VII Cap.30, secao 30.5 (AT-30.2) — `trend-worsening` dispara
        quando a métrica piora de forma sustentada, mesmo sem violar
        `threshold` em nenhum ponto isolado (por isso `threshold` não é
        consultado aqui, só `window`, já aplicado pelo chamador)."""
        if len(pontos) < 2:
            return False
        ordenados = sorted(pontos, key=lambda p: p.timestamp)
        return all(
            posterior.value > anterior.value
            for anterior, posterior in zip(ordenados, ordenados[1:], strict=False)
        )
