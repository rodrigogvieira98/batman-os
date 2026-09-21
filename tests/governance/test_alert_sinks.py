"""Testes do DiscordAlertSink e da integracao com o GovernanceEngine —
100% com transporte fake, ZERO rede."""

from __future__ import annotations

from typing import Any

from batman_os.foundation.types import Evidence, TenantId
from batman_os.governance.alert_sinks import DiscordAlertSink
from batman_os.governance.governance_engine import (
    FonteAlerta,
    GovernanceAlert,
    GovernanceEngine,
    SeveridadeAlerta,
    StatusAlerta,
)

WEBHOOK = "https://discord.test/webhook/global"


def _alerta(
    source: FonteAlerta = FonteAlerta.SLA_BREACH,
    severity: SeveridadeAlerta = SeveridadeAlerta.WARNING,
    evidencias: list[str] | None = None,
    tenant: TenantId | None = None,
) -> GovernanceAlert:
    return GovernanceAlert(
        source=source,
        severity=severity,
        evidence=[Evidence(origem="observability", evidencias=evidencias or ["p95=1200ms"])],
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


class TestEnvioBasico:
    def test_titulo_e_humano_e_o_jargao_vai_para_o_detalhe(self) -> None:
        """Ordem do DEV, 2026-09-05: o titulo diz o que aconteceu.

        Antes era `🔴 [CRITICAL] sla-breach` -- severidade em caixa alta e
        identificador interno, ilegivel para quem nao escreveu o codigo. A
        severidade continua legivel pelo EMOJI, e `sla-breach` nao some: desce
        para o campo de detalhe tecnico, onde serve a quem for investigar.
        """
        t = _TransporteFake()
        sink = DiscordAlertSink(webhook_global=WEBHOOK, transporte=t)

        sink.enviar(_alerta(severity=SeveridadeAlerta.CRITICAL))

        assert len(t.chamadas) == 1
        url, payload = t.chamadas[0]
        assert url == WEBHOOK
        embed = payload["embeds"][0]
        assert embed["title"].startswith("🔴"), "severidade continua legivel no emoji"
        assert "CRITICAL" not in embed["title"]
        assert "sla-breach" not in embed["title"]
        nomes = [c["name"] for c in embed["fields"]]
        assert nomes[0] == "O que aconteceu"
        assert any("Detalhe técnico" in n for n in nomes)
        detalhe = " ".join(c["value"] for c in embed["fields"])
        assert "p95=1200ms" in detalhe, "a evidencia tecnica nao pode se perder"

    def test_o_embed_responde_as_cinco_perguntas(self) -> None:
        """Estado, impacto e acao sao o que torna o alerta acionavel por quem
        nao e da engenharia -- e o que nenhum alerta trazia antes."""
        t = _TransporteFake()
        sink = DiscordAlertSink(webhook_global=WEBHOOK, transporte=t)

        sink.enviar(_alerta(severity=SeveridadeAlerta.CRITICAL))

        nomes = [c["name"] for c in t.chamadas[0][1]["embeds"][0]["fields"]]
        for esperado in ("O que aconteceu", "Estado do serviço", "Ação recomendada", "Impacto"):
            assert esperado in nomes, f"falta '{esperado}' no embed"

    def test_footer_nao_vaza_hostname_so_tenant(self) -> None:
        t = _TransporteFake()
        sink = DiscordAlertSink(webhook_global=WEBHOOK, transporte=t)

        sink.enviar(_alerta(tenant=TenantId("acme")))

        footer = t.chamadas[0][1]["embeds"][0]["footer"]["text"]
        assert "tenant=acme" in footer
        assert "hostname" not in footer.lower()


class TestMention:
    def test_critical_usa_here_por_padrao_nao_everyone(self) -> None:
        t = _TransporteFake()
        sink = DiscordAlertSink(webhook_global=WEBHOOK, transporte=t)

        sink.enviar(_alerta(severity=SeveridadeAlerta.CRITICAL))

        assert t.chamadas[0][1]["content"] == "@here"

    def test_everyone_so_para_fonte_na_allowlist(self) -> None:
        t = _TransporteFake()
        sink = DiscordAlertSink(
            webhook_global=WEBHOOK,
            transporte=t,
            fontes_everyone=frozenset({FonteAlerta.TENANT_ISOLATION_INCIDENT}),
        )

        sink.enviar(
            _alerta(
                source=FonteAlerta.TENANT_ISOLATION_INCIDENT, severity=SeveridadeAlerta.CRITICAL
            )
        )

        assert t.chamadas[0][1]["content"] == "@everyone"

    def test_warning_sem_mention(self) -> None:
        t = _TransporteFake()
        sink = DiscordAlertSink(webhook_global=WEBHOOK, transporte=t)

        sink.enviar(_alerta(severity=SeveridadeAlerta.WARNING))

        assert t.chamadas[0][1]["content"] == ""


class TestDedupePorEstado:
    def test_alerta_identico_e_suprimido(self) -> None:
        t = _TransporteFake()
        sink = DiscordAlertSink(webhook_global=WEBHOOK, transporte=t)

        sink.enviar(_alerta(evidencias=["ARCH-007 aberto"]))
        sink.enviar(_alerta(evidencias=["ARCH-007 aberto"]))  # conteudo identico
        sink.enviar(_alerta(evidencias=["ARCH-007 aberto"]))

        assert len(t.chamadas) == 1  # 2o e 3o suprimidos (fix do ruido diario)

    def test_mudanca_de_conteudo_passa(self) -> None:
        t = _TransporteFake()
        sink = DiscordAlertSink(webhook_global=WEBHOOK, transporte=t)

        sink.enviar(_alerta(evidencias=["ARCH-007 aberto ha 1 dia"]))
        sink.enviar(_alerta(evidencias=["ARCH-007 aberto ha 2 dias"]))  # mudou

        assert len(t.chamadas) == 2

    def test_dedupe_e_por_source_e_tenant(self) -> None:
        t = _TransporteFake()
        sink = DiscordAlertSink(
            webhooks_por_tenant={
                TenantId("a"): "https://d.test/a",
                TenantId("b"): "https://d.test/b",
            },
            transporte=t,
        )

        sink.enviar(_alerta(evidencias=["x"], tenant=TenantId("a")))
        sink.enviar(_alerta(evidencias=["x"], tenant=TenantId("b")))  # outro tenant, passa

        assert len(t.chamadas) == 2
        assert {c[0] for c in t.chamadas} == {"https://d.test/a", "https://d.test/b"}

    def test_falha_de_envio_permite_reenvio(self) -> None:
        t_falho = _TransporteFake(falhar=True)
        sink = DiscordAlertSink(webhook_global=WEBHOOK, transporte=t_falho)

        sink.enviar(_alerta(evidencias=["y"]))  # falha, nao marca assinatura

        t_ok = _TransporteFake()
        sink._transporte = t_ok  # noqa: SLF001
        sink.enviar(_alerta(evidencias=["y"]))  # mesmo conteudo, mas antes falhou

        assert len(t_ok.chamadas) == 1


class TestThrottleDiarioEPersistencia:
    """O fix do flood (2026-07-23): dedup PERSISTIDO com janela diaria — cada
    run do cron do monitor e um processo NOVO, e sem disco o estado nascia
    vazio e re-alertava a Iris caida todo ciclo de 5min."""

    def test_persistencia_entre_processos_suprime(self, tmp_path: Any) -> None:
        caminho = tmp_path / "dedup.json"
        t1 = _TransporteFake()
        sink1 = DiscordAlertSink(webhook_global=WEBHOOK, transporte=t1, caminho_estado=caminho)
        sink1.enviar(_alerta(source=FonteAlerta.FEATURE_DOWN, evidencias=["iris chat down"]))
        assert len(t1.chamadas) == 1
        assert caminho.exists()

        # processo NOVO (proximo tick do cron): outro sink, MESMO arquivo
        t2 = _TransporteFake()
        sink2 = DiscordAlertSink(webhook_global=WEBHOOK, transporte=t2, caminho_estado=caminho)
        sink2.enviar(_alerta(source=FonteAlerta.FEATURE_DOWN, evidencias=["iris chat down"]))
        assert len(t2.chamadas) == 0  # suprimido pelo estado em disco

    def test_janela_zero_desliga_throttle(self) -> None:
        t = _TransporteFake()
        # janela 0 para a severidade WARNING (o _alerta default) => sempre reenvia
        sink = DiscordAlertSink(
            webhook_global=WEBHOOK,
            transporte=t,
            janelas_por_severidade={SeveridadeAlerta.WARNING: 0.0},
        )
        sink.enviar(_alerta(evidencias=["x"]))
        sink.enviar(_alerta(evidencias=["x"]))
        assert len(t.chamadas) == 2

    def test_transicao_de_estado_passa_dentro_da_janela(self, tmp_path: Any) -> None:
        caminho = tmp_path / "dedup.json"
        t = _TransporteFake()
        sink = DiscordAlertSink(webhook_global=WEBHOOK, transporte=t, caminho_estado=caminho)
        sink.enviar(_alerta(source=FonteAlerta.FEATURE_DOWN, evidencias=["iris down"]))
        # recuperacao = OUTRA fonte/assinatura => passa mesmo recem-enviado
        sink.enviar(_alerta(source=FonteAlerta.FEATURE_RECOVERED, evidencias=["iris ok"]))
        assert len(t.chamadas) == 2

    def test_reenvio_apos_expirar_janela(self, tmp_path: Any) -> None:
        caminho = tmp_path / "dedup.json"
        t = _TransporteFake()
        sink = DiscordAlertSink(webhook_global=WEBHOOK, transporte=t, caminho_estado=caminho)
        sink.enviar(_alerta(evidencias=["repetido"]))
        # simula o tempo passando alem da janela mexendo no ts persistido
        chave = next(iter(sink._estado))  # noqa: SLF001
        sink._estado[chave]["ts"] -= 100.0  # noqa: SLF001
        # janela da severidade WARNING (o _alerta default) agora < idade => reenvia
        sink._janelas[SeveridadeAlerta.WARNING] = 10.0  # noqa: SLF001
        sink.enviar(_alerta(evidencias=["repetido"]))
        assert len(t.chamadas) == 2

    def test_dois_alertas_mesmo_source_nao_se_sobrescrevem(self, tmp_path: Any) -> None:
        # Portas 5678 e 5679: MESMO source (security-intrusion), evidencia
        # distinta. Cada um throttla sozinho — sem isso se sobrescreviam pela
        # chave (source,tenant) e AMBOS re-disparavam todo ciclo (o flood duplo).
        caminho = tmp_path / "dedup.json"

        def ciclo(sink: DiscordAlertSink) -> None:
            sink.enviar(_alerta(source=FonteAlerta.SECURITY_INTRUSION, evidencias=["porta=5678"]))
            sink.enviar(_alerta(source=FonteAlerta.SECURITY_INTRUSION, evidencias=["porta=5679"]))

        t = _TransporteFake()
        sink = DiscordAlertSink(webhook_global=WEBHOOK, transporte=t, caminho_estado=caminho)
        ciclo(sink)  # 1o ciclo: ambos novos -> 2 envios
        ciclo(sink)  # 2o ciclo: ambos repetidos -> 0
        assert len(t.chamadas) == 2

        # processo NOVO (proximo tick do cron) tambem suprime os dois
        t2 = _TransporteFake()
        sink2 = DiscordAlertSink(webhook_global=WEBHOOK, transporte=t2, caminho_estado=caminho)
        ciclo(sink2)
        assert len(t2.chamadas) == 0

    def test_dedup_ignora_latencia_volatil(self, tmp_path: Any) -> None:
        # feature-down com latencia DIFERENTE a cada ciclo (91.7ms, 88.2ms...)
        # NAO pode re-alertar — a identidade e feature/status, nao a medicao.
        caminho = tmp_path / "dedup.json"
        t = _TransporteFake()
        sink = DiscordAlertSink(webhook_global=WEBHOOK, transporte=t, caminho_estado=caminho)
        base = ["feature=syn-iris-chat", "status esperado=200 obtido=503"]
        sink.enviar(
            _alerta(
                source=FonteAlerta.FEATURE_DOWN,
                evidencias=[*base, "latencia=91.7ms"],
            )
        )
        sink.enviar(
            _alerta(
                source=FonteAlerta.FEATURE_DOWN,
                evidencias=[*base, "latencia=88.2ms"],
            )
        )
        assert len(t.chamadas) == 1  # latencia volatil nao quebra o throttle

    def test_dedup_heartbeat_ignora_ciclo_e_timestamp(self, tmp_path: Any) -> None:
        # o heartbeat traz ciclo=N + batimento_em=<ts> que mudam TODO ciclo — não
        # podem quebrar o throttle (senão 1 batimento/hora = flood no Discord).
        caminho = tmp_path / "dedup.json"
        t = _TransporteFake()
        sink = DiscordAlertSink(webhook_global=WEBHOOK, transporte=t, caminho_estado=caminho)
        base = ["status=limpo", "watcher=vivo"]
        sink.enviar(
            _alerta(
                source=FonteAlerta.OBSERVE_HEARTBEAT,
                evidencias=[*base, "ciclo=60", "batimento_em=2026-07-24T03:00:00"],
            )
        )
        sink.enviar(
            _alerta(
                source=FonteAlerta.OBSERVE_HEARTBEAT,
                evidencias=[*base, "ciclo=120", "batimento_em=2026-07-24T04:00:00"],
            )
        )
        assert len(t.chamadas) == 1  # 2o batimento (ciclo/ts distintos) suprimido → 1x/dia

    def test_janela_por_severidade(self, tmp_path: Any) -> None:
        # Diretriz: CRITICAL (impacta usuário/derruba feature/vulnerabilidade)
        # re-alerta a cada 1h; INFO (telemetria/benigno) só a cada 24h.
        def _envelhecer(sink: DiscordAlertSink, segundos: float) -> None:
            sink._estado[next(iter(sink._estado))]["ts"] -= segundos  # noqa: SLF001

        # CRITICAL com 2h de idade -> RE-ALERTA (janela 1h)
        tc = _TransporteFake()
        sc = DiscordAlertSink(webhook_global=WEBHOOK, transporte=tc, caminho_estado=tmp_path / "c")
        crit: dict[str, Any] = {
            "source": FonteAlerta.FEATURE_DOWN,
            "severity": SeveridadeAlerta.CRITICAL,
            "evidencias": ["iris down"],
        }
        sc.enviar(_alerta(**crit))
        _envelhecer(sc, 7200)
        sc.enviar(_alerta(**crit))
        assert len(tc.chamadas) == 2

        # INFO com 2h de idade -> SUPRIME (janela 24h)
        ti = _TransporteFake()
        si = DiscordAlertSink(webhook_global=WEBHOOK, transporte=ti, caminho_estado=tmp_path / "i")
        info: dict[str, Any] = {
            "source": FonteAlerta.OBSERVE_HEARTBEAT,
            "severity": SeveridadeAlerta.INFO,
            "evidencias": ["watcher=vivo"],
        }
        si.enviar(_alerta(**info))
        _envelhecer(si, 7200)
        si.enviar(_alerta(**info))
        assert len(ti.chamadas) == 1

    def test_estado_corrompido_comeca_limpo(self, tmp_path: Any) -> None:
        caminho = tmp_path / "dedup.json"
        caminho.write_text("{lixo nao json", encoding="utf-8")
        t = _TransporteFake()
        sink = DiscordAlertSink(webhook_global=WEBHOOK, transporte=t, caminho_estado=caminho)
        sink.enviar(_alerta(evidencias=["x"]))
        assert len(t.chamadas) == 1  # nao quebrou; enviou


class TestRoteamentoTenant:
    def test_tenant_sem_webhook_e_noop(self) -> None:
        t = _TransporteFake()
        sink = DiscordAlertSink(
            webhooks_por_tenant={TenantId("a"): "https://d.test/a"}, transporte=t
        )

        sink.enviar(_alerta(tenant=TenantId("desconhecido")))  # sem webhook, sem global

        assert t.chamadas == []

    def test_tenant_roteia_para_webhook_proprio(self) -> None:
        t = _TransporteFake()
        sink = DiscordAlertSink(
            webhooks_por_tenant={TenantId("a"): "https://d.test/a"},
            webhook_global=WEBHOOK,
            transporte=t,
        )

        sink.enviar(_alerta(tenant=TenantId("a")))

        assert t.chamadas[0][0] == "https://d.test/a"  # nao o global


class TestRoteamentoPorTema:
    def test_roteia_fonte_para_canal_por_tema(self) -> None:
        t = _TransporteFake()
        canais = {
            "security": "https://d.test/security",
            "infra": "https://d.test/infra",
            "performance": "https://d.test/perf",
            "log": "https://d.test/log",
        }
        sink = DiscordAlertSink(webhooks_por_canal=canais, transporte=t)

        sink.enviar(_alerta(source=FonteAlerta.SECURITY_INTRUSION, evidencias=["a"]))
        sink.enviar(_alerta(source=FonteAlerta.FEATURE_DOWN, evidencias=["b"]))
        sink.enviar(_alerta(source=FonteAlerta.OBSERVE_HEARTBEAT, evidencias=["c"]))

        assert t.chamadas[0][0] == "https://d.test/security"
        assert t.chamadas[1][0] == "https://d.test/perf"  # feature-down -> performance
        assert t.chamadas[2][0] == "https://d.test/log"

    def test_fonte_sem_mapa_cai_no_canal_padrao(self) -> None:
        t = _TransporteFake()
        # so o canal 'log' existe; uma fonte que mapeia p/ 'performance' cai no padrao
        sink = DiscordAlertSink(
            webhooks_por_canal={"log": "https://d.test/log"}, canal_padrao="log", transporte=t
        )

        sink.enviar(_alerta(source=FonteAlerta.FEATURE_DOWN))

        assert t.chamadas[0][0] == "https://d.test/log"  # o mais proximo disponivel


class TestTransporteUrllib:
    def test_envia_user_agent_proprio(self, monkeypatch: Any) -> None:
        # regressao: Cloudflare do Discord da 403 ao UA padrao do urllib.
        from batman_os.governance import alert_sinks

        capturado: dict[str, Any] = {}

        class _Resp:
            # ⚠️ O fake espelha a interface REAL. Desde SAIDA-004 o transporte
            # LE a resposta (`?wait=true` devolve o objeto da mensagem, e o ID
            # dele e a unica forma de editar depois), entao um fake que so fecha
            # nao exercita mais o caminho de producao.
            def read(self) -> bytes:
                return b'{"id": "123456789"}'

            def close(self) -> None:
                pass

            def __enter__(self) -> _Resp:
                return self

            def __exit__(self, *_: Any) -> None:
                pass

        def fake_urlopen(req: Any, timeout: float) -> Any:
            capturado["ua"] = req.get_header("User-agent")
            capturado["url"] = req.full_url
            capturado["metodo"] = req.get_method()
            return _Resp()

        monkeypatch.setattr("batman_os.governance.alert_sinks.urllib.request.urlopen", fake_urlopen)
        devolvido = alert_sinks._TransporteUrllib().postar(
            "https://discord.test/wh", {"content": "x"}
        )

        # `?wait=true` e o que faz o Discord devolver o ID; sem ele a resposta e
        # 204 sem corpo e nao ha o que editar depois.
        assert "wait=true" in capturado["url"]
        assert capturado["metodo"] == "POST"
        assert devolvido == "123456789"

        assert capturado["ua"] and "BatmanOS" in capturado["ua"]


class TestFalhaNuncaPropaga:
    def test_enviar_engole_erro_de_transporte(self) -> None:
        sink = DiscordAlertSink(webhook_global=WEBHOOK, transporte=_TransporteFake(falhar=True))

        sink.enviar(_alerta())  # nao deve levantar


class TestIntegracaoGovernanceEngine:
    def test_raise_alert_entrega_no_sink(self) -> None:
        t = _TransporteFake()
        engine = GovernanceEngine(sink=DiscordAlertSink(webhook_global=WEBHOOK, transporte=t))

        alerta = _alerta()
        engine.raise_alert(alerta)

        assert len(t.chamadas) == 1
        assert engine.get_open_alerts()[0].id == alerta.id  # persistiu tambem

    def test_sem_sink_mantem_comportamento_original(self) -> None:
        engine = GovernanceEngine()  # sem sink

        alerta = _alerta()
        engine.raise_alert(alerta)

        assert engine.get_open_alerts() == [alerta]

    def test_falha_do_sink_nao_impede_persistencia(self) -> None:
        engine = GovernanceEngine(
            sink=DiscordAlertSink(webhook_global=WEBHOOK, transporte=_TransporteFake(falhar=True))
        )

        alerta = _alerta()
        engine.raise_alert(alerta)  # sink falha internamente

        assert engine.get_open_alerts()[0].id == alerta.id
        assert engine.get_open_alerts()[0].status == StatusAlerta.OPEN
