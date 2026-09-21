"""Testes do handler bespoke FE-007 "NAV_LOCK01 — item canônico de
navegação removido" (`fe007_nav_lock.py`)."""

from __future__ import annotations

from batman_os.capabilities.operator import ExecutionContext
from batman_os.capabilities.rules.fe007_nav_lock import avaliar_fe007
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
        "codigo": "FE-007",
        "agente": "frontend-engineer",
        "severidade": "high",
        "categoria": "regressao",
        "titulo": "titulo",
        "causa": "causa",
        "remediacao": "remediacao",
    }


_NAV_VIEWER_COMPLETO = (
    "const NAV_VIEWER = [\n"
    "  { to: '/investidor' },\n"
    "  { to: '/mercado' },\n"
    "  { to: '/oportunidades' },\n"
    "  { to: '/simulacoes' },\n"
    "  { to: '/newsletter' },\n"
    "];\n"
)


class TestNavLock:
    def test_dispara_quando_rota_canonica_removida(self) -> None:
        entrada = {
            "caminho": "frontend/src/components/Layout.tsx",
            "conteudo": (
                "const NAV_VIEWER = [\n  { to: '/investidor' },\n  { to: '/mercado' },\n];\n"
            ),
            "regra": _regra(),
        }
        saida = avaliar_fe007(entrada, _contexto())
        assert len(saida["achados"]) == 1
        assert saida["achados"][0]["chave"].startswith("NAV_VIEWER:")

    def test_nao_dispara_quando_todas_rotas_presentes(self) -> None:
        entrada = {
            "caminho": "frontend/src/components/Layout.tsx",
            "conteudo": _NAV_VIEWER_COMPLETO,
            "regra": _regra(),
        }
        saida = avaliar_fe007(entrada, _contexto())
        assert saida["achados"] == []

    def test_nao_dispara_quando_bloco_ausente(self) -> None:
        entrada = {
            "caminho": "frontend/src/components/Layout.tsx",
            "conteudo": "export default function Layout() { return null; }\n",
            "regra": _regra(),
        }
        saida = avaliar_fe007(entrada, _contexto())
        assert saida["achados"] == []

    def test_nao_dispara_quando_arquivo_nao_existe(self) -> None:
        entrada = {
            "caminho": "frontend/src/components/Layout.tsx",
            "conteudo": None,
            "regra": _regra(),
        }
        saida = avaliar_fe007(entrada, _contexto())
        assert saida["achados"] == []

    def test_produz_multiplos_achados_para_multiplos_blocos_com_falta(self) -> None:
        conteudo = (
            "const NAV_VIEWER = [\n  { to: '/investidor' },\n];\n"
            "const NAV_ADMIN = [\n  { to: '/admin' },\n];\n"
        )
        entrada = {
            "caminho": "frontend/src/components/Layout.tsx",
            "conteudo": conteudo,
            "regra": _regra(),
        }
        saida = avaliar_fe007(entrada, _contexto())
        assert len(saida["achados"]) == 2
        chaves = {a["chave"] for a in saida["achados"]}
        assert any(c.startswith("NAV_VIEWER:") for c in chaves)
        assert any(c.startswith("NAV_ADMIN:") for c in chaves)
