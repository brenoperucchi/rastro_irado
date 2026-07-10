"""Primitivas de z-score do IRAI — puras, sem dependências pesadas.

Isoladas em módulo próprio para poderem ser testadas sem importar o engine
inteiro (que puxa numpy/pandas/pykalman). Reutilizadas pelo z-score
multivariate atual e pelo futuro sinal pair z-score (mesma normalização √t).
"""

# Piso de sigma: um fator sem σ calibrado (ausente) ou com σ degenerado
# (≈0 por dado constante/glitch) NÃO deve zerar o sinal. É o mesmo valor que o
# engine usa como default para σ ausente, para que os casos "ausente" e
# "gravado como 0" degradem de forma idêntica em vez de matar a contribuição
# do fator silenciosamente.
DEFAULT_SIGMA = 0.01


def normalized_zscore(ret: float, sigma: float, sqrt_t: float) -> float:
    """z-score de retorno normalizado por tempo: ret / (σ·√t).

    σ ≤ 0 usa o piso DEFAULT_SIGMA (o engine antes retornava 0.0 nesse caso,
    matando silenciosamente a contribuição do fator). √t ≤ 0 retorna 0.0 por
    segurança (evita divisão por zero no início da sessão).
    """
    if sqrt_t <= 0:
        return 0.0
    eff_sigma = sigma if sigma > 0 else DEFAULT_SIGMA
    return ret / (eff_sigma * sqrt_t)
