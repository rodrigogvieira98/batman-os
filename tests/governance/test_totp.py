"""TOTP conferido contra os vetores PUBLICADOS da RFC — `CYB-TOTP-001`.

**Por que este arquivo existe assim.** A regra-mãe do método é *verificar por
código, teste, contrato ou gabarito; nunca perguntar ao modelo se acertou*.
Criptografia é onde isso mais importa e onde é mais fácil de burlar sem
perceber: uma implementação errada de truncamento dinâmico produz códigos de
seis dígitos perfeitamente plausíveis, que simplesmente não são os que o
celular mostra.

⚠️ **Um teste que compara a minha implementação com a minha expectativa não
prova nada.** Por isso os números abaixo vêm de fora: RFC 4226 Apêndice D (HOTP)
e RFC 6238 Apêndice B (TOTP). São gabarito externo, publicados anos antes deste
repositório existir, e o mesmo que o Google Authenticator satisfaz.

As quatro travas do card têm um teste cada, e o estado atravessa instâncias
novas de propósito: cada ciclo do monitor é um processo novo (armadilha 2).
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from batman_os.governance.totp import (
    CONGELAMENTO_S,
    DIGITOS,
    JANELA_ERROS_S,
    PERIODO_S,
    TETO_ERROS,
    Veredito,
    Verificador,
    codigo_do_contador,
    codigo_em,
    gerar_segredo,
    uri_de_provisionamento,
)


def _b32(semente_ascii: str) -> str:
    """A semente da RFC, na forma que o app le.

    Convertida aqui em vez de transcrita: transcrever base32 a mao e uma fonte
    de erro que o teste nao pegaria -- ele passaria a testar a transcricao.
    """
    return base64.b32encode(semente_ascii.encode("ascii")).decode("ascii").rstrip("=")


#: RFC 4226, Apendice D. Semente ASCII "12345678901234567890".
SEMENTE_SHA1 = _b32("12345678901234567890")
#: RFC 6238, Apendice B.
SEMENTE_SHA256 = _b32("12345678901234567890123456789012")
SEMENTE_SHA512 = _b32("1234567890123456789012345678901234567890123456789012345678901234")


class TestVetoresPublicados:
    """Gabarito externo. Se um destes quebrar, a implementacao esta errada."""

    @pytest.mark.parametrize(
        ("contador", "esperado"),
        [
            (0, "755224"),
            (1, "287082"),
            (2, "359152"),
            (3, "969429"),
            (4, "338314"),
            (5, "254676"),
            (6, "287922"),
            (7, "162583"),
            (8, "399871"),
            (9, "520489"),
        ],
    )
    def test_hotp_rfc4226_apendice_d(self, contador: int, esperado: str) -> None:
        assert codigo_do_contador(SEMENTE_SHA1, contador) == esperado

    @pytest.mark.parametrize(
        ("instante", "esperado"),
        [
            (59, "94287082"),
            (1111111109, "07081804"),
            (1111111111, "14050471"),
            (1234567890, "89005924"),
            (2000000000, "69279037"),
            (20000000000, "65353130"),
        ],
    )
    def test_totp_rfc6238_sha1(self, instante: int, esperado: str) -> None:
        assert codigo_em(SEMENTE_SHA1, instante, digitos=8) == esperado

    @pytest.mark.parametrize(
        ("instante", "esperado"),
        [
            (59, "46119246"),
            (1111111109, "68084774"),
            (1111111111, "67062674"),
            (1234567890, "91819424"),
            (2000000000, "90698825"),
            (20000000000, "77737706"),
        ],
    )
    def test_totp_rfc6238_sha256(self, instante: int, esperado: str) -> None:
        assert codigo_em(SEMENTE_SHA256, instante, digitos=8, algoritmo="sha256") == esperado

    @pytest.mark.parametrize(
        ("instante", "esperado"),
        [
            (59, "90693936"),
            (1111111109, "25091201"),
            (1111111111, "99943326"),
            (1234567890, "93441116"),
            (2000000000, "38618901"),
            (20000000000, "47863826"),
        ],
    )
    def test_totp_rfc6238_sha512(self, instante: int, esperado: str) -> None:
        assert codigo_em(SEMENTE_SHA512, instante, digitos=8, algoritmo="sha512") == esperado

    def test_os_parametros_sao_os_que_o_google_authenticator_assume(self) -> None:
        # ⚠️ Divergir aqui produz codigo que nunca casa, com o operador
        # convencido de que digitou errado.
        assert (PERIODO_S, DIGITOS) == (30, 6)


def _verificador(tmp_path: Path, segredo: str | None = SEMENTE_SHA1) -> Verificador:
    tmp_path.mkdir(parents=True, exist_ok=True)
    seg = tmp_path / "segredo"
    if segredo is not None:
        seg.write_text(segredo + "\n", encoding="utf-8")
    return Verificador(caminho_segredo=seg, caminho_estado=tmp_path / "estado.json")


class TestTravaUmAntiReplay:
    def test_o_mesmo_codigo_nao_serve_duas_vezes(self, tmp_path: Path) -> None:
        v = _verificador(tmp_path)
        agora = 1_700_000_000.0
        certo = codigo_em(SEMENTE_SHA1, agora)
        assert v.verificar(certo, agora) is Veredito.ACEITO
        # ⚠️ Ainda dentro dos mesmos 30 s: sem esta trava, quem lesse o canal
        # teria a janela inteira para repetir o clique.
        assert v.verificar(certo, agora + 1) is Veredito.REPLAY

    def test_replay_nao_alimenta_o_congelamento(self, tmp_path: Path) -> None:
        # Reapresentar por engano nao pode congelar o operador: nao e tentativa
        # de adivinhar codigo, e o codigo estava certo.
        v = _verificador(tmp_path)
        agora = 1_700_000_000.0
        certo = codigo_em(SEMENTE_SHA1, agora)
        v.verificar(certo, agora)
        for i in range(TETO_ERROS + 2):
            assert v.verificar(certo, agora + 1 + i) is Veredito.REPLAY
        assert not v.congelado(agora + 20)

    def test_o_estado_atravessa_processos(self, tmp_path: Path) -> None:
        # ⚠️ Armadilha 2: cada ciclo do monitor e um processo novo. Em memoria,
        # esta trava nasceria zerada a cada 5 min e nunca dispararia.
        agora = 1_700_000_000.0
        certo = codigo_em(SEMENTE_SHA1, agora)
        assert _verificador(tmp_path).verificar(certo, agora) is Veredito.ACEITO
        assert _verificador(tmp_path).verificar(certo, agora + 1) is Veredito.REPLAY


class TestTravaDoisJanela:
    def test_aceita_um_intervalo_para_tras_e_para_frente(self, tmp_path: Path) -> None:
        agora = 1_700_000_000.0
        for passo in (-PERIODO_S, 0, PERIODO_S):
            v = _verificador(tmp_path / f"j{passo}")
            assert v.verificar(codigo_em(SEMENTE_SHA1, agora + passo), agora) is Veredito.ACEITO

    def test_recusa_dois_intervalos_de_distancia(self, tmp_path: Path) -> None:
        # Tres codigos validos por vez, nao trinta.
        agora = 1_700_000_000.0
        for passo in (-2 * PERIODO_S, 2 * PERIODO_S):
            v = _verificador(tmp_path / f"j{passo}")
            assert v.verificar(codigo_em(SEMENTE_SHA1, agora + passo), agora) is Veredito.RECUSADO


class TestTravaTresForcaBruta:
    def test_cinco_erros_em_dez_minutos_congelam_por_uma_hora(self, tmp_path: Path) -> None:
        v = _verificador(tmp_path)
        agora = 1_700_000_000.0
        for i in range(TETO_ERROS):
            assert v.verificar("000000", agora + i) is Veredito.RECUSADO
        # ⚠️ Ate o codigo CERTO e recusado enquanto congelado -- senao o
        # congelamento nao seria congelamento.
        certo = codigo_em(SEMENTE_SHA1, agora + TETO_ERROS)
        assert v.verificar(certo, agora + TETO_ERROS) is Veredito.CONGELADO
        assert v.verificar(certo, agora + CONGELAMENTO_S - 1) is Veredito.CONGELADO

    def test_o_congelamento_expira_sozinho(self, tmp_path: Path) -> None:
        v = _verificador(tmp_path)
        agora = 1_700_000_000.0
        for i in range(TETO_ERROS):
            v.verificar("000000", agora + i)
        # O congelamento conta a partir do ULTIMO erro, nao do primeiro.
        depois = agora + (TETO_ERROS - 1) + CONGELAMENTO_S + 1
        assert v.verificar(codigo_em(SEMENTE_SHA1, depois), depois) is Veredito.ACEITO

    def test_erros_espalhados_alem_da_janela_nao_somam(self, tmp_path: Path) -> None:
        # Errar uma vez por dia durante uma semana nao e forca bruta.
        v = _verificador(tmp_path)
        agora = 1_700_000_000.0
        for i in range(TETO_ERROS * 2):
            assert v.verificar("000000", agora + i * (JANELA_ERROS_S + 1)) is Veredito.RECUSADO

    def test_acerto_zera_o_contador_de_erros(self, tmp_path: Path) -> None:
        v = _verificador(tmp_path)
        agora = 1_700_000_000.0
        for i in range(TETO_ERROS - 1):
            v.verificar("000000", agora + i)
        v.verificar(codigo_em(SEMENTE_SHA1, agora + 10), agora + 10)
        for i in range(TETO_ERROS - 1):
            assert v.verificar("000000", agora + 20 + i) is Veredito.RECUSADO
        assert not v.congelado(agora + 30)


class TestFalhasQueNaoPodemVirarSilencio:
    def test_sem_segredo_nao_e_recusado_e_sim_sem_segredo(self, tmp_path: Path) -> None:
        # ⚠️ Colapsar os dois faria "a trava nao existe" parecer "a trava
        # funcionou". Quem le o veredito decide se pode agir.
        v = _verificador(tmp_path, segredo=None)
        assert v.verificar("000000", 1_700_000_000.0) is Veredito.SEM_SEGREDO

    def test_estado_corrompido_comeca_limpo_sem_derrubar_o_ciclo(self, tmp_path: Path) -> None:
        (tmp_path / "segredo").write_text(SEMENTE_SHA1, encoding="utf-8")
        (tmp_path / "estado.json").write_text("{isto nao e json", encoding="utf-8")
        v = Verificador(
            caminho_segredo=tmp_path / "segredo", caminho_estado=tmp_path / "estado.json"
        )
        agora = 1_700_000_000.0
        assert v.verificar(codigo_em(SEMENTE_SHA1, agora), agora) is Veredito.ACEITO

    def test_codigo_com_espacos_e_aceito(self, tmp_path: Path) -> None:
        # O app mostra "123 456", e e assim que a pessoa digita.
        v = _verificador(tmp_path)
        agora = 1_700_000_000.0
        certo = codigo_em(SEMENTE_SHA1, agora)
        assert v.verificar(f"{certo[:3]} {certo[3:]} ", agora) is Veredito.ACEITO


class TestSegredoEProvisionamento:
    def test_o_segredo_gerado_tem_160_bits_e_e_base32_valido(self) -> None:
        s = gerar_segredo()
        assert "=" not in s
        assert len(base64.b32decode(s + "=" * (-len(s) % 8))) == 20

    def test_dois_segredos_seguidos_nao_se_repetem(self) -> None:
        assert gerar_segredo() != gerar_segredo()

    def test_a_uri_leva_o_segredo_e_os_parametros_do_app(self) -> None:
        uri = uri_de_provisionamento(SEMENTE_SHA1, conta="rodrigo")
        assert uri.startswith("otpauth://totp/")
        assert f"secret={SEMENTE_SHA1}" in uri
        assert "digits=6" in uri and "period=30" in uri and "algorithm=SHA1" in uri
