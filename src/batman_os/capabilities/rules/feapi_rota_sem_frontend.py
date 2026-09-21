"""Capability bespoke FE-API "rota sem cliente frontend" (Vol.IV Cap.17).

Não generalizada numa Skill — cross-reference com DUAS particularidades
que não se repetem juntas em nenhum outro código: (1) extração de rotas
via AST (`@router.get("/x")` etc., mesma lógica de `collect_fastapi_
routes` do legado) DENTRO do próprio arquivo, comparada contra um texto
agregado de OUTRO conjunto de arquivos (frontend); (2) MÚLTIPLOS achados
por arquivo, cada um com `chave=path` distinto (diferente de ARCH-003,
que produz no máximo 1 achado por página — aqui um único arquivo de
router pode ter N rotas, cada uma gerando seu PRÓPRIO fingerprint porque
`chave` varia).

A descoberta (`descoberta_arquivos.py::_resultado_feapi`) empacota o
conteúdo do PRÓPRIO arquivo de rotas + o texto agregado do frontend
(lido uma única vez) como JSON — o handler faz o parsing AST e a
comparação."""

from __future__ import annotations

import ast
import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from batman_os.capabilities.capability_contract import (
    AcceptanceTest,
    CapabilityImplementation,
    ResultadoEsperado,
)
from batman_os.capabilities.operator import ExecutionContext
from batman_os.foundation.types import CapabilityId
from batman_os.runtime.capability_engine import CapabilityDefinition, SideEffects

_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete"})
_ADMIN_ROUTERS = frozenset({"admin.py", "deploy.py", "metrics.py", "release.py"})
#: Prefixos de rota que NAO se espera encontrar no frontend.
#:
#: ⚠️ `/webhooks` entrou em 2026-09-04, e e falso positivo ESTRUTURAL, nao
#: circunstancial: um webhook e chamado pelo PROVEDOR externo (Mercado Pago,
#: Stripe, GitHub), nunca pelo navegador do usuario. Exigir metodo no frontend
#: para ele e pedir que o cliente chame o proprio servidor no lugar do provedor
#: -- e se alguem "corrigisse" o achado criando esse metodo, teria construido
#: exatamente a superficie que um webhook nao deve ter.
#: Medido no radar-preditivo: `/webhooks/mercadopago` era 1 dos 4 achados.
_ADMIN_PREFIXES = ("/health", "/admin/", "/webhooks", "/webhook/")


class RegraFeApiSpec(BaseModel):
    codigo: str
    agente: str
    severidade: str
    categoria: str
    titulo: str
    causa: str
    remediacao: str
    caminho_frontend_api: str


class EntradaFeApi(BaseModel):
    tipo: Literal["feapi"] = "feapi"
    caminho: str
    conteudo: str | None = None
    regra: RegraFeApiSpec


class AchadoFeApi(BaseModel):
    codigo: str
    agente: str
    severidade: str
    categoria: str
    titulo: str
    descricao: str
    causa: str
    remediacao: str
    arquivo: str
    chave: str = ""
    fingerprint: str = ""


class SaidaFeApi(BaseModel):
    achados: list[AchadoFeApi] = Field(default_factory=list)


class EntradaInvalida(Exception):
    """Levantada quando `entrada` não satisfaz `EntradaFeApi` — vira
    SCHEMA_REJECTION no Execution Engine (Vol.III Cap.12)."""


def _computar_fingerprint(
    agente: str, categoria: str, caminho: str, codigo: str, chave: str
) -> str:
    import hashlib

    normalizado = caminho.replace("\\", "/")
    bruto = f"{agente}|{categoria}|{normalizado}|{codigo}|{chave}"
    return hashlib.sha1(bruto.encode("utf-8")).hexdigest()


def _rotas_do_arquivo(texto: str) -> list[tuple[str, int]]:
    try:
        tree = ast.parse(texto)
    except SyntaxError:
        return []
    rotas: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        for dec in getattr(node, "decorator_list", []):
            if (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and dec.func.attr in _HTTP_METHODS
                and dec.args
                and isinstance(dec.args[0], ast.Constant)
                and isinstance(dec.args[0].value, str)
            ):
                rotas.append((dec.args[0].value, dec.lineno))
    return rotas


def avaliar_feapi(entrada: Any, contexto: ExecutionContext) -> Any:
    del contexto
    try:
        dados = EntradaFeApi.model_validate(entrada)
    except Exception as exc:
        raise EntradaInvalida(str(exc)) from exc

    payload = json.loads(dados.conteudo) if dados.conteudo else {}
    api_src: str = payload.get("api_src", "")
    frontend_text: str = payload.get("frontend_text", "")

    nome_arquivo = dados.caminho.replace("\\", "/").rsplit("/", 1)[-1]
    if nome_arquivo in _ADMIN_ROUTERS:
        return SaidaFeApi(achados=[]).model_dump()

    regra = dados.regra
    achados: list[AchadoFeApi] = []
    for path, lineno in _rotas_do_arquivo(api_src):
        if any(path.startswith(pfx) for pfx in _ADMIN_PREFIXES):
            continue
        probe = path.split("{")[0].rstrip("/")
        if not probe or probe in frontend_text:
            continue
        fingerprint = _computar_fingerprint(
            regra.agente, regra.categoria, dados.caminho, regra.codigo, path
        )
        achados.append(
            AchadoFeApi(
                codigo=regra.codigo,
                agente=regra.agente,
                severidade=regra.severidade,
                categoria=regra.categoria,
                titulo=regra.titulo,
                descricao=(
                    f"Rota {path} (em {dados.caminho}) não aparece em "
                    f"{regra.caminho_frontend_api} (linha {lineno})."
                ),
                causa=regra.causa,
                remediacao=regra.remediacao,
                arquivo=dados.caminho,
                chave=path,
                fingerprint=fingerprint,
            )
        )

    return SaidaFeApi(achados=achados).model_dump()


def construir_implementacao() -> CapabilityImplementation:
    definicao = CapabilityDefinition(
        id=CapabilityId("feapi-rota-sem-frontend"),
        name="FE-API rota sem cliente frontend",
        version="1.0.0",
        input_schema=EntradaFeApi.model_json_schema(),
        output_schema=SaidaFeApi.model_json_schema(),
        deterministic=True,
        side_effects=SideEffects.NONE,
        idempotent=True,
    )

    _regra_teste = {
        "codigo": "FE-API",
        "agente": "frontend-engineer",
        "severidade": "medium",
        "categoria": "api",
        "titulo": "t",
        "causa": "c",
        "remediacao": "r",
        "caminho_frontend_api": "frontend/src/api",
    }
    entrada_sucesso = {
        "caminho": "api/routers/pedidos.py",
        "conteudo": json.dumps(
            {
                "api_src": "@router.get('/pedidos')\ndef listar():\n    pass\n",
                "frontend_text": "nada relacionado aqui",
            }
        ),
        "regra": _regra_teste,
    }
    entrada_ok = {
        "caminho": "api/routers/pedidos.py",
        "conteudo": json.dumps(
            {
                "api_src": "@router.get('/pedidos')\ndef listar():\n    pass\n",
                "frontend_text": "fetch('/pedidos')",
            }
        ),
        "regra": _regra_teste,
    }

    return CapabilityImplementation(
        definition=definicao,
        handler=avaliar_feapi,
        acceptance_tests=[
            AcceptanceTest(
                name="rota-sem-cliente-frontend-dispara-um-achado",
                entrada=entrada_sucesso,
                resultado_esperado=ResultadoEsperado.SUCCESS,
                matcher_saida=lambda saida: len(saida["achados"]) == 1,
            ),
            AcceptanceTest(
                name="rota-com-cliente-frontend-nao-dispara",
                entrada=entrada_ok,
                resultado_esperado=ResultadoEsperado.SUCCESS,
                matcher_saida=lambda saida: saida["achados"] == [],
            ),
            AcceptanceTest(
                name="entrada-sem-campo-obrigatorio-e-rejeitada",
                entrada={"conteudo": "x"},  # falta 'caminho'
                resultado_esperado=ResultadoEsperado.SCHEMA_REJECTION,
            ),
            AcceptanceTest(
                name="regra-com-tipo-de-campo-invalido-e-tratada-como-falha-de-invocacao",
                entrada={
                    "caminho": "api/routers/x.py",
                    "conteudo": "x",
                    "regra": {"severidade": 123},
                },
                resultado_esperado=ResultadoEsperado.TIMEOUT,
            ),
        ],
    )
