"""Três defeitos do caminho de entrega, medidos no journal de produção.

* `DIV-VARREDURA-001` — silêncio DELIBERADO e falha de rota gravavam o mesmo
  `sem_canal`, com o mesmo detalhe "nenhum webhook resolveu para este alerta".
  Eram 6.781 registros assim, e **todos** deliberados: `porta-manutencao`
  (política de manutenção) e `varredura-web` (regra em shadow). Se uma rota
  quebrasse de verdade, seria indistinguível desses milhares.
* `DIV-ENVELOPE-001` — o resumo diário dizia "2 incidente(s) aberto(s)" e
  "há incidente de segurança em curso" no corpo, e "Funcionando normalmente /
  Nenhuma ação necessária / Impacto: Nenhum" no cartão.
* `DIV-PRICES-001` — `prices-20y` está CRITICAL desde 08/09, com 284 supressões
  e ~23 entregas idênticas. A 24ª mensagem era igual à primeira.
"""

from __future__ import annotations

from typing import Any

from batman_os.foundation.types import Evidence
from batman_os.governance import mensagem
from batman_os.governance.alert_sinks import DiscordAlertSink
from batman_os.governance.governance_engine import (
    FonteAlerta,
    GovernanceAlert,
    SeveridadeAlerta,
)

WEBHOOK = "https://discord.test/webhook"
ORIGEM_RESUMO = "observe:resumo-diario"
ORIGEM_MANUTENCAO = "observe:porta-manutencao"
ORIGEM_VARREDURA = "observe:varredura-web"


class _Transporte:
    def __init__(self) -> None:
        self.chamadas: list[tuple[str, dict[str, Any]]] = []

    def postar(self, webhook_url: str, payload: dict[str, Any]) -> str | None:
        self.chamadas.append((webhook_url, payload))
        return str(len(self.chamadas))

    def editar(self, webhook_url: str, message_id: str, payload: dict[str, Any]) -> None:
        return None


class _Relogio:
    def __init__(self, inicio: float = 1_789_000_000.0) -> None:
        self.agora = inicio

    def avancar(self, segundos: float) -> None:
        self.agora += segundos

    def __call__(self) -> float:
        return self.agora


def _alerta(
    origem: str,
    *,
    linhas: list[str] | None = None,
    severidade: SeveridadeAlerta = SeveridadeAlerta.INFO,
    fonte: FonteAlerta = FonteAlerta.SECURITY_INTRUSION,
) -> GovernanceAlert:
    return GovernanceAlert(
        source=fonte,
        severity=severidade,
        evidence=[Evidence(origem=origem, evidencias=linhas or ["linha"])],
    )


def _sink(transporte: _Transporte, tmp_path: Any, **kw: Any) -> DiscordAlertSink:
    return DiscordAlertSink(
        webhooks_por_canal={"cyber": WEBHOOK, "log": WEBHOOK, "infra": WEBHOOK},
        transporte=transporte,
        caminho_estado=tmp_path / "dedup.json",
        caminho_journal=tmp_path / "journal.jsonl",
        **kw,
    )


# --------------------------------------------------------------------------
# DIV-VARREDURA-001
# --------------------------------------------------------------------------


class TestSilencioDeliberadoNaoEFalhaDeRota:
    def test_manutencao_conhecida_e_politica_e_nao_defeito(self, tmp_path: Any) -> None:
        sink = _sink(_Transporte(), tmp_path)

        assert sink.enviar(_alerta(ORIGEM_MANUTENCAO)) == "silenciado_por_politica"

    def test_regra_em_shadow_tambem_e_politica(self, tmp_path: Any) -> None:
        """A varredura web está em shadow por decisão (Apêndice C, §C.5).

        ⚠️ Meu card dizia "detector mudo por defeito de configuração". Estava
        errado: a mudez é deliberada. O defeito era a INDISTINGUIBILIDADE.
        """
        sink = _sink(_Transporte(), tmp_path)

        assert sink.enviar(_alerta(ORIGEM_VARREDURA)) == "silenciado_por_politica"

    def test_rota_que_a_politica_queria_e_nao_existe_continua_sendo_defeito(
        self, tmp_path: Any
    ) -> None:
        """⚠️ O controle: sem nenhum webhook configurado, um alerta que a
        política MANDA publicar cai em `sem_canal` — e isso é defeito."""
        sink = DiscordAlertSink(
            transporte=_Transporte(),
            caminho_estado=tmp_path / "dedup.json",
            caminho_journal=tmp_path / "journal.jsonl",
        )

        grave = _alerta("observe:ssh-bruteforce", severidade=SeveridadeAlerta.CRITICAL)
        assert sink.enviar(grave) == "sem_canal"

    def test_os_dois_desfechos_sao_valores_distintos(self, tmp_path: Any) -> None:
        sink = _sink(_Transporte(), tmp_path)
        deliberado = sink.enviar(_alerta(ORIGEM_MANUTENCAO))

        sink_sem_rota = DiscordAlertSink(
            transporte=_Transporte(),
            caminho_estado=tmp_path / "d2.json",
            caminho_journal=tmp_path / "j2.jsonl",
        )
        falha = sink_sem_rota.enviar(
            _alerta("observe:ssh-bruteforce", severidade=SeveridadeAlerta.CRITICAL)
        )

        assert deliberado != falha, (
            "política funcionando e defeito de configuração não podem gravar o"
            " mesmo desfecho — era assim que 6.781 registros ficavam iguais"
        )


# --------------------------------------------------------------------------
# DIV-ENVELOPE-001
# --------------------------------------------------------------------------


class TestOCartaoSegueOVeredito:
    def _cartao(self, veredito: str | None) -> tuple[str, str, str]:
        linhas = ["resumo diário de segurança — prova de vida do canal"]
        if veredito is not None:
            linhas.append(f"veredito_do_dia={veredito}")
        a = _alerta(ORIGEM_RESUMO, linhas=linhas)
        return (
            mensagem.estado_do_servico(a),
            mensagem.acao_recomendada(a),
            mensagem.impacto(a),
        )

    def test_dia_limpo_continua_dizendo_que_esta_tudo_normal(self) -> None:
        estado, acao, _impacto = self._cartao("limpo")

        assert "normalmente" in estado
        assert acao == mensagem.Acao.NENHUMA

    def test_incidente_em_curso_NAO_diz_nenhuma_acao_necessaria(self) -> None:
        """O defeito exato de 11/09 00:00 BRT."""
        estado, acao, impacto = self._cartao("incidente")

        assert acao != mensagem.Acao.NENHUMA
        assert "normalmente" not in estado
        assert "incidente" in impacto.lower()

    def test_coleta_cega_nao_vira_boa_noticia(self) -> None:
        estado, acao, impacto = self._cartao("inconclusivo")

        assert "nconclusivo" in estado
        assert acao != mensagem.Acao.NENHUMA
        assert "não é ausência de ataque" in impacto.lower() or "NÃO é" in impacto

    def test_sem_o_marcador_o_cartao_antigo_vale_e_nada_quebra(self) -> None:
        """⚠️ Degradação, nunca exceção: um emissor antigo segue produzindo
        mensagem válida."""
        estado, acao, _impacto = self._cartao(None)

        assert "normalmente" in estado
        assert acao == mensagem.Acao.NENHUMA


# --------------------------------------------------------------------------
# DIV-PRICES-001
# --------------------------------------------------------------------------


class TestIncidenteLongoNaoEIncidenteNovo:
    def _campos(self, payload: dict[str, Any]) -> dict[str, str]:
        return {c["name"]: c["value"] for c in payload["embeds"][0]["fields"]}

    def test_a_primeira_mensagem_nao_fala_de_arrasto(self, tmp_path: Any, monkeypatch: Any) -> None:
        import time as _time

        monkeypatch.setattr(_time, "time", _Relogio())
        t = _Transporte()
        sink = _sink(t, tmp_path)

        sink.enviar(
            _alerta(
                "observe:pipeline",
                severidade=SeveridadeAlerta.CRITICAL,
                fonte=FonteAlerta.DATA_PIPELINE_ERROR,
            )
        )

        assert "Há quanto tempo" not in self._campos(t.chamadas[0][1])

    def test_depois_de_horas_a_mensagem_DIZ_que_nao_e_nova(
        self, tmp_path: Any, monkeypatch: Any
    ) -> None:
        """Era o `prices-20y`: a 24ª mensagem idêntica à primeira."""
        import time as _time

        relogio = _Relogio()
        monkeypatch.setattr(_time, "time", relogio)
        t = _Transporte()
        sink = _sink(t, tmp_path)
        alerta = _alerta(
            "observe:pipeline",
            severidade=SeveridadeAlerta.CRITICAL,
            fonte=FonteAlerta.DATA_PIPELINE_ERROR,
        )

        sink.enviar(alerta)
        relogio.avancar(3 * 86400.0)
        sink.enviar(alerta)

        campos = self._campos(t.chamadas[-1][1])
        assert "Há quanto tempo" in campos
        assert "NAO E NOVO" in campos["Há quanto tempo"]
        assert "3 dias" in campos["Há quanto tempo"]

    def test_o_inicio_do_episodio_sobrevive_aos_reenvios(
        self, tmp_path: Any, monkeypatch: Any
    ) -> None:
        """⚠️ Se `desde` fosse sobrescrito a cada entrega, um incidente de três
        dias seria lido como se tivesse começado agora — o defeito de volta."""
        import time as _time

        relogio = _Relogio()
        monkeypatch.setattr(_time, "time", relogio)
        t = _Transporte()
        sink = _sink(t, tmp_path)
        alerta = _alerta(
            "observe:pipeline",
            severidade=SeveridadeAlerta.CRITICAL,
            fonte=FonteAlerta.DATA_PIPELINE_ERROR,
        )

        for _ in range(5):
            sink.enviar(alerta)
            relogio.avancar(7 * 3600.0)

        campos = self._campos(t.chamadas[-1][1])
        assert "Há quanto tempo" in campos
        assert "28 h" in campos["Há quanto tempo"]
