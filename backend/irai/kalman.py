"""
Wrapper causal para Filtro de Kalman, evitando lookahead bias.
"""
from typing import Optional, Tuple
import numpy as np
from pykalman import KalmanFilter

class KalmanFilterWrapper:
    """
    Wrapper para aplicar o Filtro de Kalman em séries temporais
    de forma estritamente causal, utilizando apenas `filter_update`.
    """
    
    def __init__(self, 
                 n_dim_state: int, 
                 n_dim_obs: int, 
                 transition_covariance: float = 1e-5,
                 observation_covariance: float = 1e-3,
                 initial_state_mean: Optional[np.ndarray] = None,
                 initial_state_covariance: Optional[np.ndarray] = None):
        """
        Inicializa o filtro de Kalman.
        
        Args:
            n_dim_state: Número de dimensões do estado (e.g., betas dos fatores + intercept).
            n_dim_obs: Número de dimensões da observação (e.g., 1 para o preço do target).
            transition_covariance: Multiplicador para a matriz de covariância de transição (ruído do sistema).
            observation_covariance: Multiplicador para a covariância da observação (ruído de medição).
            initial_state_mean: Estado inicial (prior). Se None, usa zeros.
            initial_state_covariance: Covariância inicial do estado. Se None, usa identidade.
        """
        self.n_dim_state = n_dim_state
        self.n_dim_obs = n_dim_obs
        
        # A matriz de transição assume que os betas são independentes e andam como random walk (Identidade)
        transition_matrices = np.eye(n_dim_state)
        
        # O ruído do sistema (transition covariance) controla a rapidez com que os betas podem mudar
        trans_cov = transition_covariance * np.eye(n_dim_state)
        
        # O ruído da observação (observation covariance)
        obs_cov = observation_covariance * np.eye(n_dim_obs)
        
        # Estado inicial
        if initial_state_mean is None:
            self.state_mean = np.zeros(n_dim_state)
        else:
            self.state_mean = np.asarray(initial_state_mean)
            
        if initial_state_covariance is None:
            self.state_covariance = np.eye(n_dim_state)
        else:
            self.state_covariance = np.asarray(initial_state_covariance)
            
        self.kf = KalmanFilter(
            transition_matrices=transition_matrices,
            observation_matrices=None, # Definido dinamicamente no update()
            transition_covariance=trans_cov,
            observation_covariance=obs_cov,
            initial_state_mean=self.state_mean,
            initial_state_covariance=self.state_covariance
        )
        
    def update(self, observation: np.ndarray, observation_matrix: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        Atualiza o estado utilizando a nova observação causalmente.
        
        Args:
            observation: O valor observado (e.g., variação do target ou preço). Shape (1,) ou (n_dim_obs,).
            observation_matrix: A matriz que relaciona o estado à observação (e.g., os fatores). Shape (1, n_dim_state)
            
        Returns:
            Tuple contendo (state_mean, state_covariance) após a atualização.
        """
        observation = np.asarray(observation)
        observation_matrix = np.asarray(observation_matrix)
        
        # Pykalman's filter_update:
        # Pega a média/cov do estado anterior, a matriz de observação ATUAL, e o valor observado ATUAL.
        self.state_mean, self.state_covariance = self.kf.filter_update(
            filtered_state_mean=self.state_mean,
            filtered_state_covariance=self.state_covariance,
            observation=observation,
            observation_matrix=observation_matrix
        )
        
        return self.state_mean, self.state_covariance

    def predict(self, observation_matrix: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Time update SEM correção — para quando não há observação do target.

        É o tratamento canônico de observação faltante: o pykalman mascara a
        observação (`observation=None`) e o ganho de Kalman vai a zero, então a
        média fica intocada (transição é identidade) e a covariância cresce em Q.
        Semântica: "o tempo passou, mas nada do target foi observado ainda".

        Usado no pré-mercado da B3 (~180 barras M5 em que os fatores globais
        negociam e o target não). Alimentar `update()` com uma observação
        fabricada — a falsa de antes ou um zero — apagaria o prior: a memória
        efetiva do filtro é sqrt(R/Q) = sqrt(1e-3/1e-5) = 10 barras.

        Nota: não dá pra reusar `update(observation=None)` — o `np.asarray(None)`
        de lá viraria `array(None, dtype=object)` e quebraria o ramo mascarado
        do pykalman. Daí o método separado.
        """
        self.state_mean, self.state_covariance = self.kf.filter_update(
            filtered_state_mean=self.state_mean,
            filtered_state_covariance=self.state_covariance,
            observation=None,                                   # -> ganho = 0
            observation_matrix=np.asarray(observation_matrix),  # shape ainda exigido
        )
        return self.state_mean, self.state_covariance

    def get_state(self) -> Tuple[np.ndarray, np.ndarray]:
        """Retorna o estado atual."""
        return self.state_mean, self.state_covariance

    def set_state(self, state_mean: np.ndarray, state_covariance: np.ndarray):
        """Define o estado a partir de valores salvos."""
        self.state_mean = np.asarray(state_mean)
        self.state_covariance = np.asarray(state_covariance)
