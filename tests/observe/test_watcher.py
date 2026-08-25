"""Testes do ObserveWatcher — run_once (conjunto exato + entrega via sink),
heartbeat (emite limpo + self-heartbeat a cada N ciclos), run_forever
(best-effort: excecao num ciclo nao derruba o loop)."""

from __future__ import annotations

from collections import Counter

import pytest

from batman_os.foundation.types import TenantId
from batman_os.governance.governance_engine import (
    FonteAlerta,
    GovernanceAlert,
    GovernanceEngine,
    SeveridadeAlerta,
)
from batman_os.observe.snapshot import (
    AmostraAuth,
    AmostraEndpoint,
    AmostraInfra,
    AmostraPortas,
    ContagemNginx,
    ObserveSnapshot,
    PortaEscuta,
)
from batman_os.observe.watcher import ObserveWatcher


class _SinkFake:
    """Registra cada GovernanceAlert entregue (prova de entrega via sink)."""

    def __init__(self) -> None:
        self.entregues: list[GovernanceAlert] = []

    def enviar(self, alert: GovernanceAlert) -> None:
        self.entregues.append(alert)


def _snapshot_rico() -> ObserveSnapshot:
    return ObserveSnapshot(
        tenant_id=TenantId("acme"),
        infra=AmostraInfra(cpu_percent=95.0, ram_percent=50.0, disk_percent=85.0),
        endpoints=[
            AmostraEndpoint(url="https://x/health", ok=False, status=None, error="timeout"),
            AmostraEndpoint(
                url="https://x/api",
                ok=True,
                status=200,
                latencias_janela_ms=[600.0, 700.0, 1200.0],
                headers_presentes=["Strict-Transport-Security"],
            ),
        ],
        portas=AmostraPortas(inesperadas=[PortaEscuta(port=6379, bind="0.0.0.0", service="redis")]),
        processos_rodando=["bash", "xmrig"],
        processos_esperados=["nginx", "gunicorn"],
        nginx=ContagemNginx(total=100, status_5xx=8, por_ip_404={"1.2.3.4": 40}, auth_401_403=20),
        auth=AmostraAuth(
            total_linhas=200, falhas=25, ip_top="9.9.9.9", falhas_por_ip={"9.9.9.9": 25}
        ),
        nginx_config_valida=False,
        nginx_config_detalhe="nginx -t falhou",
    )


class TestRunOnceConjuntoExato:
    def test_produz_exatamente_o_conjunto_esperado(self) -> None:
        gov = GovernanceEngine()
        watcher = ObserveWatcher(gov, tenant_id=TenantId("acme"))

        alertas = watcher.run_once(_snapshot_rico())

        contagem = Counter((a.source.value, a.severity.value) for a in alertas)
        assert contagem == Counter(
            {
                ("endpoint-down", "critical"): 1,  # /health timeout (✗→✓)
                ("endpoint-latency", "critical"): 1,  # p95=1200 >= 1000
                ("endpoint-error-rate", "critical"): 1,  # nginx 8% >= 5%
                ("infra-saturation", "critical"): 1,  # cpu 95 >= 90
                ("infra-saturation", "warning"): 1,  # disk 85 (banda unica, sem crit junto)
                (
                    "security-intrusion",
                    "critical",
                ): 4,  # redis externo, xmrig, nginx invalido, ssh 25 falhas
                ("security-intrusion", "warning"): 3,  # 404 scanner, 401/403, headers
                ("service-down", "critical"): 2,  # nginx + gunicorn ausentes (✗→✓)
            }
        )

    def test_cada_alerta_foi_entregue_no_sink(self) -> None:
        sink = _SinkFake()
        gov = GovernanceEngine(sink=sink)
        watcher = ObserveWatcher(gov, tenant_id=TenantId("acme"))

        alertas = watcher.run_once(_snapshot_rico())

        ids_retornados = {a.id for a in alertas}
        ids_entregues = {a.id for a in sink.entregues}
        assert ids_retornados == ids_entregues
        assert len(sink.entregues) == len(alertas)

    def test_metrica_nao_gera_warning_e_critical_juntos(self) -> None:
        # CPU=95 deve gerar SO um CRITICAL de infra, nunca WARNING+CRITICAL.
        gov = GovernanceEngine()
        watcher = ObserveWatcher(gov, tenant_id=TenantId("acme"))
        snap = ObserveSnapshot(tenant_id=TenantId("acme"), infra=AmostraInfra(cpu_percent=95.0))
        alertas = watcher.run_once(snap)
        infra = [a for a in alertas if a.source == FonteAlerta.INFRA_SATURATION]
        assert len(infra) == 1
        assert infra[0].severity == SeveridadeAlerta.CRITICAL

    def test_snapshot_limpo_nao_gera_alertas(self) -> None:
        gov = GovernanceEngine()
        watcher = ObserveWatcher(gov, tenant_id=TenantId("acme"))
        snap = ObserveSnapshot(
            tenant_id=TenantId("acme"),
            infra=AmostraInfra(cpu_percent=10.0, ram_percent=20.0, disk_percent=30.0),
            endpoints=[
                AmostraEndpoint(url="https://x", ok=True, status=200, latencias_janela_ms=[50.0])
            ],
            nginx=ContagemNginx(total=100, status_5xx=0),
        )
        assert watcher.run_once(snap) == []

    def test_run_once_deterministico_entre_ciclos(self) -> None:
        gov = GovernanceEngine()
        watcher = ObserveWatcher(gov, tenant_id=TenantId("acme"))
        snap = _snapshot_rico()
        c1 = Counter((a.source.value, a.severity.value) for a in watcher.run_once(snap))
        c2 = Counter((a.source.value, a.severity.value) for a in watcher.run_once(snap))
        assert c1 == c2  # nenhum ponto obsoleto acumulado entre ciclos


class TestHeartbeat:
    def test_emite_mesmo_quando_limpo(self) -> None:
        sink = _SinkFake()
        gov = GovernanceEngine(sink=sink)
        watcher = ObserveWatcher(gov, tenant_id=TenantId("acme"))

        alerta = watcher.heartbeat(ObserveSnapshot(tenant_id=TenantId("acme")), houve_alerta=False)

        assert alerta.source == FonteAlerta.OBSERVE_HEARTBEAT
        assert alerta.severity == SeveridadeAlerta.INFO
        assert any("status=tudo-limpo" in linha for linha in alerta.evidence[0].evidencias)
        assert len(sink.entregues) == 1

    def test_heartbeat_de_atencao_quando_ha_alerta(self) -> None:
        gov = GovernanceEngine()
        watcher = ObserveWatcher(gov, tenant_id=TenantId("acme"))
        alerta = watcher.heartbeat(ObserveSnapshot(tenant_id=TenantId("acme")), houve_alerta=True)
        assert any("status=atencao" in linha for linha in alerta.evidence[0].evidencias)

    def test_self_heartbeat_a_cada_n_ciclos(self) -> None:
        sink = _SinkFake()
        gov = GovernanceEngine(sink=sink)
        watcher = ObserveWatcher(gov, tenant_id=TenantId("acme"), ciclos_por_heartbeat=3)

        def coletar() -> ObserveSnapshot:
            return ObserveSnapshot(tenant_id=TenantId("acme"))  # limpo

        watcher.run_forever(intervalo=0.0, coletar=coletar, max_ciclos=6, dormir=lambda s: None)

        hb = [a for a in sink.entregues if a.source == FonteAlerta.OBSERVE_HEARTBEAT]
        assert len(hb) == 2  # ciclos 3 e 6
        # o self-heartbeat prova liveness mesmo com o daemon 100% limpo


class TestRunForeverBestEffort:
    def test_excecao_num_ciclo_nao_derruba_o_loop(self) -> None:
        gov = GovernanceEngine()
        watcher = ObserveWatcher(gov, tenant_id=TenantId("acme"), ciclos_por_heartbeat=100)
        chamadas: list[int] = []

        def coletar() -> ObserveSnapshot:
            chamadas.append(1)
            if len(chamadas) == 2:
                raise RuntimeError("coleta falhou neste ciclo")
            return ObserveSnapshot(
                tenant_id=TenantId("acme"),
                endpoints=[AmostraEndpoint(url="https://x", ok=False, error="down")],
            )

        ciclos = watcher.run_forever(
            intervalo=0.0, coletar=coletar, max_ciclos=3, dormir=lambda s: None
        )

        assert ciclos == 3  # rodou os 3, apesar da excecao no 2o
        assert len(chamadas) == 3
        # os ciclos 1 e 3 (validos) emitiram endpoint-down; o 2o falhou e foi ignorado
        down = gov.get_open_alerts(source=FonteAlerta.ENDPOINT_DOWN)
        assert len(down) == 2

    def test_evento_de_parada_encerra_o_loop(self) -> None:
        gov = GovernanceEngine()
        watcher = ObserveWatcher(gov, tenant_id=TenantId("acme"))

        class _Parada:
            def __init__(self) -> None:
                self._contador = 0

            def is_set(self) -> bool:
                self._contador += 1
                return self._contador > 2

        ciclos = watcher.run_forever(
            intervalo=0.0,
            coletar=lambda: ObserveSnapshot(tenant_id=TenantId("acme")),
            parar=_Parada(),
            dormir=lambda s: None,
        )
        assert ciclos <= 2

    def test_dormir_e_chamado_entre_ciclos(self) -> None:
        gov = GovernanceEngine()
        watcher = ObserveWatcher(gov, tenant_id=TenantId("acme"), ciclos_por_heartbeat=100)
        sonos: list[float] = []
        watcher.run_forever(
            intervalo=1.5,
            coletar=lambda: ObserveSnapshot(tenant_id=TenantId("acme")),
            max_ciclos=2,
            dormir=lambda s: sonos.append(s),
        )
        # dorme entre ciclos, mas nao apos o ultimo
        assert sonos == [1.5]


class TestRastroLocalDoObserve:
    """`ALERTA_CPU_LATENCIA_SEM_CARIMBO01` — o daemon alertava e não deixava
    rastro NENHUM na máquina.

    Medido na VPS em 2026-08-12: o processo estava vivo (pid 1021816, 60 dias
    de uptime), e `/var/log/<app>/batman_os_observe.log` tinha **0 bytes desde
    23/07**. A causa é esta classe: `run_forever` só logava em EXCEÇÃO, então
    20 dias sem exceção produzem um arquivo vazio — e arquivo vazio é
    indistinguível de "o daemon nunca subiu".

    A consequência prática foi o card não poder ser respondido: chegaram
    CRITICALs de `CPU>90%` e `latência p95>1000ms` ao Discord, e ao investigar
    não havia **quando**. Sem carimbo não dá para separar as duas hipóteses —
    pico real numa janela de cron pesado, ou limiar mal calibrado.

    O volume é deliberadamente contido: `/var/log/<app>` **não tem logrotate**
    (medido: 548 MB, arquivos de até 174 MB), então logar 1.440 linhas/dia
    trocaria um crescimento sem teto por outro. Loga-se o que é informativo —
    o alerta, e o ciclo em que alguma métrica entra em banda.
    """

    def test_alerta_deixa_carimbo_no_log(self, caplog: pytest.LogCaptureFixture) -> None:
        sink = _SinkFake()
        watcher = ObserveWatcher(GovernanceEngine(sink=sink), tenant_id=TenantId("acme"))
        snap = ObserveSnapshot(tenant_id=TenantId("acme"), infra=AmostraInfra(cpu_percent=95.0))

        with caplog.at_level("INFO", logger="batman_os.observe.watcher"):
            alertas = watcher.run_once(snap)

        assert alertas, "o cenário precisa produzir alerta para o teste valer"
        texto = caplog.text
        assert "observe.cpu" in texto, "o log tem de nomear a métrica que disparou"
        assert "95" in texto, "o log tem de trazer o VALOR medido, não só o nome"

    def test_ciclo_com_metrica_em_banda_registra_a_leitura(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Banda de WARNING (80–90) não gera CRITICAL, mas é exatamente a
        faixa que responde 'o limiar está mal calibrado?'."""
        sink = _SinkFake()
        watcher = ObserveWatcher(GovernanceEngine(sink=sink), tenant_id=TenantId("acme"))
        snap = ObserveSnapshot(tenant_id=TenantId("acme"), infra=AmostraInfra(cpu_percent=85.0))

        with caplog.at_level("INFO", logger="batman_os.observe.watcher"):
            watcher.run_once(snap)

        assert "observe.cpu" in caplog.text
        assert "85" in caplog.text

    def test_maquina_ociosa_nao_polui_o_log(self, caplog: pytest.LogCaptureFixture) -> None:
        """A trava contra trocar um crescimento sem teto por outro: com tudo
        abaixo do limiar de WARNING, o ciclo não escreve linha de métrica.
        Na VPS medida (load 0.01, CPU muito abaixo de 80) isso é o caso
        esmagadoramente comum — 1.440 ciclos/dia."""
        sink = _SinkFake()
        watcher = ObserveWatcher(GovernanceEngine(sink=sink), tenant_id=TenantId("acme"))
        snap = ObserveSnapshot(tenant_id=TenantId("acme"), infra=AmostraInfra(cpu_percent=3.0))

        with caplog.at_level("INFO", logger="batman_os.observe.watcher"):
            watcher.run_once(snap)

        assert "observe.cpu" not in caplog.text
