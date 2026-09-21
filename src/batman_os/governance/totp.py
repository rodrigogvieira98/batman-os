"""TOTP (RFC 6238) para ação forte no Discord — `CYB-TOTP-001`.

**O problema que este módulo resolve.** O botão no Discord vai existir, e a
pergunta do DEV foi *"isso também não pode significar mais uma
vulnerabilidade"*. A assinatura Ed25519 prova que a requisição veio do Discord;
o snowflake prova qual conta clicou. Nenhum dos dois prova que **você** clicou:
uma sessão sequestrada do Discord carrega os dois.

⚠️ **Por que não uma senha**, que foi a primeira ideia do DEV: senha digitada no
canal vale para sempre se vazar. Um código TOTP expira em 30 s, e o que trafega
pelo Discord é só o código — nunca o segredo.

**O segredo é gerado na VPS e nunca sai dela.** Não passa pela Google, não passa
pelo Discord, não há conta nem serviço externo. O app do celular só faz a
matemática do padrão sobre um segredo que você transcreveu uma vez.

**Zero dependência nova.** O projeto tem quatro (`pydantic`, `anthropic`,
`tenacity`, `python-dotenv`) e TOTP cabe em `hmac`, `hashlib`, `base64`,
`secrets` e `struct` — tudo da biblioteca padrão.

## As quatro travas

1. **Anti-replay.** Código usado não serve de novo, nem dentro dos seus 30 s.
   Sem isso, quem lesse o canal teria a janela inteira para repetir o clique.
2. **Janela de ±1 intervalo, e só.** Cobre relógio dessincronizado sem ampliar
   a superfície: três códigos válidos por vez, não trinta.
3. **Teto de 5 erros em 10 min congela por 1 h.** Seis dígitos são um milhão de
   possibilidades; sem teto, força bruta é viável no tempo de vida de um
   incidente.
4. **O snowflake continua exigido.** O TOTP prova QUEM; o ID prova QUE CONTA.
   Um substitui o outro em nada.

⚠️ **Cada ciclo do monitor é um processo novo** (armadilha 2 do `CLAUDE.md`: é
cron de 5 min, não daemon). Contador de erro e lista de códigos usados em
memória nasceriam zerados a cada ciclo, e as travas 1 e 3 nunca disparariam.
Por isso o estado vive em disco.

## O limite, declarado

`root` na VPS lê o arquivo do segredo. Isso não é contornável: o ciclo roda como
`radar` e precisa ler o segredo para conferir o código. **Mas quem tem root não
precisa do botão** — executaria `ufw` direto. O TOTP protege contra
comprometimento do **Discord**, que é o canal exposto; não contra
comprometimento da própria VPS.

Reset é por SSH (`batman totp reset`): a chave SSH já é o fator forte do DEV,
está em dois lugares e tem passphrase. É mais robusta que código de recuperação
em papel, e não cria um segundo caminho de recuperação para defender.
"""

from __future__ import annotations

import base64
import hmac
import json
import logging
import os
import secrets
import struct
import time
from enum import StrEnum
from pathlib import Path
from urllib.parse import quote

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

ENV_SEGREDO = "BATMANOS_TOTP_SEGREDO"
ENV_ESTADO = "BATMANOS_TOTP_ESTADO"
SEGREDO_PADRAO = Path(".batman-os") / "totp_segredo"
ESTADO_PADRAO = Path(".batman-os") / "totp_estado.json"

#: Os parametros do Google Authenticator. Nao sao configuraveis de proposito:
#: o app do celular assume estes valores, e divergir aqui produz um codigo que
#: nunca casa -- com o operador convencido de que digitou errado.
PERIODO_S = 30
DIGITOS = 6
ALGORITMO = "sha1"

#: Trava 2. `1` significa: intervalo anterior, atual e proximo.
JANELA_INTERVALOS = 1

#: Trava 3.
TETO_ERROS = 5
JANELA_ERROS_S = 600.0
CONGELAMENTO_S = 3600.0

#: Quanto tempo um contador ja usado fica na lista da trava 1. Precisa cobrir a
#: janela inteira mais folga; alem disso o codigo ja nao seria aceito de todo
#: jeito, e guardar mais so faz o arquivo crescer.
RETENCAO_USADOS_S = PERIODO_S * (2 * JANELA_INTERVALOS + 2)

#: 20 bytes = 160 bits, o tamanho recomendado pela RFC 4226 (secao 4, R6).
BYTES_DO_SEGREDO = 20


class Veredito(StrEnum):
    """O que aconteceu com o codigo apresentado.

    ⚠️ `RECUSADO`, `REPLAY` e `CONGELADO` sao coisas diferentes e nao podem
    colapsar num `False`: o operador precisa saber se errou o codigo, se
    reapresentou um usado ou se esta de castigo -- e o journal precisa
    distinguir erro honesto de tentativa de repeticao.
    """

    ACEITO = "aceito"
    RECUSADO = "recusado"
    REPLAY = "replay"
    CONGELADO = "congelado"
    SEM_SEGREDO = "sem-segredo"


def gerar_segredo(n_bytes: int = BYTES_DO_SEGREDO) -> str:
    """Um segredo novo, em base32 sem preenchimento -- a forma que o app le."""
    return base64.b32encode(secrets.token_bytes(n_bytes)).decode("ascii").rstrip("=")


def _chave(segredo_b32: str) -> bytes:
    limpo = segredo_b32.strip().replace(" ", "").upper()
    return base64.b32decode(limpo + "=" * (-len(limpo) % 8), casefold=True)


def codigo_do_contador(
    segredo_b32: str,
    contador: int,
    *,
    digitos: int = DIGITOS,
    algoritmo: str = ALGORITMO,
) -> str:
    """HOTP da RFC 4226: o truncamento dinamico, sem atalho.

    Separado de `codigo_em` porque e ele que os vetores de teste da RFC
    exercitam -- e vetor publicado e gabarito externo, que e a unica forma de
    verificacao que nao depende de eu achar que acertei.
    """
    mac = hmac.new(_chave(segredo_b32), struct.pack(">Q", contador), algoritmo).digest()
    deslocamento = mac[-1] & 0x0F
    trecho = struct.unpack(">I", mac[deslocamento : deslocamento + 4])[0] & 0x7FFFFFFF
    return str(trecho % (10**digitos)).zfill(digitos)


def codigo_em(
    segredo_b32: str,
    instante_s: float,
    *,
    digitos: int = DIGITOS,
    periodo_s: int = PERIODO_S,
    algoritmo: str = ALGORITMO,
) -> str:
    """TOTP da RFC 6238: HOTP sobre o numero do intervalo de tempo."""
    return codigo_do_contador(
        segredo_b32, int(instante_s // periodo_s), digitos=digitos, algoritmo=algoritmo
    )


def uri_de_provisionamento(segredo_b32: str, *, conta: str, emissor: str = "Batman OS") -> str:
    """A `otpauth://` que vira QR code no app.

    ⚠️ Esta string CONTEM o segredo. Ela existe para ser lida uma vez, no
    terminal da VPS, por quem ja esta autenticado por SSH -- nunca para trafegar
    pelo Discord, por e-mail ou por qualquer canal que guarde historico.
    """
    rotulo = quote(f"{emissor}:{conta}", safe="")
    return (
        f"otpauth://totp/{rotulo}?secret={segredo_b32}&issuer={quote(emissor)}"
        f"&algorithm={ALGORITMO.upper()}&digits={DIGITOS}&period={PERIODO_S}"
    )


class EstadoTotp(BaseModel):
    """O que precisa atravessar ciclos para as travas 1 e 3 existirem."""

    #: contador ja usado -> quando foi usado. Trava 1.
    usados: dict[str, float] = Field(default_factory=dict)
    #: carimbos dos erros recentes. Trava 3.
    erros: list[float] = Field(default_factory=list)
    congelado_ate: float = 0.0


def _caminho(env: str, padrao: Path) -> Path:
    bruto = os.environ.get(env, "")
    return Path(bruto) if bruto.strip() else padrao


class Verificador:
    """Confere um codigo contra o segredo da VPS, com as quatro travas.

    Estado em disco porque o ciclo e um processo novo a cada 5 min.
    """

    def __init__(
        self,
        *,
        caminho_segredo: Path | None = None,
        caminho_estado: Path | None = None,
    ) -> None:
        self.caminho_segredo = caminho_segredo or _caminho(ENV_SEGREDO, SEGREDO_PADRAO)
        self.caminho_estado = caminho_estado or _caminho(ENV_ESTADO, ESTADO_PADRAO)
        self.estado = self._carregar()

    def _carregar(self) -> EstadoTotp:
        try:
            bruto = json.loads(self.caminho_estado.read_text(encoding="utf-8"))
            return EstadoTotp.model_validate(bruto)
        except FileNotFoundError:
            return EstadoTotp()
        except (OSError, ValueError) as erro:
            # ⚠️ Estado ilegivel comeca limpo e AVISA. Derrubar o ciclo por causa
            # do arquivo de anti-replay trocaria uma falha pequena por cegueira.
            logger.warning("estado do TOTP ilegivel, recomecando limpo: %s", erro)
            return EstadoTotp()

    def persistir(self) -> None:
        self.caminho_estado.parent.mkdir(parents=True, exist_ok=True)
        temporario = self.caminho_estado.with_suffix(".tmp")
        temporario.write_text(self.estado.model_dump_json(indent=2) + "\n", encoding="utf-8")
        temporario.replace(self.caminho_estado)

    def segredo(self) -> str | None:
        try:
            bruto = self.caminho_segredo.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return bruto or None

    def congelado(self, agora_s: float) -> bool:
        return agora_s < self.estado.congelado_ate

    def _limpar(self, agora_s: float) -> None:
        self.estado.usados = {
            c: q for c, q in self.estado.usados.items() if agora_s - q <= RETENCAO_USADOS_S
        }
        self.estado.erros = [q for q in self.estado.erros if agora_s - q <= JANELA_ERROS_S]

    def _errou(self, agora_s: float) -> None:
        self.estado.erros.append(agora_s)
        if len(self.estado.erros) >= TETO_ERROS:
            self.estado.congelado_ate = agora_s + CONGELAMENTO_S
            self.estado.erros = []
            logger.warning(
                "TOTP congelado por %.0f s apos %d erros em %.0f s",
                CONGELAMENTO_S,
                TETO_ERROS,
                JANELA_ERROS_S,
            )

    def verificar(self, apresentado: str, agora_s: float | None = None) -> Veredito:
        """O veredito, e o estado ja persistido quando ele muda."""
        agora_s = time.time() if agora_s is None else agora_s
        self._limpar(agora_s)

        if self.congelado(agora_s):
            self.persistir()
            return Veredito.CONGELADO

        segredo = self.segredo()
        if segredo is None:
            # ⚠️ Sem segredo NAO e "recusado": e "a trava nao existe". Quem le o
            # veredito precisa distinguir para nao concluir que o TOTP protegeu
            # alguma coisa quando ele sequer estava configurado.
            return Veredito.SEM_SEGREDO

        limpo = apresentado.strip().replace(" ", "")
        atual = int(agora_s // PERIODO_S)
        for passo in range(-JANELA_INTERVALOS, JANELA_INTERVALOS + 1):
            contador = atual + passo
            esperado = codigo_do_contador(segredo, contador)
            if not hmac.compare_digest(esperado, limpo):
                continue
            if str(contador) in self.estado.usados:
                # ⚠️ Codigo certo, mas ja gasto. Nao conta como erro de
                # digitacao: nao alimenta a trava 3, para que reapresentar por
                # engano nao congele o operador.
                self.persistir()
                return Veredito.REPLAY
            self.estado.usados[str(contador)] = agora_s
            self.estado.erros = []
            self.persistir()
            return Veredito.ACEITO

        self._errou(agora_s)
        self.persistir()
        return Veredito.RECUSADO
