"""A matriz de roteamento — por fonte, e por severidade onde a fonte e grossa.

Por que este arquivo existe
---------------------------
Medido em 2026-09-05, no journal de producao:

    performance   158 entregues     95% de tudo
    infra           6 entregues     todos falso positivo de CPU
    security        2 entregues   contra 2.046 SUPRIMIDOS
    log             2 entregues

O canal de seguranca recebia enxurrada de ruido esperado — portas de manutencao
do n8n e do monarx em loopback — porque as OITO regras de seguranca do observe
emitem todas a mesma `SECURITY_INTRUSION`. Rotear so por fonte nao distingue
"porta do n8n, prevista" de "brute-force SSH em curso", e canal de seguranca que
carrega ruido e canal que se aprende a ignorar.

A severidade discrimina, e foi conferida regra a regra antes de virar codigo:
porta esperada sai LOW->INFO; ssh_bruteforce e processo_suspeito saem CRITICAL;
bruteforce_404, nao_autorizado, headers_ausentes e endpoint_exposto saem
MEDIUM/HIGH->WARNING.

Ajustes do DEV que este arquivo trava:
  * evento esperado de manutencao NUNCA vira "intrusao" nem inunda canal;
  * falha do MONITOR nao se confunde com falha do produto;
  * nginx invalido e operacional (#infra); nginx ALTERADO e ameaca (#cyber);
  * nenhum evento vai a dois canais;
  * cada evento tem canal padrao e fallback.
"""

from __future__ import annotations

import pytest

from batman_os.foundation.types import Evidence, TenantId
from batman_os.governance.alert_sinks import (
    CANAL_CYBER,
    CANAL_SEGURANCA_LEGADO,
    SOMENTE_JOURNAL,
    DiscordAlertSink,
    canal_do_alerta,
)
from batman_os.governance.governance_engine import (
    FonteAlerta,
    GovernanceAlert,
    SeveridadeAlerta,
)


def _alerta(
    fonte: FonteAlerta,
    severidade: SeveridadeAlerta = SeveridadeAlerta.WARNING,
    origem: str = "observe:generico",
) -> GovernanceAlert:
    return GovernanceAlert(
        source=fonte,
        severity=severidade,
        evidence=[Evidence(origem=origem, evidencias=["linha"])],
        related_tenant_id=TenantId("t"),
    )


class TestAmeacaVaiParaCyber:
    @pytest.mark.parametrize(
        "origem",
        [
            "observe:ssh-bruteforce",
            "observe:ssh-auth-bypass",
            "observe:bruteforce-404",
            "observe:nao-autorizado",
            "observe:porta-inesperada",
            "observe:processo-suspeito",
            "observe:endpoint-exposto",
            "observe:headers-ausentes",
            "observe:nginx-mudanca",
        ],
    )
    def test_seguranca_acionavel_vai_para_cyber(self, origem: str) -> None:
        alerta = _alerta(FonteAlerta.SECURITY_INTRUSION, SeveridadeAlerta.CRITICAL, origem)

        assert canal_do_alerta(alerta) == CANAL_CYBER

    def test_isolamento_de_tenant_tambem_e_ameaca(self) -> None:
        alerta = _alerta(FonteAlerta.TENANT_ISOLATION_INCIDENT, SeveridadeAlerta.CRITICAL)

        assert canal_do_alerta(alerta) == CANAL_CYBER


class TestManutencaoNaoEhIntrusao:
    """O ajuste mais importante do DEV: 2.046 supressões vinham daqui."""

    def test_porta_de_manutencao_nao_vai_a_canal_nenhum(self) -> None:
        alerta = _alerta(
            FonteAlerta.SECURITY_INTRUSION, SeveridadeAlerta.INFO, "observe:porta-manutencao"
        )

        assert canal_do_alerta(alerta) is SOMENTE_JOURNAL

    def test_nem_mesmo_se_a_severidade_subir(self) -> None:
        """A origem manda: manutenção declarada não vira ameaça por
        classificação de severidade de outra regra."""
        alerta = _alerta(
            FonteAlerta.SECURITY_INTRUSION, SeveridadeAlerta.CRITICAL, "observe:porta-manutencao"
        )

        assert canal_do_alerta(alerta) is SOMENTE_JOURNAL

    def test_info_de_seguranca_generico_tambem_fica_no_journal(self) -> None:
        """INFO repetitivo não inunda canal — vai para journal e resumo."""
        alerta = _alerta(FonteAlerta.SECURITY_INTRUSION, SeveridadeAlerta.INFO)

        assert canal_do_alerta(alerta) is SOMENTE_JOURNAL

    def test_o_sink_nao_entrega_o_que_e_somente_journal(self) -> None:
        registrados: list[tuple[str, dict[str, object]]] = []

        class _T:
            def postar(self, url: str, payload: dict[str, object]) -> str | None:
                registrados.append((url, payload))
                return str(len(registrados))

            # O fake espelha a interface real (SAIDA-004); estes testes olham
            # roteamento, entao `editar` nao precisa fazer nada.
            def editar(self, url: str, message_id: str, payload: dict[str, object]) -> None:
                return None

        sink = DiscordAlertSink(
            webhooks_por_canal={"cyber": "https://d.test/cyber", "log": "https://d.test/log"},
            transporte=_T(),
        )
        sink.enviar(
            _alerta(
                FonteAlerta.SECURITY_INTRUSION,
                SeveridadeAlerta.INFO,
                "observe:porta-manutencao",
            )
        )

        assert registrados == [], "manutenção esperada não vira mensagem"


class TestOperacionalNaoEhAmeaca:
    def test_nginx_invalido_vai_para_infra(self) -> None:
        """Configuração quebrada é problema operacional — não ataque."""
        alerta = _alerta(
            FonteAlerta.SECURITY_INTRUSION, SeveridadeAlerta.CRITICAL, "observe:nginx-invalida"
        )

        assert canal_do_alerta(alerta) == "infra"

    def test_nginx_ALTERADO_continua_ameaca(self) -> None:
        """A exceção que o DEV pediu: evidência concreta de alteração fica no
        canal de ameaça."""
        alerta = _alerta(
            FonteAlerta.SECURITY_INTRUSION, SeveridadeAlerta.CRITICAL, "observe:nginx-mudanca"
        )

        assert canal_do_alerta(alerta) == CANAL_CYBER

    def test_servico_caido_vai_para_infra(self) -> None:
        alerta = _alerta(
            FonteAlerta.SECURITY_INTRUSION, SeveridadeAlerta.CRITICAL, "observe:servico-caido"
        )

        assert canal_do_alerta(alerta) == "infra"


class TestFalhaDoMonitorNaoEhFalhaDoProduto:
    def test_monitor_cego_vai_para_log(self) -> None:
        assert canal_do_alerta(_alerta(FonteAlerta.MONITOR_CEGO)) == "log"

    def test_heartbeat_vai_para_log(self) -> None:
        assert canal_do_alerta(_alerta(FonteAlerta.OBSERVE_HEARTBEAT)) == "log"

    def test_cegueira_nunca_vai_para_cyber(self) -> None:
        """Falta de permissão em arquivo não é ataque, e mandá-la ao canal de
        ameaça faria caçar um atacante que pode não existir."""
        assert canal_do_alerta(_alerta(FonteAlerta.MONITOR_CEGO)) != CANAL_CYBER


class TestDisponibilidade:
    @pytest.mark.parametrize(
        "fonte",
        [
            FonteAlerta.FEATURE_DOWN,
            FonteAlerta.FEATURE_RECOVERED,
            FonteAlerta.ENDPOINT_DOWN,
            FonteAlerta.ENDPOINT_LATENCY,
            FonteAlerta.SLA_BREACH,
            FonteAlerta.LLM_CIRCUIT_BREAKER,
        ],
    )
    def test_vao_para_performance(self, fonte: FonteAlerta) -> None:
        assert canal_do_alerta(_alerta(fonte)) == "performance"

    @pytest.mark.parametrize(
        "fonte",
        [
            FonteAlerta.INFRA_SATURATION,
            FonteAlerta.SERVICE_DOWN,
            FonteAlerta.DATA_SOURCE_STALE,
        ],
    )
    def test_vao_para_infra(self, fonte: FonteAlerta) -> None:
        assert canal_do_alerta(_alerta(fonte)) == "infra"


class TestFallbackECanalUnico:
    def test_cyber_cai_no_canal_antigo_enquanto_nao_existir(self) -> None:
        """Fallback explícito: enquanto #cyber-security não estiver
        provisionado, a ameaça vai ao canal antigo em vez de sumir."""
        enviados: list[str] = []

        class _T:
            def postar(self, url: str, payload: dict[str, object]) -> str | None:
                enviados.append(url)
                return str(len(enviados))

            def editar(self, url: str, message_id: str, payload: dict[str, object]) -> None:
                return None

        sink = DiscordAlertSink(
            webhooks_por_canal={CANAL_SEGURANCA_LEGADO: "https://d.test/security"},
            transporte=_T(),
        )
        sink.enviar(
            _alerta(
                FonteAlerta.SECURITY_INTRUSION,
                SeveridadeAlerta.CRITICAL,
                "observe:ssh-bruteforce",
            )
        )

        assert enviados == ["https://d.test/security"]

    def test_nunca_entrega_nos_dois_canais(self) -> None:
        """Duplicar entre #security e #cyber-security treina a ignorar ambos."""
        enviados: list[str] = []

        class _T:
            def postar(self, url: str, payload: dict[str, object]) -> str | None:
                enviados.append(url)
                return str(len(enviados))

            def editar(self, url: str, message_id: str, payload: dict[str, object]) -> None:
                return None

        sink = DiscordAlertSink(
            webhooks_por_canal={
                CANAL_CYBER: "https://d.test/cyber",
                CANAL_SEGURANCA_LEGADO: "https://d.test/security",
            },
            transporte=_T(),
        )
        sink.enviar(
            _alerta(
                FonteAlerta.SECURITY_INTRUSION,
                SeveridadeAlerta.CRITICAL,
                "observe:ssh-bruteforce",
            )
        )

        assert enviados == ["https://d.test/cyber"], "um destino, nunca dois"

    def test_fonte_desconhecida_cai_no_padrao(self) -> None:
        assert canal_do_alerta(_alerta(FonteAlerta.RULE_DRIFT), "log") == "log"
