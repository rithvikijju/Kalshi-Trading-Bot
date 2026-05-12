"""SFM (State Frequency Memory) cell — optional realized-vol predictor.

Reference: Zhang, Aggarwal, Qi, "Stock Price Prediction via Discovering
Multi-Frequency Trading Patterns", KDD 2017.

Implementation follows Equations 7-19 exactly:

  Eq 7-9:   S_t ∈ ℝ^{D×K} state-frequency memory matrix, decomposed into
              real (Re S_t) and imaginary (Im S_t) parts on cos/sin basis.
              S_t = F_t ⊙ S_{t-1} + (i_t ⊙ ĉ_t) [e^{jω_1 t}, ..., e^{jω_K t}]^T

  Eq 12-14: state forget gate f_t^state ∈ ℝ^D, frequency forget gate
              f_t^freq ∈ ℝ^K, joint forget gate F_t = f_t^state ⊗ f_t^freq

  Eq 15-16: input gate i_t and modulation ĉ_t (LSTM-standard)

  Eq 17:    state-only reconstruction c_t = tanh(A_t · u_a + b_a)
              where A_t = |S_t| is the amplitude

  Eq 18-19: output gate o_t and hidden state h_t = o_t ⊙ tanh(c_t)

Use case in this bot: predict next-N-min realized vol from a window of
recent log returns. The predicted σ feeds the lognormal fair value.

NOTE: Disabled by default (CFG['sfm_enabled'] = False). The empirical
sample bank's σ matching already handles regime variation. SFM is
worth turning on only if the rolling-window σ proves systematically
biased on accumulated paper trades.
"""
from __future__ import annotations
import numpy as np

try:
    import torch
    from torch import nn
    HAVE_TORCH = True
except ImportError:
    HAVE_TORCH = False
    nn = None


if HAVE_TORCH:
    class SFMCell(nn.Module):
        """State Frequency Memory cell.

        Args:
            input_size: dim of input vector x_t
            hidden_size: D (number of states)
            num_frequencies: K (number of frequency components)

        forward(x_t, prev_state) → (h_t, new_state)
        prev_state: (Re_S, Im_S, h_prev) tuple
        Re_S, Im_S: (batch, D, K)
        h_prev:     (batch, D)
        """
        def __init__(self, input_size: int, hidden_size: int,
                     num_frequencies: int = 4):
            super().__init__()
            self.input_size = input_size
            self.D = hidden_size
            self.K = num_frequencies
            self.t = 0    # timestep counter (rolled into state in practice)

            # Pre-compute frequencies ω_k = 2πk/K, evenly spaced on [0, 2π]
            omegas = 2 * np.pi * np.arange(1, num_frequencies + 1) / num_frequencies
            self.register_buffer("omegas", torch.tensor(omegas, dtype=torch.float32))

            # Eq 12: state forget gate (output dim D)
            self.W_fs = nn.Linear(input_size + hidden_size, hidden_size)
            # Eq 13: frequency forget gate (output dim K)
            self.W_ff = nn.Linear(input_size + hidden_size, num_frequencies)
            # Eq 15: input gate (D)
            self.W_i  = nn.Linear(input_size + hidden_size, hidden_size)
            # Eq 16: input modulation ĉ (D)
            self.W_c  = nn.Linear(input_size + hidden_size, hidden_size)
            # Eq 17: inverse-transform composition u_a (K → 1) per state
            self.u_a  = nn.Parameter(torch.randn(num_frequencies))
            self.b_a  = nn.Parameter(torch.zeros(hidden_size))
            # Eq 18: output gate (D); takes c_t too in standard LSTM peephole style
            self.W_o  = nn.Linear(input_size + hidden_size + hidden_size, hidden_size)

        def init_state(self, batch_size: int, device=None):
            d = device or next(self.parameters()).device
            return (
                torch.zeros(batch_size, self.D, self.K, device=d),    # Re S
                torch.zeros(batch_size, self.D, self.K, device=d),    # Im S
                torch.zeros(batch_size, self.D, device=d),            # h
            )

        def forward(self, x, prev_state, t=None):
            Re_S_prev, Im_S_prev, h_prev = prev_state
            B = x.shape[0]
            if t is None: t = self.t; self.t += 1

            xh = torch.cat([x, h_prev], dim=-1)               # (B, in+D)

            # Gates (Eqs 12-16)
            fs = torch.sigmoid(self.W_fs(xh))                  # (B, D)
            ff = torch.sigmoid(self.W_ff(xh))                  # (B, K)
            F  = fs.unsqueeze(-1) * ff.unsqueeze(-2)           # (B, D, K), Eq 14
            i  = torch.sigmoid(self.W_i(xh))                   # (B, D)
            c_tilde = torch.tanh(self.W_c(xh))                 # (B, D)

            # Frequency basis at time t (Eqs 8-9)
            omegas_t = self.omegas * float(t)                  # (K,)
            cos_t = torch.cos(omegas_t)                        # (K,)
            sin_t = torch.sin(omegas_t)                        # (K,)

            # Update real/imag state (Eqs 8-9)
            input_term = (i * c_tilde).unsqueeze(-1)            # (B, D, 1)
            Re_S = F * Re_S_prev + input_term * cos_t          # (B, D, K)
            Im_S = F * Im_S_prev + input_term * sin_t          # (B, D, K)

            # Amplitude (|S_t|) and state-only reconstruction (Eq 17)
            A = torch.sqrt(Re_S**2 + Im_S**2 + 1e-9)            # (B, D, K)
            c = torch.tanh(A @ self.u_a + self.b_a)             # (B, D)

            # Output gate + hidden state (Eqs 18-19)
            xho = torch.cat([x, h_prev, c], dim=-1)
            o = torch.sigmoid(self.W_o(xho))
            h = o * torch.tanh(c)

            return h, (Re_S, Im_S, h)


    class SFMVolPredictor(nn.Module):
        """Wrap SFMCell with a regression head to predict next-N-min realized vol.

        Input: window of recent log_ret values (B, T, 1)
        Output: predicted annualized σ (B,)
        """
        def __init__(self, input_size: int = 1, hidden_size: int = 32,
                     num_frequencies: int = 4):
            super().__init__()
            self.cell = SFMCell(input_size, hidden_size, num_frequencies)
            self.head = nn.Sequential(
                nn.Linear(hidden_size, 16),
                nn.ReLU(),
                nn.Linear(16, 1),
                nn.Softplus(),    # ensure σ > 0
            )

        def forward(self, x):
            B, T, _ = x.shape
            state = self.cell.init_state(B, device=x.device)
            self.cell.t = 0
            for t in range(T):
                _, state = self.cell(x[:, t], state, t=t)
            h_final = state[2]
            return self.head(h_final).squeeze(-1)


    def predict_sigma_with_sfm(model: SFMVolPredictor, recent_returns,
                                 device=None) -> float:
        """Run a trained SFM model on a window of recent log returns,
        return predicted annualized σ."""
        device = device or next(model.parameters()).device
        x = torch.tensor(recent_returns, dtype=torch.float32, device=device)
        x = x.reshape(1, -1, 1)
        with torch.no_grad():
            sigma = float(model(x).item())
        return sigma

else:
    # PyTorch not available — module-level placeholders
    class SFMCell:           # type: ignore
        def __init__(self, *a, **kw):
            raise ImportError("PyTorch required for SFM. pip install torch")
    class SFMVolPredictor:   # type: ignore
        def __init__(self, *a, **kw):
            raise ImportError("PyTorch required for SFM. pip install torch")
    def predict_sigma_with_sfm(*a, **kw):
        raise ImportError("PyTorch required for SFM. pip install torch")
