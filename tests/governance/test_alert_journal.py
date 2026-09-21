"""Testes do journal local de alertas — ZERO rede, ZERO Discord.

Por que este arquivo existe
---------------------------
Medido em 2026-09-04: o daemon `observe` rodava havia 8 dias com o log em ZERO
byte desde 23/07. Ele alertava e nao registrava nada. A pergunta do DEV — *"que
alertas sairam hoje?"* — so pode ser respondida em um comando se cada desfecho
da entrega virar uma linha estruturada, e e isso que estes testes prendem.

O teste central e `test_suprimido_pela_janela_tambem_e_registrado`: e o desfecho
que o codigo anterior mandava para um `logger.debug` que nao chegava a lugar
nenhum, e e justamente o que distingue *o problema parou* de *o problema
continua e a janela o calou*.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from batman_os.foundation.types import Evidence, TenantId
from batman_os.governance import alert_journal
from batman_os.governance.alert_sinks import DiscordAlertSink
from batman_os.governance.governance_engine import (
    FonteAlerta,
    GovernanceAlert,
    SeveridadeAlerta,
)

WEBHOOK = "https://discord.test/webhook/global"


def _alerta(
    source: FonteAlerta = FonteAlerta.FEATURE_DOWN,
    severity: SeveridadeAlerta = SeveridadeAlerta.CRITICAL,
    origem: str = "feature-monitor:syn-api-health",
    evidencias: list[str] | None = None,
    tenant: TenantId | None = None,
) -> GovernanceAlert:
    return GovernanceAlert(
        source=source,
        severity=severity,
        evidence=[Evidence(origem=origem, evidencias=evidencias or ["HTTP 522 em 19.6s"])],
        related_tenant_id=tenant,
    )


class _TransporteFake:
    def __init__(self, falhar: bool = False) -> None:
        self.chamadas: list[tuple[str, dict[str, Any]]] = []
        self.edicoes: list[tuple[str, dict[str, Any]]] = []
        self._falhar = falhar

    def postar(self, webhook_url: str, payload: dict[str, Any]) -> str | None:
        if self._falhar:
            raise ConnectionError("webhook fora do ar")
        self.chamadas.append((webhook_url, payload))
        return str(len(self.chamadas))

    # ⚠️ O fake espelha a interface REAL. Desde SAIDA-004 o transporte tem
    # `editar`, porque a recuperacao REESCREVE a mensagem de abertura em vez de
    # postar uma nova. Fake sem o metodo deixa o caminho de producao sem
    # cobertura de tipo -- e foi assim que o Protocol e o fake divergiram.
    def editar(self, webhook_url: str, message_id: str, payload: dict[str, Any]) -> None:
        if self._falhar:
            raise ConnectionError("webhook fora do ar")
        self.edicoes.append((message_id, payload))


def _linhas(caminho: Path) -> list[dict[str, Any]]:
    if not caminho.exists():
        return []
    return [json.loads(ln) for ln in caminho.read_text(encoding="utf-8").splitlines() if ln.strip()]


class TestEventoEstruturado:
    def test_campos_pedidos_pelo_dev_estao_todos_presentes(self) -> None:
        """Contrato de forma: consumidor que espera um campo nao pode receber
        `KeyError` quando o alerta nao tiver o valor — o campo sai `null`."""
        ev = alert_journal.evento(_alerta(), outcome="entregue", notification_sent=True)
        for campo in (
            "timestamp",
            "alert_id",
            "component",
            "check",
            "severity",
            "status",
            "measured_value",
            "threshold",
            "unit",
            "error_type",
            "target",
            "notification_sent",
            "incident_key",
            "outcome",
            "fingerprint",
            "evidence",
        ):
            assert campo in ev, f"campo '{campo}' sumiu do evento"

    def test_traduz_origem_para_componente_e_check(self) -> None:
        ev = alert_journal.evento(
            _alerta(origem="feature-monitor:syn-iris-chat"),
            outcome="entregue",
            notification_sent=True,
        )
        assert ev["component"] == "synthetic-monitor"
        assert ev["check"] == "syn-iris-chat"

    def test_queda_e_recuperacao_do_mesmo_alvo_compartilham_incident_key(self) -> None:
        """O que liga comeco e fim do incidente.

        Sem isto o registro responde *o que caiu* e nunca *por quanto tempo
        ficou caido* — e foi essa a pergunta que nao teve resposta em 04/09.
        """
        caiu = _alerta(source=FonteAlerta.FEATURE_DOWN, origem="feature-monitor:syn-auth-login")
        voltou = _alerta(
            source=FonteAlerta.FEATURE_RECOVERED,
            severity=SeveridadeAlerta.INFO,
            origem="feature-monitor:syn-auth-login",
        )
        assert alert_journal.incident_key(caiu) == alert_journal.incident_key(voltou)
        ev_caiu = alert_journal.evento(caiu, outcome="entregue", notification_sent=True)
        ev_voltou = alert_journal.evento(voltou, outcome="entregue", notification_sent=True)
        assert ev_caiu["status"] == "firing"
        assert ev_voltou["status"] == "resolved"

    def test_alvos_diferentes_nao_colidem(self) -> None:
        """CONTROLE do teste acima: se tudo compartilhasse chave, o teste de
        cima passaria sem provar nada."""
        a = _alerta(origem="feature-monitor:syn-auth-login")
        b = _alerta(origem="feature-monitor:syn-iris-chat")
        assert alert_journal.incident_key(a) != alert_journal.incident_key(b)


class TestOsQuatroDesfechos:
    """Tres dos quatro eram silenciosos. Nenhum pode voltar a ser."""

    def test_entregue(self, tmp_path: Path) -> None:
        jrn = tmp_path / "alertas.jsonl"
        t = _TransporteFake()
        sink = DiscordAlertSink(webhook_global=WEBHOOK, transporte=t, caminho_journal=jrn)
        sink.enviar(_alerta())
        linhas = _linhas(jrn)
        assert len(linhas) == 1
        assert linhas[0]["outcome"] == "entregue"
        assert linhas[0]["notification_sent"] is True

    def test_sem_canal_deixa_de_ser_no_op_silencioso(self, tmp_path: Path) -> None:
        jrn = tmp_path / "alertas.jsonl"
        DiscordAlertSink(transporte=_TransporteFake(), caminho_journal=jrn).enviar(_alerta())
        linhas = _linhas(jrn)
        assert len(linhas) == 1, "alerta sem webhook sumiu sem deixar rastro — o defeito original"
        assert linhas[0]["outcome"] == "sem_canal"
        assert linhas[0]["notification_sent"] is False

    def test_falha_de_transporte_e_registrada(self, tmp_path: Path) -> None:
        jrn = tmp_path / "alertas.jsonl"
        DiscordAlertSink(
            webhook_global=WEBHOOK, transporte=_TransporteFake(falhar=True), caminho_journal=jrn
        ).enviar(_alerta())
        linhas = _linhas(jrn)
        assert len(linhas) == 1
        assert linhas[0]["outcome"] == "falha_de_transporte"
        assert linhas[0]["notification_sent"] is False
        assert "ConnectionError" in (linhas[0]["detail"] or "")

    def test_suprimido_pela_janela_tambem_e_registrado(self, tmp_path: Path) -> None:
        """O desfecho que mais importa, e o que so existia num `logger.debug`.

        Um incidente ativo ha 6 h e um resolvido produzem o MESMO silencio no
        Discord. So o journal os separa.
        """
        jrn = tmp_path / "alertas.jsonl"
        t = _TransporteFake()
        sink = DiscordAlertSink(
            webhook_global=WEBHOOK,
            transporte=t,
            caminho_journal=jrn,
            caminho_estado=tmp_path / "dedup.json",
        )
        alerta = _alerta()
        sink.enviar(alerta)
        sink.enviar(alerta)  # identico, dentro da janela -> suprimido

        assert len(t.chamadas) == 1, "o throttle parou de funcionar"
        linhas = _linhas(jrn)
        assert [ln["outcome"] for ln in linhas] == ["entregue", "suprimido_por_janela"]
        assert linhas[1]["notification_sent"] is False
        assert linhas[0]["fingerprint"] == linhas[1]["fingerprint"], (
            "o mesmo problema precisa carregar o mesmo fingerprint — e assim que se "
            "conta 'uma causa repetida N vezes' em vez de 'N problemas'"
        )


class TestRobustez:
    def test_journal_quebrado_nunca_derruba_a_entrega(self, tmp_path: Path) -> None:
        """A entrega promete nao levantar. Um journal que quebrasse isso
        trocaria cegueira por mudez — e mudez e pior."""
        t = _TransporteFake()
        # caminho impossivel: um ARQUIVO no lugar do diretorio pai
        arquivo = tmp_path / "bloqueio"
        arquivo.write_text("nao sou diretorio", encoding="utf-8")
        sink = DiscordAlertSink(
            webhook_global=WEBHOOK, transporte=t, caminho_journal=arquivo / "alertas.jsonl"
        )

        sink.enviar(_alerta())  # nao pode levantar

        assert len(t.chamadas) == 1, "a entrega ao Discord foi perdida por causa do journal"

    def test_append_only_nunca_reescreve(self, tmp_path: Path) -> None:
        """Processo-frio: cada ciclo do cron e um processo novo. Se o journal
        truncasse, o historico do dia sumiria a cada 5 minutos."""
        jrn = tmp_path / "alertas.jsonl"
        for i in range(3):
            alert_journal.registrar({"n": i}, caminho=jrn)
        assert [ln["n"] for ln in _linhas(jrn)] == [0, 1, 2]

    def test_registrar_devolve_falso_em_vez_de_levantar(self, tmp_path: Path) -> None:
        arquivo = tmp_path / "bloqueio"
        arquivo.write_text("nao sou diretorio", encoding="utf-8")
        assert alert_journal.registrar({"a": 1}, caminho=arquivo / "x.jsonl") is False


class TestCaminho:
    def test_variavel_de_ambiente_manda(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        alvo = tmp_path / "de-producao.jsonl"
        monkeypatch.setenv(alert_journal.ENV_CAMINHO, str(alvo))
        assert alert_journal.caminho_do_ambiente() == alvo

    def test_sem_variavel_usa_o_default_ao_lado_do_dedup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(alert_journal.ENV_CAMINHO, raising=False)
        assert alert_journal.caminho_do_ambiente() == alert_journal.CAMINHO_PADRAO
