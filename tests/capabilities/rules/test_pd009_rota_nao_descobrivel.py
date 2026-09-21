"""Testes do handler bespoke PD-009 "rota não referenciada em nav ou CTA"
(`pd009_rota_nao_descobrivel.py`)."""

from __future__ import annotations

import json

from batman_os.capabilities.operator import ExecutionContext
from batman_os.capabilities.rules.pd009_rota_nao_descobrivel import avaliar_pd009
from batman_os.foundation.types import MissionId, StepId, TenantId, agora


def _contexto() -> ExecutionContext:
    return ExecutionContext(
        mission_id=MissionId("m-1"),
        tenant_id=TenantId("t-1"),
        step_id=StepId("s-1"),
        deadline=agora(),
    )


def _regra() -> dict[str, object]:
    return {
        "codigo": "PD-009",
        "agente": "product-designer",
        "severidade": "low",
        "categoria": "descobribilidade",
        "titulo": "titulo",
        "causa": "causa",
        "remediacao": "remediacao",
    }


class TestRotaNaoDescobrivel:
    def test_dispara_para_rota_orfa_sem_referencia_em_nav(self) -> None:
        entrada = {
            "caminho": "frontend/src/App.tsx",
            "conteudo": json.dumps(
                {
                    "app_texto": '<Route path="/newsletter" element={<Newsletter />} />',
                    "nav_textos": ["nada relacionado aqui"],
                }
            ),
            "regra": _regra(),
        }
        saida = avaliar_pd009(entrada, _contexto())
        assert len(saida["achados"]) == 1

    def test_nao_dispara_quando_referenciada_em_nav(self) -> None:
        entrada = {
            "caminho": "frontend/src/App.tsx",
            "conteudo": json.dumps(
                {
                    "app_texto": '<Route path="/newsletter" element={<Newsletter />} />',
                    "nav_textos": ["<Link to='/newsletter'>Newsletter</Link>"],
                }
            ),
            "regra": _regra(),
        }
        saida = avaliar_pd009(entrada, _contexto())
        assert saida["achados"] == []

    def test_nao_dispara_sem_rotas_orfas(self) -> None:
        entrada = {
            "caminho": "frontend/src/App.tsx",
            "conteudo": json.dumps({"app_texto": '<Route path="/dashboard" />', "nav_textos": []}),
            "regra": _regra(),
        }
        saida = avaliar_pd009(entrada, _contexto())
        assert saida["achados"] == []

    def test_nao_dispara_sem_app_tsx(self) -> None:
        entrada = {"caminho": "frontend/src/App.tsx", "conteudo": None, "regra": _regra()}
        saida = avaliar_pd009(entrada, _contexto())
        assert saida["achados"] == []
