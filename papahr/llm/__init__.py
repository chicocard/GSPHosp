"""Integração opcional de LLM local usando llama-cpp-python.

Modelos recomendados (baixo consumo de RAM/CPU):
 - Microsoft Phi-3 Mini 4K Instruct (3.8B params, Q4_K_M ~ 2.6GB)
 - TinyLlama-1.1B Chat (Q4_K_M ~ 700MB, CPU muito leve)
 - Google Gemma 2 2B (Q4_K_M ~ 1.5GB)
 - Mistral-7B Instruct v0.3 (Q4_K_M ~ 4.2GB, mais inteligente)

O código tenta importar llama_cpp. Se não disponível, um modo 'mock' de
fallback instrui o usuário como instalar o backend.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

import polars as pl

from papahr.config import get_settings
from papahr.logging_config import get_logger

log = get_logger(__name__)

# Prompt templates voltados para análise de dados da saúde (SUS/DATASUS)

SYSTEM_PROMPT = (
    "Você é um analista de dados especializado em Saúde Pública no Brasil, "
    "com domínio dos sistemas DATASUS (SIA/SIH/CNES/SIGTAP), epidemiologia, "
    "gestão hospitalar e oncologia. Responda em português de forma objetiva, "
    "precisa e técnica. Quando possível, quantifique e estratifique os "
    "resultados por município, estabelecimento, complexidade e financiamento. "
    "Indique limitações dos dados quando encontrar valores ausentes ou "
    "procedimentos revogados."
)

SUMMARY_PROMPT = (
    "Analise o seguinte conjunto de dados da produção ambulatorial/hospitalar "
    "do SUS e gere um resumo executivo curto com: (1) volume total de "
    "atendimentos e valor financeiro; (2) top 5 procedimentos por frequência; "
    "(3) perfil de complexidade e financiamento; (4) distribuição geográfica "
    "relevante; (5) insights acionáveis para gestão hospitalar.\n\n"
    "DADOS (resumo em CSV):\n{csv_summary}\n\n"
    "ANÁLISE:"
)

TREND_PROMPT = (
    "Identifique tendências temporais na seguinte série mensal de produção "
    "do SUS. Destaque meses de pico, queda abrupta e crescimento sustentado. "
    "Sugira possíveis causas (sazonalidade, epidemias, alteração de "
    "financiamento etc.).\n\nSÉRIE:\n{series}\n\nANÁLISE:"
)

FREE_PROMPT = (
    "Com base no seguinte resumo dos dados de produção do SUS, responda:\n\n"
    "{user_question}\n\n"
    "RESUMO DOS DADOS:\n{csv_summary}\n\nRESPOSTA:"
)


def _dataframe_summary(df: pl.DataFrame, max_rows: int = 500) -> str:
    """Extrai um resumo leve do DataFrame para contexto do LLM."""
    if df.is_empty():
        return "DataFrame vazio."
    cols_of_interest = [c for c in [
        "Ano/Mes de Atendimento", "Ano/Mes processamento",
        "Procedimentos realizados", "Grupo de Procedimentos",
        "Complexidade Procedimento", "Complexidade do Procedimento",
        "Tipo de Financiamento", "Financiamento",
        "Municipio Residencia", "Estado Residencia",
        "Estabelecimento CNES", "Hospital (CNES)",
        "Nome da Morbidade (CID)", "Nome da Mobirdade (CID)",
        "Frequencia", "Valor Total", "Valor Aprovado", "Valor Apresentado",
        "Situacao da AIH",
    ] if c in df.columns]
    if not cols_of_interest:
        cols_of_interest = df.columns[: min(12, len(df.columns))]
    sample = df.select(cols_of_interest).head(max_rows)
    parts = [
        f"Shape: {df.height} linhas x {df.width} colunas",
        f"Colunas: {', '.join(df.columns)}",
        "",
        "Amostra:",
        sample.write_csv(separator=";"),
    ]
    # Agregações rápidas
    for col in [c for c in ["Frequencia", "Valor Total", "Valor Aprovado"] if c in df.columns]:
        if df[col].dtype.is_numeric():
            s = df[col].sum()
            parts.append(f"\nTotal {col}: {s:,.2f}")
    return "\n".join(parts)[:16000]


# ---------------------------------------------------------------------------
# Implementação real (llama-cpp-python) + fallback
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class LLMConfig:
    model_path: Optional[Path]
    n_ctx: int = 4096
    n_threads: int = 4
    n_gpu_layers: int = 0
    temperature: float = 0.1
    max_tokens: int = 1024
    top_p: float = 0.9
    seed: int = 42


@dataclass
class LLMResponse:
    text: str
    tokens_generated: int = 0
    tokens_prompt: int = 0
    elapsed_seconds: float = 0.0
    usage: dict = field(default_factory=dict)


class LLMAnalyst:
    """Analista de dados com LLM local.

    Recomendação: usar Phi-3 Mini 4K Instruct GGUF Q4_K_M (microsoft/Phi-3-mini-4k-instruct-gguf).
    Download via Hugging Face:
    https://huggingface.co/microsoft/Phi-3-mini-4k-instruct-gguf/resolve/main/Phi-3-mini-4k-instruct-q4.gguf
    ~2.6GB, CPU 4 threads: ~8-15 tok/s, memória residente ~3.2GB.
    """

    def __init__(self, config: Optional[LLMConfig] = None) -> None:
        settings = get_settings()
        self.config = config or LLMConfig(
            model_path=settings.llm_model_path,
            n_ctx=settings.llm_n_ctx,
            n_threads=settings.llm_n_threads,
            n_gpu_layers=settings.llm_n_gpu_layers,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
        )
        self._model = None
        self._llama_available = False
        try:
            import llama_cpp  # noqa: F401
            self._llama_available = True
        except Exception:
            self._llama_available = False
            log.warning("llama-cpp-python nao instalado. Use pip install llama-cpp-python")

    @property
    def available(self) -> bool:
        return self._llama_available and self.config.model_path and Path(self.config.model_path).exists()

    def install_hint(self) -> str:
        return (
            "Para habilitar o LLM local (opcional — o restante do GSPHosp funciona sem ele):\n"
            "1. Instale o wheel pré-compilado (CPU) na venv do projeto:\n"
            "   .venv\\Scripts\\python.exe -m pip install llama-cpp-python "
            "--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu\n"
            "2. Baixe um modelo GGUF, ex.: Phi-3-mini-4k-instruct-q4.gguf (~2,3 GB):\n"
            "   https://huggingface.co/microsoft/Phi-3-mini-4k-instruct-gguf\n"
            "3. Em Configurações, informe o caminho do .gguf (ou defina "
            "PAPAHHR_LLM_MODEL_PATH no .env).\n"
            "4. (Opcional) PAPAHHR_LLM_N_GPU_LAYERS > 0 se tiver GPU com wheel CUDA/Vulkan."
        )

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        if not self._llama_available:
            raise RuntimeError("llama-cpp-python nao disponivel.\n" + self.install_hint())
        if not self.config.model_path or not Path(self.config.model_path).exists():
            raise RuntimeError(
                f"Modelo nao encontrado em {self.config.model_path}.\n" + self.install_hint()
            )
        from llama_cpp import Llama

        log.info(
            "Carregando modelo LLM",
            path=str(self.config.model_path),
            n_ctx=self.config.n_ctx,
            n_threads=self.config.n_threads,
            gpu_layers=self.config.n_gpu_layers,
        )
        self._model = Llama(
            model_path=str(self.config.model_path),
            n_ctx=self.config.n_ctx,
            n_threads=self.config.n_threads,
            n_gpu_layers=self.config.n_gpu_layers,
            seed=self.config.seed,
            verbose=False,
        )
        return self._model

    def _chat_template(self, messages: list[dict[str, str]]) -> str:
        """Aplica ChatML (funciona em Phi-3, Mistral, Gemma, TinyLlama)."""
        out = []
        for m in messages:
            role = m["role"]
            content = m["content"]
            if role == "system":
                out.append(f"<|system|>\n{content}<|end|>")
            elif role == "user":
                out.append(f"<|user|>\n{content}<|end|>")
            else:
                out.append(f"<|assistant|>\n{content}<|end|>")
        out.append("<|assistant|>\n")
        return "\n".join(out)

    def generate(self, prompt: str, *, system: str = SYSTEM_PROMPT, stream: bool = False) -> LLMResponse | Iterator[str]:
        import time
        t0 = time.perf_counter()
        model = self._ensure_model()
        full_prompt = self._chat_template([
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ])

        if stream:
            def _stream() -> Iterator[str]:
                for tok in model(
                    full_prompt,
                    max_tokens=self.config.max_tokens,
                    temperature=self.config.temperature,
                    top_p=self.config.top_p,
                    stream=True,
                ):
                    chunk = tok["choices"][0]["text"]
                    if chunk:
                        yield chunk
            return _stream()

        out = model(
            full_prompt,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            top_p=self.config.top_p,
        )
        text = out["choices"][0]["text"].strip()
        usage = out.get("usage") or {}
        elapsed = time.perf_counter() - t0
        return LLMResponse(
            text=text,
            tokens_generated=int(usage.get("completion_tokens", 0)),
            tokens_prompt=int(usage.get("prompt_tokens", 0)),
            elapsed_seconds=elapsed,
            usage=usage,
        )

    # ---- Tarefas de alto nível orientadas a dados ----------------------

    def summarize_dataset(self, df: pl.DataFrame) -> LLMResponse:
        """Gera resumo executivo do conjunto de dados."""
        summary = _dataframe_summary(df)
        prompt = SUMMARY_PROMPT.format(csv_summary=summary)
        return self.generate(prompt)  # type: ignore[return-value]

    def answer_question(self, df: pl.DataFrame, question: str) -> LLMResponse:
        """Responde pergunta livre sobre os dados."""
        summary = _dataframe_summary(df)
        prompt = FREE_PROMPT.format(user_question=question, csv_summary=summary)
        return self.generate(prompt)  # type: ignore[return-value]

    def analyze_trend(self, df: pl.DataFrame, date_col: str, value_col: str) -> LLMResponse:
        """Analisa tendências temporais."""
        if date_col not in df.columns or value_col not in df.columns:
            raise ValueError(f"Colunas ausentes: {date_col} ou {value_col}")
        agg = df.group_by(date_col).agg(pl.col(value_col).sum()).sort(date_col)
        series_csv = agg.write_csv(separator=";")
        prompt = TREND_PROMPT.format(series=series_csv)
        return self.generate(prompt)  # type: ignore[return-value]
