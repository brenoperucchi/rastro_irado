"""Primitivas de z-score do IRAI — puras, sem dependências pesadas.

Isoladas em módulo próprio para poderem ser testadas sem importar o engine
inteiro (que puxa numpy/pandas/pykalman). Reutilizadas pelo z-score
multivariate atual e pelo sinal pair z-score (mesma normalização √t).
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


# ── Pair z-score (sinal pairwise dinâmico) ────────────────────────────────
# Design: .planning/notes/pair-zscore-signal.md
# TODO:   .planning/todos/pending/implementar-pair-zscore-dots.md
#
# O "par ativo" é o fator de maior |β| no estado do Kalman naquele bar. O sinal
# vem da reversão do resíduo (retorno do target vs. o explicado pelo par).
# Tudo aqui é puro/testável; o engine v2 apenas alimenta betas/retornos e
# persiste os campos resultantes no IRAISnapshot.

PAIR_THRESHOLD = 1.5      # z_pair de disparo (≈1.5σ), default do design
PAIR_SIGMA_WINDOW = 20    # janela do σ rolling do resíduo (observações)
PAIR_MIN_BETA = 0.1       # |β| mínimo para o par ser considerado válido


def select_active_pair(betas, labels, min_beta: float = PAIR_MIN_BETA,
                       sigmas=None, min_sigma_frac: float = 0.25):
    """Escolhe o fator de maior |β| (o "par ativo") entre ``betas[1:]``.

    ``betas[0]`` é o intercepto; ``betas[1:]`` alinham 1-a-1 com ``labels``
    (mesma ordem de active_factors no engine). Retorna
    ``{"label", "beta", "index"}`` ou ``None`` se nenhum |β| ≥ ``min_beta``.

    ``sigmas`` (opcional, alinhado a ``labels``): exclui da eleição fatores de
    volatilidade quase-nula — cujo σ < ``min_sigma_frac`` × mediana das σ da
    cesta. Um fator de σ ~0 (ex: bond ETF de curta duração) tem retorno ~0, o
    resíduo do par vira ~o retorno do próprio target e o z-score degenera
    (valores absurdos). Sem ``sigmas``, o comportamento é o legado (só |β|).
    """
    sigma_floor = 0.0
    if sigmas:
        pos = sorted(s for s in sigmas if s and s > 0)
        if pos:
            sigma_floor = min_sigma_frac * pos[len(pos) // 2]  # frac da mediana

    best = None
    for i, label in enumerate(labels):
        if i + 1 >= len(betas):
            break
        b = betas[i + 1]
        if abs(b) < min_beta:
            continue
        if sigmas and i < len(sigmas) and sigmas[i] and sigmas[i] < sigma_floor:
            continue  # fator de vol quase-nula — par degenerado, pula
        if best is None or abs(b) > abs(best["beta"]):
            best = {"label": label, "beta": b, "index": i}
    return best


def pairwise_residual(ret_target: float, beta: float, ret_factor: float) -> float:
    """Resíduo do par: retorno do target menos o retorno explicado pelo fator."""
    return ret_target - beta * ret_factor


def rolling_sigma(residuals, window: int = PAIR_SIGMA_WINDOW) -> float:
    """Desvio-padrão populacional dos últimos ``window`` resíduos.

    Retorna 0.0 com menos de 2 amostras — nesse caso ``normalized_zscore``
    aplica o piso ``DEFAULT_SIGMA``, evitando z_pair explosivo no começo da
    janela (antes de acumular resíduos suficientes).
    """
    recent = residuals[-window:] if window and window > 0 else list(residuals)
    n = len(recent)
    if n < 2:
        return 0.0
    mean = sum(recent) / n
    var = sum((x - mean) ** 2 for x in recent) / n
    return var ** 0.5


def pair_zscore(residuals, window: int = PAIR_SIGMA_WINDOW) -> float:
    """Z de reversão à média do resíduo do par: (r_t − μ_janela)/σ_janela.

    Sem √t: σ já é a dispersão dos NÍVEIS de resíduo na janela (não vol-por-barra).
    Centrado na média rolling → limitado a ~√3 para resíduo em tendência suave (em
    vez de explodir); dispara só em deslocamento genuíno do equilíbrio. Com <2
    amostras ou σ≈0 retorna 0.0 (sem sinal) — NÃO usa o piso DEFAULT_SIGMA: no par,
    σ degenerado deve ZERAR o sinal, não inflá-lo (oposto do z de fator).
    """
    recent = residuals[-window:] if window and window > 0 else list(residuals)
    n = len(recent)
    if n < 2:
        return 0.0
    mean = sum(recent) / n
    sigma = (sum((x - mean) ** 2 for x in recent) / n) ** 0.5
    if sigma <= 0:
        return 0.0
    return (recent[-1] - mean) / sigma


def pair_signal(z_pair: float, beta: float, threshold: float = PAIR_THRESHOLD) -> str:
    """Sinal de compra/venda do par por reversão do resíduo.

    Tabela (.planning/notes/pair-zscore-signal.md):
      |z_pair| < threshold                    -> "neutral"
      β < 0 (inverso):  z ≤ -thr -> "buy" ,  z ≥ +thr -> "sell"
      β > 0 (direto) :  z ≤ -thr -> "sell",  z ≥ +thr -> "buy"
    """
    if beta == 0 or abs(z_pair) < threshold:
        return "neutral"
    below = z_pair <= -threshold
    if beta < 0:                      # relação inversa
        return "buy" if below else "sell"
    return "sell" if below else "buy"  # relação direta
