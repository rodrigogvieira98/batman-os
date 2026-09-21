"""`endpoint-down` não pode dizer "não respondeu" sobre quem RESPONDEU.

`DIV-MENSAGEM-001`, medido em 2026-09-16 no cartão entregue ao Discord:

    título   : Site fora do ar
    resumo   : O site não respondeu nem pela internet nem por dentro do servidor.
    estado   : 🔴 Indisponível
    evidência: status=403  ·  detalhe=HTTP 403

**403 é uma resposta.** A frase contradizia o dado que ela própria carregava.

E a distinção não é cosmética: muda para onde o plantão olha. *Sem resposta*
aponta rede e processo caído; *respondeu com erro* aponta aplicação, upstream e
nginx. Uma frase só para as duas manda a pessoa para o lugar errado metade das
vezes.

⚠️ A frase continua vindo do MAPA, escrita à mão — a evidência só ESCOLHE qual
entrada usar. Tirar a frase da evidência é o que `resumo()` proíbe, e por dois
motivos medidos: uma versão exibiu `metric=observe.cpu:crit` e outra escolheu
*"parte das falhas não teve IP extraído"* por acaso de pontuação.
"""

from __future__ import annotations

from batman_os.foundation.types import Evidence
from batman_os.governance import mensagem
from batman_os.governance.governance_engine import (
    FonteAlerta,
    GovernanceAlert,
    SeveridadeAlerta,
)


def _alerta(*linhas: str) -> GovernanceAlert:
    return GovernanceAlert(
        source=FonteAlerta.ENDPOINT_DOWN,
        severity=SeveridadeAlerta.CRITICAL,
        evidence=[Evidence(origem="observe:endpoint-down", evidencias=list(linhas))],
    )


class TestRespondeuComErro:
    def test_403_nao_e_dito_como_ausencia_de_resposta(self) -> None:
        """⚠️ O cartão exato que foi ao Discord em 16/09 18:45."""
        a = _alerta("url=https://x/wp-config.php", "status=403", "detalhe=HTTP 403")
        assert "não respondeu" not in mensagem.resumo(a)
        assert "respondeu" in mensagem.resumo(a)

    def test_500_aponta_aplicacao_e_nao_rede(self) -> None:
        a = _alerta("url=https://x", "status=500", "detalhe=HTTP 500")
        assert "aplicação" in mensagem.impacto(a)
        assert "Site respondendo com erro" in mensagem.titulo(a)

    def test_continua_indisponivel_e_escalar(self) -> None:
        """Responder 500 não é menos grave: quem abre o site recebe erro. O que
        muda é o DIAGNÓSTICO, não a gravidade."""
        a = _alerta("url=https://x", "status=502")
        assert mensagem.estado_do_servico(a) == mensagem.EstadoDoServico.INDISPONIVEL
        assert mensagem.acao_recomendada(a) == mensagem.Acao.ESCALAR


class TestSemResposta:
    def test_timeout_continua_dizendo_que_nao_respondeu(self) -> None:
        """A contrapartida: o conserto não pode ter apagado o caso verdadeiro."""
        a = _alerta("url=https://x", "status=timeout", "detalhe=The read operation timed out")
        assert "não respondeu" in mensagem.resumo(a)
        assert "Site fora do ar" in mensagem.titulo(a)


class TestODefaultNaoAfirmaDemais:
    def test_status_ausente_cai_no_texto_neutro(self) -> None:
        """⚠️ Se a grafia de `status=` mudar, a leitura falha. A degradação tem
        de cair no que NÃO afirma demais — afirmar 'não respondeu' por falha de
        parsing repetiria o defeito que este mapa conserta."""
        a = _alerta("url=https://x")
        assert "não respondeu" not in mensagem.resumo(a)
        assert "não foi declarado" in mensagem.resumo(a)

    def test_status_ininteligivel_cai_no_texto_neutro(self) -> None:
        a = _alerta("url=https://x", "status=???")
        assert "não respondeu" not in mensagem.resumo(a)

    def test_nenhum_caso_cai_no_padrao_sem_classificacao(self) -> None:
        """`_PADRAO` é a rede de segurança e tem de continuar inalcançável."""
        for linhas in (("status=403",), ("status=timeout",), ("url=https://x",)):
            assert mensagem.resumo(_alerta(*linhas)) != mensagem._PADRAO[1]
