"""Vol. IX, Cap. 34/35 — cenário de referência ponta a ponta.

Reproduz o cenário "timeout no Gunicorn" (Cap.35, o mesmo exemplo usado
desde a abertura da obra no Cap.1) usando os componentes REAIS já
construídos — nenhum mock de Kernel/Runtime/Learning, só os Protocols de
borda que a própria arquitetura já define (invocador de step, LLM
gateway, validador). Prova que o sistema se compõe de ponta a ponta pelo
menos para este caminho (AT-35.1/35.2) — fecha a lacuna que a tabela de
status do Cap.35 registrava como "ainda não existe: o cenário completo
como suíte de teste de integração".

Execução 1 (secao 35.2): Mission Runtime cria a Missão -> Planning Engine
compõe via grafo (nenhum Playbook existe ainda) -> Decision Engine escala
para humano (Knowledge Base não cobre) -> Workflow Engine executa com
checkpoint -> Missão conclui com `cognitiveDebtFlag=human`.

Ciclo de aprendizado + Execução N (secao 35.3/35.4): após N ocorrências
idênticas, `find_promotion_candidates` (Vol.III Cap.13) identifica o
padrão -> `RuleDefinition` promovida via shadow mode (Vol.VI Cap.24) ->
uma nova ocorrência é resolvida por conhecimento via
`CatalogoDeRegrasComoBaseConhecimento` (adaptador Rule Evolution ->
Decision Engine) -> Cognitive Debt cai para este `MissionTypeId`.
"""

from __future__ import annotations

from datetime import timedelta

from batman_os.foundation.types import (
    CapabilityId,
    CapabilityRef,
    CognitiveDebtFlag,
    Criticidade,
    DecisionOption,
    EscalationPolicy,
    Evidence,
    HumanReviewRef,
    MissionId,
    MissionTypeId,
    Reversibilidade,
    RuleId,
    TenantId,
)
from batman_os.kernel.decision_engine import DecisionEngine, RespostaLlmCandidata
from batman_os.kernel.event_bus import EventBus
from batman_os.kernel.mission_runtime import (
    MissionEventType,
    MissionIntent,
    MissionRuntime,
    MissionState,
)
from batman_os.kernel.planning_engine import DecisionPoint, plan
from batman_os.kernel.workflow_engine import ResultadoInvocacao, WorkflowEngine
from batman_os.learning.knowledge_graph import KnowledgeGraph, TipoNoKnowledge
from batman_os.learning.operational_learning import cognitive_debt_por_tipo
from batman_os.learning.rule_evolution import (
    DecisionPointSignature,
    RuleDefinition,
    RulePromotion,
    ShadowEvaluation,
    StatusRegra,
    promover_a_active,
)
from batman_os.learning.rule_evolution_adapter import CatalogoDeRegrasComoBaseConhecimento
from batman_os.runtime.operational_memory import (
    DecisionSummary,
    OperationalMemory,
    OperationalRecord,
    find_promotion_candidates,
)
from batman_os.workflow.missions import MissionTypeDefinition, MissionTypeRegistry

TIPO = MissionTypeId("investigate-incident")
TENANT = TenantId("t-1")
PERGUNTA = "qual acao tomar para o timeout do Gunicorn?"
OPCAO_CORRETA = DecisionOption(id="aumentar-timeout", descricao="Aumentar worker timeout")


def _registro_tipos() -> MissionTypeRegistry:
    registro = MissionTypeRegistry()
    registro.register(
        MissionTypeDefinition(
            id=TIPO,
            criticality=Criticidade.MEDIUM,
            default_sla=timedelta(hours=1),
            escalation_defaults=_politica(),
        )
    )
    return registro


def _politica() -> EscalationPolicy:
    return EscalationPolicy(
        confidence_threshold=0.8,
        preferred_escalation="human",
        max_llm_retries=1,
        reversibility=Reversibilidade.REVERSIVEL,
    )


def _ponto_decisao() -> DecisionPoint:
    return DecisionPoint(pergunta=PERGUNTA, opcoes=[OPCAO_CORRETA], escalation_policy=_politica())


class _RegistroCapacidadesFake:
    """Vol.II Cap.7 — composição via grafo: `collect-logs` e
    `analyze-latency`, as Capabilities genéricas citadas no Cap.35."""

    def buscar_candidatos(self, intent: MissionIntent) -> list[CapabilityRef]:
        del intent
        return [
            CapabilityRef(capability_id=CapabilityId("collect-logs"), versao="1.0.0"),
            CapabilityRef(capability_id=CapabilityId("analyze-latency"), versao="1.0.0"),
        ]

    def versao(self) -> str:
        return "v1"


class _InvocadorSempreSucesso:
    def invocar(self, step: object) -> ResultadoInvocacao:
        del step
        return ResultadoInvocacao(sucesso=True, output={"ok": True})


class _SemConhecimentoInicial:
    """Vol.II Cap.8, secao 35.2 passo 5 — antes de qualquer regra
    promovida, a Knowledge Base não cobre este padrão ainda."""

    def consultar(self, ponto: DecisionPoint) -> None:
        del ponto
        return None


class _LlmNuncaChamado:
    def consultar(self, ponto: DecisionPoint) -> RespostaLlmCandidata:
        del ponto
        raise AssertionError("Cenario nao envolve LLM em nenhum momento")


class _ValidadorQualquer:
    def validar(self, ponto: DecisionPoint, resposta: object) -> bool:
        del ponto, resposta
        return True


class TestExecucao1EscaladaParaHumano:
    """Vol.IX Cap.35, secao 35.2 — primeira ocorrência, Cognitive Debt alto."""

    def test_ciclo_completo_ate_completed_com_cognitive_debt_human(self) -> None:
        runtime = MissionRuntime(EventBus(), tipos=_registro_tipos())
        mission = runtime.create(
            MissionIntent(dados={"sintoma": "timeout", "servico": "gunicorn"}),
            TIPO,
            tenant_id=TENANT,
        )
        assert mission.estado == MissionState.CREATED

        runtime.transition(mission.id, MissionEventType.PLANNING_STARTED, TENANT)
        plano = plan(
            mission_id=mission.id,
            tenant_id=TENANT,
            intent=mission.intent,
            registro=_RegistroCapacidadesFake(),
        )
        assert len(plano.steps) == 2
        runtime.transition(mission.id, MissionEventType.PLAN_READY, TENANT)

        runtime.transition(mission.id, MissionEventType.DECIDING_STARTED, TENANT)
        engine = DecisionEngine(
            base_conhecimento=_SemConhecimentoInicial(),
            llm_gateway=_LlmNuncaChamado(),
            validador=_ValidadorQualquer(),
        )
        ponto = _ponto_decisao()
        resultado = engine.resolve(ponto, mission.id)
        assert resultado.escalonado_para == "human"
        runtime.transition(mission.id, MissionEventType.ESCALATED_TO_HUMAN, TENANT)
        assert runtime.get_state(mission.id, TENANT) == MissionState.AWAITING_HUMAN

        decision = engine.resolver_com_resposta_humana(
            ponto,
            mission.id,
            opcao_escolhida=OPCAO_CORRETA,
            evidencia=[
                Evidence(origem="humano", evidencias=["timeout sempre apos deploy de config X"])
            ],
        )
        assert decision.resolved_by == "human"
        runtime.transition(mission.id, MissionEventType.ESCALATION_RESOLVED, TENANT)
        runtime.transition(mission.id, MissionEventType.DECISIONS_RESOLVED, TENANT)
        assert runtime.get_state(mission.id, TENANT) == MissionState.EXECUTING

        wf = WorkflowEngine(_InvocadorSempreSucesso())
        run = wf.iniciar(mission.id, plano)
        while wf.get_run(run.id).estado == "running":
            prontos = wf.passos_prontos(run.id)
            if not prontos:
                break
            wf.executar_passo(run.id, prontos[0], TENANT)
        assert wf.get_run(run.id).estado == "completed"

        final = runtime.transition(mission.id, MissionEventType.WORKFLOW_COMPLETED, TENANT)
        assert final.estado == MissionState.COMPLETED
        assert final.cognitive_debt_flag == CognitiveDebtFlag.HUMAN

        memory = OperationalMemory()
        memory.registrar(
            OperationalRecord(
                mission_id=mission.id,
                tenant_id=TENANT,
                mission_type=TIPO,
                decision_points_resolved=(
                    DecisionSummary(
                        decision_point_id=PERGUNTA,
                        resolved_by="human",
                        chosen_option_id=OPCAO_CORRETA.id,
                        confidence=1.0,
                    ),
                ),
                final_state=final.estado,
                cognitive_debt_flag=final.cognitive_debt_flag,
            )
        )
        assert len(memory.all_records()) == 1


class TestCicloDeAprendizadoEExecucaoN:
    """Vol.IX Cap.35, secao 35.3/35.4 — o padrão se repete, é promovido a
    regra via shadow mode, e a próxima ocorrência não precisa mais de
    apoio humano: Cognitive Debt cai."""

    def _memoria_com_12_ocorrencias_humanas(self) -> OperationalMemory:
        memory = OperationalMemory()
        for indice in range(12):
            memory.registrar(
                OperationalRecord(
                    mission_id=MissionId(f"m-historico-{indice}"),
                    tenant_id=TENANT,
                    mission_type=TIPO,
                    decision_points_resolved=(
                        DecisionSummary(
                            decision_point_id=PERGUNTA,
                            resolved_by="human",
                            chosen_option_id=OPCAO_CORRETA.id,
                            confidence=1.0,
                        ),
                    ),
                    final_state=MissionState.COMPLETED,
                    cognitive_debt_flag=CognitiveDebtFlag.HUMAN,
                )
            )
        return memory

    def _promover_regra_via_shadow_mode(
        self, assinatura: str, grafo: KnowledgeGraph | None = None
    ) -> RuleDefinition:
        regra_draft = RuleDefinition(
            id=RuleId("R-gunicorn-timeout"),
            version="1.0.0",
            applies_to=DecisionPointSignature(pergunta_padrao=PERGUNTA),
            condition=[],
            resolution=OPCAO_CORRETA,
            confidence_base=0.95,
            provenance=RulePromotion(
                source_candidate_signature=assinatura,
                reviewed_by=HumanReviewRef("review-domain-expert"),
            ),
            status=StatusRegra.DRAFT,
        )
        avaliacoes = [
            ShadowEvaluation(
                rule_id=regra_draft.id,
                decision_point_id=PERGUNTA,
                shadow_resolution=OPCAO_CORRETA,
                actual_resolution=OPCAO_CORRETA,
                agreement=indice < 48,  # 48 de 50 = 96%, secao 35.3 passo 16
            )
            for indice in range(50)
        ]
        return promover_a_active(
            regra_draft,
            avaliacoes,
            taxa_minima=0.9,
            minimo_avaliacoes=50,
            grafo=grafo if grafo is not None else KnowledgeGraph(),
            tenant_id=TENANT,
        )

    def test_padrao_recorrente_e_identificado_como_candidato(self) -> None:
        memory = self._memoria_com_12_ocorrencias_humanas()

        candidatos = find_promotion_candidates(memory, threshold=12)

        assert len(candidatos) == 1
        assert candidatos[0].assinatura == PERGUNTA
        assert len(candidatos[0].registros) == 12

    def test_regra_promovida_via_shadow_mode_atinge_active(self) -> None:
        memory = self._memoria_com_12_ocorrencias_humanas()
        candidatos = find_promotion_candidates(memory, threshold=12)
        grafo = KnowledgeGraph()

        regra_ativa = self._promover_regra_via_shadow_mode(candidatos[0].assinatura, grafo=grafo)

        assert regra_ativa.status == StatusRegra.ACTIVE
        assert regra_ativa.provenance.reviewed_by == HumanReviewRef("review-domain-expert")
        # Milestone 4: a regra promovida agora entra no Knowledge Graph
        # (Cap.23) de verdade, fechando o ciclo descrito no Cap.26.
        nos_rule = grafo.nos_por_tipo(TipoNoKnowledge.RULE)
        assert any(no.ref == str(regra_ativa.id) for no in nos_rule)

    def test_execucao_n_resolve_por_conhecimento_sem_escalar_e_cognitive_debt_cai(self) -> None:
        memory = self._memoria_com_12_ocorrencias_humanas()
        candidatos = find_promotion_candidates(memory, threshold=12)
        regra_ativa = self._promover_regra_via_shadow_mode(candidatos[0].assinatura)

        # Execucao N (secao 35.4): novo MissionIntent, mesmo padrao
        runtime = MissionRuntime(EventBus(), tipos=_registro_tipos())
        nova_missao = runtime.create(
            MissionIntent(dados={"sintoma": "timeout", "servico": "gunicorn"}),
            TIPO,
            tenant_id=TENANT,
        )
        runtime.transition(nova_missao.id, MissionEventType.PLANNING_STARTED, TENANT)
        runtime.transition(nova_missao.id, MissionEventType.PLAN_READY, TENANT)
        runtime.transition(nova_missao.id, MissionEventType.DECIDING_STARTED, TENANT)

        # Decision Engine agora consulta o catalogo de regras ativas (fecha o
        # ciclo Rule Evolution -> Decision Engine, achado de revisao anterior)
        catalogo = CatalogoDeRegrasComoBaseConhecimento([regra_ativa])
        engine_n = DecisionEngine(
            base_conhecimento=catalogo,
            llm_gateway=_LlmNuncaChamado(),
            validador=_ValidadorQualquer(),
        )
        resultado_n = engine_n.resolve(_ponto_decisao(), nova_missao.id)

        assert resultado_n.decision is not None
        assert resultado_n.decision.resolved_by == "knowledge"  # sem escalonamento humano

        runtime.transition(nova_missao.id, MissionEventType.DECISIONS_RESOLVED, TENANT)
        final_n = runtime.transition(nova_missao.id, MissionEventType.WORKFLOW_COMPLETED, TENANT)
        assert final_n.cognitive_debt_flag == CognitiveDebtFlag.AUTONOMOUS

        memory.registrar(
            OperationalRecord(
                mission_id=nova_missao.id,
                tenant_id=TENANT,
                mission_type=TIPO,
                decision_points_resolved=(
                    DecisionSummary(
                        decision_point_id=PERGUNTA,
                        resolved_by="knowledge",
                        chosen_option_id=OPCAO_CORRETA.id,
                        confidence=regra_ativa.confidence_base,
                    ),
                ),
                final_state=final_n.estado,
                cognitive_debt_flag=final_n.cognitive_debt_flag,
            )
        )

        debt_so_historico = cognitive_debt_por_tipo(memory.all_records()[:12], TIPO)
        debt_com_execucao_n = cognitive_debt_por_tipo(memory.all_records(), TIPO)

        # `cognitive_debt_por_tipo` passou a devolver `None` para NO_DATA em
        # 2026-08-26: antes devolvia 0.0 sem registros, e 0.0 neste KPI le como
        # "toda missao autonoma". Aqui HA registros nos dois casos, entao None
        # seria defeito — asserir isso e o que separa "mediu zero" de "nao mediu".
        assert debt_so_historico is not None, "ha 12 registros: NO_DATA seria defeito"
        assert debt_com_execucao_n is not None, "ha registros: NO_DATA seria defeito"
        assert debt_so_historico == 1.0  # 12 de 12 ocorrencias humanas
        assert debt_com_execucao_n < debt_so_historico  # Cognitive Debt caiu (secao 35.4)
