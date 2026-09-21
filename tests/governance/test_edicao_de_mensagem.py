"""A recuperação EDITA a mensagem original — SAIDA-004.

Decisão do DEV, 2026-09-08: *"o estado e a auditoria mudam imediatamente; se
houve mensagem original, ela é editada com a recuperação; não nasce uma nova
notificação sonora para cada fechamento."*

E as três exigências do caminho de falha, que são o que este arquivo mais
protege: *"persistir a referência da mensagem e do destino usado, sem registrar
o segredo do webhook. Se a edição falhar, auditar e usar o resumo existente; não
criar mensagem substituta nem impedir o fechamento interno."*
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from batman_os.foundation.types import Evidence, Medicao
from batman_os.governance.alert_sinks import (
    DiscordAlertSink,
    assunto_do_alerta,
    impressao_do_destino,
)
from batman_os.governance.governance_engine import (
    FonteAlerta,
    GovernanceAlert,
    SeveridadeAlerta,
)

WEBHOOK = "https://discord.test/api/webhooks/42/token-secreto-nao-pode-vazar"
ASSUNTO = "metrica:observe.cpu"


class _Transporte:
    """Registra o que foi postado e editado, sem rede."""

    def __init__(self, *, falhar_edicao: bool = False) -> None:
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.edicoes: list[tuple[str, str]] = []
        self._falhar_edicao = falhar_edicao
        self._proximo = 1000

    def postar(self, webhook_url: str, payload: dict[str, Any]) -> str | None:
        self.posts.append((webhook_url, payload))
        self._proximo += 1
        return str(self._proximo)

    def editar(self, webhook_url: str, message_id: str, payload: dict[str, Any]) -> None:
        if self._falhar_edicao:
            raise RuntimeError("404 Unknown Message")
        self.edicoes.append((message_id, payload.get("content", "")))


def _abertura() -> GovernanceAlert:
    return GovernanceAlert(
        source=FonteAlerta.INFRA_SATURATION,
        severity=SeveridadeAlerta.CRITICAL,
        evidence=[
            Evidence(
                origem="AlertRule:observe.cpu:crit",
                evidencias=["metric=observe.cpu:crit", "valor=95%"],
                assunto=ASSUNTO,
                medicao=Medicao(base="observe.cpu", valor=95.0, unidade="%"),
            )
        ],
    )


def _fechamento() -> GovernanceAlert:
    return GovernanceAlert(
        source=FonteAlerta.FEATURE_RECOVERED,
        severity=SeveridadeAlerta.INFO,
        evidence=[
            Evidence(
                origem="observe:metrica-recuperada",
                evidencias=["metrica=observe.cpu", "voltou ao normal"],
                assunto=ASSUNTO,
                medicao=Medicao(base="observe.cpu", valor=70.0, unidade="%"),
            )
        ],
    )


def _sink(tmp_path: Path, transporte: _Transporte) -> DiscordAlertSink:
    return DiscordAlertSink(
        webhook_global=WEBHOOK,
        transporte=transporte,
        caminho_estado=tmp_path / "dedup.json",
        janela_repeticao_s=0.0,
    )


class TestOFechamentoEditaEmVezDePostar:
    def test_recuperacao_edita_a_mensagem_original(self, tmp_path: Path) -> None:
        transporte = _Transporte()
        sink = _sink(tmp_path, transporte)

        sink.enviar(_abertura())
        assert len(transporte.posts) == 1
        message_id = "1001"  # o primeiro ID que o transporte devolve

        sink.enviar(_fechamento())
        # ⚠️ Nenhum POST novo: postar de novo tocaria o canal, e a decisao do DEV
        # e explicita -- "nao nasce uma nova notificacao sonora".
        assert len(transporte.posts) == 1
        assert len(transporte.edicoes) == 1
        assert transporte.edicoes[0][0] == message_id

    def test_sem_mensagem_original_o_fechamento_posta_normalmente(self, tmp_path: Path) -> None:
        """O par que varia: editar não pode virar silêncio quando não há o que
        editar. Um fechamento sem abertura registrada ainda tem de chegar."""
        transporte = _Transporte()
        _sink(tmp_path, transporte).enviar(_fechamento())
        assert len(transporte.posts) == 1
        assert not transporte.edicoes

    def test_abertura_nao_edita_nada(self, tmp_path: Path) -> None:
        transporte = _Transporte()
        sink = _sink(tmp_path, transporte)
        sink.enviar(_abertura())
        sink.enviar(_abertura())
        assert not transporte.edicoes


class TestOSegredoDoWebhookNuncaEGravado:
    def test_o_estado_guarda_impressao_e_nao_a_url(self, tmp_path: Path) -> None:
        caminho = tmp_path / "dedup.json"
        transporte = _Transporte()
        _sink(tmp_path, transporte).enviar(_abertura())

        bruto = caminho.read_text(encoding="utf-8")
        # ⚠️ A URL do webhook CONTEM o token. Grava-la num arquivo de depuracao
        # que alguem vai abrir seria vazar o segredo.
        assert "token-secreto-nao-pode-vazar" not in bruto
        assert WEBHOOK not in bruto

        dados = json.loads(bruto)
        # ⚠️ A chave ganhou o TENANT em `DIV-TENANT-001` (`tenant|assunto`),
        # porque `assunto_de_metrica` devolve o mesmo texto para todos eles e o
        # fechamento de um editava a mensagem do outro. O que este teste afirma
        # -- que o estado guarda a IMPRESSAO e nunca a URL -- nao mudou.
        (guardado,) = [v for k, v in dados["mensagens"].items() if k.endswith(f"|{ASSUNTO}")]
        assert guardado["id"] == "1001"
        assert guardado["destino"] == impressao_do_destino(WEBHOOK)

    def test_a_impressao_nao_reconstroi_a_url(self) -> None:
        impressao = impressao_do_destino(WEBHOOK)
        assert "token" not in impressao
        assert len(impressao) == 16


class TestQuandoAEdicaoFalha:
    def test_falha_de_edicao_nao_cria_mensagem_substituta(self, tmp_path: Path) -> None:
        """*"Não criar mensagem substituta"* — a exigência mais fácil de violar.

        O reflexo natural seria "se o PATCH falhou, posta". Mas postar é
        exatamente o que a decisão proíbe: uma mensagem nova toca o canal.
        """
        transporte = _Transporte(falhar_edicao=True)
        sink = _sink(tmp_path, transporte)
        sink.enviar(_abertura())
        sink.enviar(_fechamento())

        assert len(transporte.posts) == 1, "nenhum POST substituto"
        assert not transporte.edicoes

    def test_falha_de_edicao_e_auditada(self, tmp_path: Path) -> None:
        transporte = _Transporte(falhar_edicao=True)
        sink = _sink(tmp_path, transporte)
        sink.enviar(_abertura())
        sink.enviar(_fechamento())

        # A auditoria vive no journal; o desfecho tem nome PROPRIO e nao se
        # confunde com entrega -- "falhei ao editar" e diferente de "entreguei".
        from batman_os.governance import alert_journal

        linhas = [
            json.loads(linha)
            for linha in alert_journal.caminho_do_ambiente()
            .read_text(encoding="utf-8")
            .splitlines()
            if linha.strip()
        ]
        desfechos = [linha["outcome"] for linha in linhas]
        assert "falha_de_edicao" in desfechos
        registro = next(linha for linha in linhas if linha["outcome"] == "falha_de_edicao")
        assert registro["notification_sent"] is False
        assert "resumo diario" in registro["detail"]

    def test_webhook_rotacionado_nao_tenta_editar(self, tmp_path: Path) -> None:
        """O ID pertence ao webhook que postou.

        Se a URL rotacionar, o ID fica órfão: a edição não é nem tentada, e o
        órfão é AUDITADO em vez de virar um 404 silencioso.
        """
        transporte = _Transporte()
        _sink(tmp_path, transporte).enviar(_abertura())

        outro = DiscordAlertSink(
            webhook_global="https://discord.test/api/webhooks/99/outro-token",
            transporte=transporte,
            caminho_estado=tmp_path / "dedup.json",
            janela_repeticao_s=0.0,
        )
        outro.enviar(_fechamento())

        assert not transporte.edicoes
        from batman_os.governance import alert_journal

        desfechos = [
            json.loads(linha)["outcome"]
            for linha in alert_journal.caminho_do_ambiente()
            .read_text(encoding="utf-8")
            .splitlines()
            if linha.strip()
        ]
        assert "edicao_impossivel" in desfechos


class TestOAssuntoLigaOsDoisLados:
    def test_o_assunto_sai_do_campo_e_nao_do_texto(self) -> None:
        assert assunto_do_alerta(_abertura()) == ASSUNTO
        assert assunto_do_alerta(_fechamento()) == ASSUNTO

    def test_alerta_sem_assunto_nao_guarda_referencia(self, tmp_path: Path) -> None:
        """Sem elo declarado não há edição — e isso é honesto, não um buraco."""
        transporte = _Transporte()
        sem_elo = GovernanceAlert(
            source=FonteAlerta.INFRA_SATURATION,
            severity=SeveridadeAlerta.WARNING,
            evidence=[Evidence(origem="observe:qualquer", evidencias=["x"])],
        )
        _sink(tmp_path, transporte).enviar(sem_elo)
        dados = json.loads((tmp_path / "dedup.json").read_text(encoding="utf-8"))
        assert not dados["mensagens"]


class TestOEstadoAntigoContinuaValendo:
    def test_formato_antigo_e_lido_sem_perder_o_throttle(self, tmp_path: Path) -> None:
        """O arquivo em produção tem o formato antigo — um mapa na raiz.

        Ler só o formato novo faria todo throttle em curso reiniciar no deploy,
        e o flood que ele segura voltaria por uma janela inteira.
        """
        import time

        caminho = tmp_path / "dedup.json"
        caminho.write_text(json.dumps({"abc123": {"ts": time.time()}}), encoding="utf-8")

        transporte = _Transporte()
        sink = DiscordAlertSink(
            webhook_global=WEBHOOK,
            transporte=transporte,
            caminho_estado=caminho,
            janela_repeticao_s=3600.0,
        )
        # O estado antigo sobreviveu: a chave continua conhecida.
        assert "abc123" in sink._estado


class TestOFechamentoVoltaAoCanalDaAbertura:
    """Defeito achado em PRODUÇÃO, no primeiro fechamento real (08/09).

    A abertura de CPU é `INFRA_SATURATION` e roteia para `#infra`; o fechamento
    é `FEATURE_RECOVERED` e roteia para `#performance`. Canais diferentes,
    webhooks diferentes — e o `PATCH` registrava `edicao_impossivel` por destino
    divergente, **sempre**. A edição era impossível por construção, e só um
    fechamento de verdade mostrou.
    """

    def test_edita_no_webhook_que_postou_e_nao_no_da_fonte(self, tmp_path: Path) -> None:
        transporte = _Transporte()
        sink = DiscordAlertSink(
            webhooks_por_canal={
                "infra": "https://d.test/infra",
                "performance": "https://d.test/performance",
            },
            transporte=transporte,
            caminho_estado=tmp_path / "dedup.json",
            janela_repeticao_s=0.0,
        )

        sink.enviar(_abertura())
        assert transporte.posts[0][0] == "https://d.test/infra"

        sink.enviar(_fechamento())
        assert len(transporte.posts) == 1, "nenhuma mensagem nova"
        assert len(transporte.edicoes) == 1, "editou a original"

    def test_sem_referencia_o_fechamento_roteia_pela_fonte(self, tmp_path: Path) -> None:
        """O par que varia: sem original guardada, o roteamento normal vale."""
        transporte = _Transporte()
        DiscordAlertSink(
            webhooks_por_canal={
                "infra": "https://d.test/infra",
                "performance": "https://d.test/performance",
            },
            transporte=transporte,
            caminho_estado=tmp_path / "dedup.json",
            janela_repeticao_s=0.0,
        ).enviar(_fechamento())
        assert transporte.posts[0][0] == "https://d.test/performance"


class TestAEdicaoPassaNaFrenteDoThrottle:
    """Defeito achado em PRODUÇÃO em 08/09, no segundo exercício real.

    A abertura foi entregue e guardou a referência; o fechamento caiu em
    `suprimido_por_janela`, porque um fechamento anterior tinha a mesma
    assinatura dentro da janela. Resultado: a mensagem original ficava dizendo
    CRITICAL **para sempre**, com a métrica já recuperada.

    O throttle existe para não repetir MENSAGEM. Editar não repete nada — não
    cria mensagem, não notifica, não faz som. Aplicar-lhe a regra do ruído é
    usar a defesa contra flood para impedir a **correção** de uma mensagem
    errada.
    """

    def test_fechamento_edita_mesmo_dentro_da_janela(self, tmp_path: Path) -> None:
        transporte = _Transporte()
        sink = DiscordAlertSink(
            webhook_global=WEBHOOK,
            transporte=transporte,
            caminho_estado=tmp_path / "dedup.json",
            # Janela LARGA: sem a correção, o fechamento seria suprimido.
            janela_repeticao_s=86_400.0,
        )
        sink.enviar(_abertura())
        assert len(transporte.posts) == 1

        # Um fechamento anterior ja gastou a janela desta assinatura.
        sink.enviar(_fechamento())
        assert len(transporte.edicoes) == 1

        # E o SEGUNDO ciclo do mesmo assunto: abre de novo e fecha de novo.
        sink.enviar(_abertura())
        sink.enviar(_fechamento())
        assert len(transporte.edicoes) == 2, "a segunda correcao nao pode ser engolida"

    def test_abertura_repetida_CONTINUA_throttlada(self, tmp_path: Path) -> None:
        """O par que varia: a correção não pode desligar o throttle das aberturas.

        Sem este teste, mover a edição para a frente poderia ter aberto um
        caminho que ignora a janela para tudo — e o flood que o throttle segura
        voltaria inteiro.
        """
        transporte = _Transporte()
        sink = DiscordAlertSink(
            webhook_global=WEBHOOK,
            transporte=transporte,
            caminho_estado=tmp_path / "dedup.json",
            janela_repeticao_s=86_400.0,
        )
        sink.enviar(_abertura())
        sink.enviar(_abertura())
        sink.enviar(_abertura())
        assert len(transporte.posts) == 1, "abertura repetida segue suprimida"
