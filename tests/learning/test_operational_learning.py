"""Testes de Operational Learning (Vol.VI Cap.26) — AT-26.1 a AT-26.3."""

from __future__ import annotations

from datetime import timedelta

from batman_os.foundation.types import (
    CognitiveDebtFlag,
    DecisionOption,
    HumanReviewRef,
    MissionId,
    MissionTypeId,
    RuleId,
    TenantId,
    Timestamp,
    agora,
)
from batman_os.kernel.mission_runtime import MissionState
from batman_os.learning.operational_learning import (
    ItemDeBacklog,
    cognitive_debt_por_tipo,
    idade_do_backlog_pendente,
    leitura_cognitive_debt,
    rastrear_origem_da_regra,
    tempo_de_resolucao_dos_concluidos,
    trajetoria_cognitive_debt,
)
from batman_os.learning.rule_evolution import (
    DecisionPointSignature,
    RuleDefinition,
    RulePromotion,
    StatusRegra,
    resolve_rule,
)
from batman_os.runtime.operational_memory import (
    DecisionSummary,
    OperationalMemory,
    OperationalRecord,
    find_promotion_candidates,
)

TIPO_A = MissionTypeId("investigate-incident")
TIPO_B = MissionTypeId("preparar-deploy")


def _registro(
    mission_type: MissionTypeId,
    flag: CognitiveDebtFlag,
    recorded_at: Timestamp,
) -> OperationalRecord:
    return OperationalRecord(
        mission_id=MissionId("m-x"),
        tenant_id=TenantId("t-1"),
        mission_type=mission_type,
        final_state=MissionState.COMPLETED,
        cognitive_debt_flag=flag,
        recorded_at=recorded_at,
    )


class TestAT261CognitiveDebtIsoladoPorMissionType:
    def test_dois_tipos_com_debt_diferentes_nao_se_misturam(self) -> None:
        agora_ = agora()
        registros = [
            _registro(TIPO_A, CognitiveDebtFlag.AUTONOMOUS, agora_),
            _registro(TIPO_A, CognitiveDebtFlag.AUTONOMOUS, agora_),
            _registro(TIPO_A, CognitiveDebtFlag.HUMAN, agora_),
            _registro(TIPO_B, CognitiveDebtFlag.HUMAN, agora_),
            _registro(TIPO_B, CognitiveDebtFlag.LLM, agora_),
        ]

        debt_a = cognitive_debt_por_tipo(registros, TIPO_A)
        debt_b = cognitive_debt_por_tipo(registros, TIPO_B)

        assert debt_a == 1 / 3
        assert debt_b == 1.0

    def test_tipo_sem_registros_e_no_data_e_nunca_zero(self) -> None:
        """Ausencia de dado NAO pode ler como autonomia perfeita.

        ⚠️ Este teste exigia `== 0.0` ate 2026-08-26 — ou seja, consagrava a
        falha silenciosa como requisito. E `0.0` neste KPI significa "toda
        missao resolvida autonomamente": sem nenhum registro, a metrica que
        mede autonomia declarava autonomia perfeita. "Nunca tentou" e
        "acertou tudo" produziam o mesmo numero, e o segundo e o que alguem
        publicaria num relatorio.

        A spec (Vol.VI Cap.26) nao define o caso vazio — nao ha divergencia
        com ela, e sim lacuna preenchida pela unica leitura defensavel.
        """
        assert cognitive_debt_por_tipo([], TIPO_A) is None

    def test_leitura_publica_a_cobertura_junto_da_taxa(self) -> None:
        """Taxa sem cobertura nao e interpretavel: "das que rodaram, quantas
        precisaram de fora?" nao diz quantas deveriam ter rodado."""
        leitura = leitura_cognitive_debt([], TIPO_A)
        assert leitura.sem_dados is True
        assert leitura.total_missoes == 0
        assert leitura.proporcao_nao_autonoma is None

    def test_trajetoria_mostra_queda_ao_longo_do_tempo(self) -> None:
        base = agora()
        janela = timedelta(days=7)
        registros = [
            _registro(TIPO_A, CognitiveDebtFlag.HUMAN, base),
            _registro(TIPO_A, CognitiveDebtFlag.HUMAN, base + timedelta(days=1)),
            _registro(TIPO_A, CognitiveDebtFlag.AUTONOMOUS, base + timedelta(days=8)),
            _registro(TIPO_A, CognitiveDebtFlag.AUTONOMOUS, base + timedelta(days=9)),
        ]

        trajetoria = trajetoria_cognitive_debt(registros, TIPO_A, tamanho_janela=janela)

        assert len(trajetoria) == 2
        assert trajetoria[0].proporcao_autonoma == 0.0
        assert trajetoria[1].proporcao_autonoma == 1.0

    def test_trajetoria_vazia_sem_registros(self) -> None:
        assert trajetoria_cognitive_debt([], TIPO_A, timedelta(days=7)) == []


class TestAT262RastreabilidadeDeOrigemDaRegra:
    def test_origem_de_regra_ativa_e_sempre_reviewed_by_preenchido(self) -> None:
        regra = RuleDefinition(
            id=RuleId("R-1"),
            version="1.0.0",
            applies_to=DecisionPointSignature(pergunta_padrao="qual acao?"),
            resolution=DecisionOption(id="a", descricao="A"),
            confidence_base=0.9,
            provenance=RulePromotion(
                source_candidate_signature="sig-1", reviewed_by=HumanReviewRef("review-42")
            ),
            status=StatusRegra.ACTIVE,
        )

        assert rastrear_origem_da_regra(regra) == HumanReviewRef("review-42")

    def test_ciclo_completo_de_ponta_a_ponta(self) -> None:
        """OperationalRecord (Vol.III Cap.13) -> PromotionCandidate ->
        RuleDefinition com reviewed_by -> resolve_rule() (Cap.24) consome a
        regra -> origem sempre rastreavel (AT-26.2)."""
        from batman_os.foundation.types import EscalationPolicy, Reversibilidade
        from batman_os.kernel.planning_engine import DecisionPoint

        memory = OperationalMemory()
        for _ in range(3):
            memory.registrar(
                OperationalRecord(
                    mission_id=MissionId("m-x"),
                    tenant_id=TenantId("t-1"),
                    mission_type=TIPO_A,
                    decision_points_resolved=(
                        DecisionSummary(
                            decision_point_id="dp-timeout",
                            resolved_by="human",
                            chosen_option_id="aumentar-timeout",
                            confidence=1.0,
                        ),
                    ),
                    final_state=MissionState.COMPLETED,
                    cognitive_debt_flag=CognitiveDebtFlag.HUMAN,
                )
            )

        candidatos = find_promotion_candidates(memory, threshold=3)
        assert len(candidatos) == 1

        regra_promovida = RuleDefinition(
            id=RuleId("R-timeout"),
            version="1.0.0",
            applies_to=DecisionPointSignature(pergunta_padrao="qual acao para timeout?"),
            resolution=DecisionOption(id="aumentar-timeout", descricao="Aumentar timeout"),
            confidence_base=0.9,
            provenance=RulePromotion(
                source_candidate_signature=candidatos[0].assinatura,
                reviewed_by=HumanReviewRef("review-99"),
            ),
            status=StatusRegra.ACTIVE,
        )

        ponto = DecisionPoint(
            pergunta="qual acao para timeout?",
            opcoes=[DecisionOption(id="aumentar-timeout", descricao="Aumentar timeout")],
            escalation_policy=EscalationPolicy(
                confidence_threshold=0.8,
                preferred_escalation="human",
                max_llm_retries=1,
                reversibility=Reversibilidade.REVERSIVEL,
            ),
        )
        resolvida = resolve_rule(ponto, dados={}, candidatos=[regra_promovida])

        assert resolvida is not None
        assert rastrear_origem_da_regra(resolvida) == HumanReviewRef("review-99")


class TestAT263BacklogDeHumanReviewMensuravel:
    def test_itens_pendentes_tem_idade_calculada(self) -> None:
        agora_ = agora()
        itens = [
            ItemDeBacklog(identificado_em=agora_ - timedelta(days=5)),
            ItemDeBacklog(identificado_em=agora_ - timedelta(days=1), resolvido_em=agora_),
        ]

        idades = idade_do_backlog_pendente(itens, agora_)

        assert len(idades) == 1
        assert idades[0] == timedelta(days=5)

    def test_itens_concluidos_tem_tempo_de_resolucao_calculado(self) -> None:
        agora_ = agora()
        itens = [
            ItemDeBacklog(
                identificado_em=agora_ - timedelta(days=10),
                resolvido_em=agora_ - timedelta(days=2),
            ),
            ItemDeBacklog(identificado_em=agora_),  # ainda pendente
        ]

        tempos = tempo_de_resolucao_dos_concluidos(itens)

        assert tempos == [timedelta(days=8)]

    def test_sem_itens_backlog_vazio(self) -> None:
        assert idade_do_backlog_pendente([], agora()) == []
        assert tempo_de_resolucao_dos_concluidos([]) == []
