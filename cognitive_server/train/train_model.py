"""
Cognitive Server - Model Training Pipeline
Trains a binary model for Cognitive Load Score prediction.

Architecture: Input(8) -> Dense(32,ReLU) -> Dropout(0.2) -> Dense(16,ReLU) -> Dense(1,Sigmoid*100)

Usage:
  python train/train_model.py          # Train and export model
"""

import os, sys, json, math, io, struct
import numpy as np
from datetime import datetime

# Resolve paths
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(ROOT)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Configuration
EPOCHS = 300
LR = 0.01
BATCH_SIZE = 32
TRAIN_SPLIT = 0.8
VAL_SPLIT = 0.1
PATIENCE = 40
MIN_DELTA = 0.0005
DROPOUT = 0.2

NORM_BOUNDS = {
    'kpm': (0, 120), 'switch_rate': (0, 30), 'scroll_entropy': (0, 5000),
    'mouse_entropy': (0, 1), 'idle_ratio': (0, 1), 'tab_count': (0, 30),
    'domain_switches': (0, 15), 'time_of_day': (-1, 1),
}

FEATURE_COLS = ['kpm', 'switch_rate', 'scroll_entropy', 'mouse_entropy',
                'idle_ratio', 'tab_count', 'domain_switches', 'time_of_day']

MODEL_PATH = os.path.join(REPO_ROOT, 'cognitive_server', 'ml', 'model.clsmdl')
TFLITE_PATH = os.path.join(REPO_ROOT, 'cognitive_server', 'ml', 'model.tflite')
METRICS_PATH = os.path.join(HERE, 'training_metrics.json')
DATA_PATH = os.path.join(REPO_ROOT, 'cognitive_server', 'train', 'synthetic_data.csv')


# ---- Activation + Loss ----

def relu(x):
    return np.maximum(0, x)


def relu_deriv(x):
    return (x > 0).astype(np.float32)


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -500, 500)))


def sigmoid_deriv_from_sig(sig):
    return sig * (1.0 - sig)


def mse_loss_grad(pred, target):
    """MSE loss gradient: 2/n * (pred - target)."""
    n = len(pred)
    return 2.0 * (pred - target) / n


# ---- Dense Layer ----

class DenseLayer:
    def __init__(self, in_dim, out_dim, activation='relu', dropout=0.0):
        self.act = activation
        self.dropout = dropout
        self.training = True
        limit = math.sqrt(6.0 / (in_dim + out_dim))
        self.W = np.random.uniform(-limit, limit, (in_dim, out_dim)).astype(np.float32)
        self.b = np.zeros(out_dim, dtype=np.float32)
        self.X_in = None
        self.Z = None
        self.A = None
        self.mask = None

    def forward(self, X, training=False):
        self.training = training
        self.X_in = X
        self.Z = np.dot(X, self.W) + self.b

        if self.act == 'relu':
            self.A = relu(self.Z)
        elif self.act == 'sigmoid':
            self.A = sigmoid(self.Z)
        else:
            self.A = self.Z.copy()

        if training and self.dropout > 0:
            self.mask = (np.random.rand(*self.A.shape) > self.dropout).astype(np.float32)
            self.A = self.A * self.mask / (1.0 - self.dropout)
        else:
            self.mask = np.ones_like(self.A)
        return self.A

    def backward(self, dA, lr):
        """Backpropagate gradient dA through this layer."""
        dA = dA * self.mask / max(1.0 - self.dropout, 1e-8)

        # Gradient pre-activation
        if self.act == 'relu':
            dZ = dA * relu_deriv(self.Z)
        elif self.act == 'sigmoid':
            sig_deriv = sigmoid_deriv_from_sig(self.A)
            dZ = dA * sig_deriv
        else:
            dZ = dA

        # Weight/bias gradients
        n = self.X_in.shape[0]
        dW = np.dot(self.X_in.T, dZ) / n
        db = np.mean(dZ, axis=0)

        # Gradient for previous layer
        dX = np.dot(dZ, self.W.T)

        # Update
        self.W -= lr * dW
        self.b -= lr * db
        return dX

    def get_weights(self):
        return {'W': self.W.copy(), 'b': self.b.copy(), 'act': self.act}


# ---- Model ----

class CLSModel:
    def __init__(self):
        self.l1 = DenseLayer(8, 32, 'relu')
        self.l2 = DenseLayer(32, 16, 'relu', DROPOUT)
        self.l3 = DenseLayer(16, 1, 'sigmoid')
        self.layers = [self.l1, self.l2, self.l3]

    def predict_raw(self, X, training=False):
        """Returns sigmoid output in [0, 1]."""
        out = self.l1.forward(X, training)
        out = self.l2.forward(out, training)
        out = self.l3.forward(out, training)
        return out

    def predict(self, X, training=False):
        """Returns CLS score in [0, 100]."""
        return self.predict_raw(X, training) * 100.0

    def train_step(self, Xb, yb, lr):
        """Train one mini-batch. yb in [0, 100]."""
        if yb.ndim == 1:
            yb = yb.reshape(-1, 1)

        # Forward
        raw_out = self.predict_raw(Xb, training=True)
        pred_scaled = raw_out * 100.0

        # Loss: MSE in [0,100] space
        loss = float(np.mean((pred_scaled - yb) ** 2))

        # Gradient: d(MSE)/d(pred_scaled) = 2*(pred-y)/n
        n = len(yb)
        d_pred_scaled = 2.0 * (pred_scaled - yb) / n

        # Chain through *100 scaling: d_raw = d_pred_scaled * 100
        d_raw = d_pred_scaled * 100.0

        # Backprop through layers (reverse)
        d = self.l3.backward(d_raw, lr)
        d = self.l2.backward(d, lr)
        d = self.l1.backward(d, lr)
        return loss

    def evaluate(self, X, y):
        pred = self.predict(X, training=False).flatten()
        mse = float(np.mean((pred - y) ** 2))
        mae = float(np.mean(np.abs(pred - y)))
        return mse, mae

    def get_all_weights(self):
        return [l.get_weights() for l in self.layers]

    def set_all_weights(self, wlist):
        for layer, w in zip(self.layers, wlist):
            layer.W, layer.b = w['W'].copy(), w['b'].copy()


# ---- Data ----

def load_data(path):
    import csv
    X, y = [], []
    with open(path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            feat = [float(row.get(c, 0)) for c in FEATURE_COLS]
            X.append(feat)
            y.append(float(row.get('cls_target', 0)))
    return np.array(X, dtype=np.float32), np.array(y, dtype=np.float32)


def normalize(X):
    Xn = np.zeros_like(X)
    for i, col in enumerate(FEATURE_COLS):
        lo, hi = NORM_BOUNDS.get(col, (0, 1))
        if hi > lo:
            Xn[:, i] = np.clip((X[:, i] - lo) / (hi - lo), 0, 1)
        else:
            Xn[:, i] = X[:, i]
    return Xn


# ---- Model Export (.clsmdl) ----

def export_model(model, path):
    ACT = {'relu': 0, 'sigmoid': 1, 'linear': 2}
    buf = io.BytesIO()
    buf.write(b'CLSM')
    buf.write(struct.pack('<I', 1))
    buf.write(struct.pack('<I', len(model.layers)))
    for layer in model.layers:
        w = layer.W.astype(np.float32)
        b = layer.b.astype(np.float32)
        buf.write(struct.pack('<IIB', w.shape[0], w.shape[1], ACT.get(layer.act, 2)))
        buf.write(b'\x00\x00\x00')
        buf.write(w.tobytes())
        buf.write(b.tobytes())
    data = buf.getvalue()
    with open(path, 'wb') as f:
        f.write(data)
    return len(data)


def export_tflite(model, path):
    """Export model to TFLite format using flatbuffers."""
    try:
        import tensorflow as tf

        # Build a TensorFlow model with the same architecture
        tf_model = tf.keras.Sequential([
            tf.keras.layers.Dense(32, activation='relu', input_shape=(8,)),
            tf.keras.layers.Dropout(0.2),
            tf.keras.layers.Dense(16, activation='relu'),
            tf.keras.layers.Dense(1, activation='sigmoid'),
        ])

        # Set weights from trained model
        l1_w, l1_b = model.l1.W, model.l1.b
        l2_w, l2_b = model.l2.W, model.l2.b
        l3_w, l3_b = model.l3.W, model.l3.b

        tf_model.layers[0].set_weights([l1_w, l1_b])
        tf_model.layers[2].set_weights([l2_w, l2_b])
        tf_model.layers[3].set_weights([l3_w, l3_b])

        # Convert to TFLite
        converter = tf.lite.TFLiteConverter.from_keras_model(tf_model)
        converter.optimizations = [tf.lite.Optimize.DEFAULT]
        tflite_model = converter.convert()

        with open(path, 'wb') as f:
            f.write(tflite_model)

        return len(tflite_model)
    except ImportError:
        print("  TensorFlow not available — skipping TFLite export")
        return 0
    except Exception as e:
        print(f"  TFLite export failed: {e}")
        return 0


# ---- Training ----

def train():
    print("=" * 60)
    print("Cognitive CLS Model Training")
    print("=" * 60)

    if not os.path.exists(DATA_PATH):
        print("Generating data...")
        from cognitive_server.train.generate_data import main as gen
        gen()

    print(f"Loading data...")
    X_raw, y = load_data(DATA_PATH)
    X = normalize(X_raw)
    print(f"Dataset: {len(X)} samples")
    print(f"CLS: {y.min():.1f}-{y.max():.1f}, mean={y.mean():.1f}")

    # Shuffle and split
    idx = np.random.permutation(len(X))
    te = int(len(X) * TRAIN_SPLIT)
    va = int(len(X) * (TRAIN_SPLIT + VAL_SPLIT))
    X_tr, y_tr = X[idx[:te]], y[idx[:te]]
    X_va, y_va = X[idx[te:va]], y[idx[te:va]]
    X_te, y_te = X[idx[va:]], y[idx[va:]]
    print(f"Split: train={len(X_tr)}, val={len(X_va)}, test={len(X_te)}")

    np.random.seed(42)
    model = CLSModel()

    best_val = float('inf')
    patience_cnt = 0
    best_w = None

    print(f"\nTraining {EPOCHS} epochs (bs={BATCH_SIZE}, lr={LR})...")
    print("-" * 70)

    for ep in range(EPOCHS):
        perm = np.random.permutation(len(X_tr))
        n_batches = math.ceil(len(X_tr) / BATCH_SIZE)
        ep_loss = 0

        for i in range(n_batches):
            s = i * BATCH_SIZE
            e = min(s + BATCH_SIZE, len(X_tr))
            batch_idx = perm[s:e]
            ep_loss += model.train_step(X_tr[batch_idx], y_tr[batch_idx], LR)

        ep_loss /= n_batches
        val_mse, val_mae = model.evaluate(X_va, y_va)
        train_mse, train_mae = model.evaluate(X_tr, y_tr)

        if (ep + 1) % 10 == 0 or ep == 0:
            print(f"Ep {ep+1:4d}/{EPOCHS} | loss={ep_loss:.4f} | "
                  f"val_mse={val_mse:.4f} val_mae={val_mae:.2f} | "
                  f"train_mae={train_mae:.2f}")

        # Early stopping on validation MSE
        if val_mse < best_val - MIN_DELTA:
            best_val = val_mse
            patience_cnt = 0
            best_w = model.get_all_weights()
        else:
            patience_cnt += 1

        if patience_cnt >= PATIENCE:
            print(f"\nEarly stopping at epoch {ep + 1}")
            break

    # Reload best weights
    if best_w:
        model.set_all_weights(best_w)

    # Final evaluation
    train_mse, train_mae = model.evaluate(X_tr, y_tr)
    val_mse, val_mae = model.evaluate(X_va, y_va)
    test_mse, test_mae = model.evaluate(X_te, y_te)
    preds = model.predict(X_te, training=False).flatten()
    corr = float(np.corrcoef(preds, y_te)[0, 1])

    print("\n" + "=" * 70)
    print("Final Evaluation (best weights)")
    print("=" * 70)
    print(f"Train  - MSE: {train_mse:.4f}, MAE: {train_mae:.2f}")
    print(f"Val    - MSE: {val_mse:.4f}, MAE: {val_mae:.2f}")
    print(f"Test   - MSE: {test_mse:.4f}, MAE: {test_mae:.2f}")
    print(f"Correlation: {corr:.4f}")

    # Export .clsmdl
    size = export_model(model, MODEL_PATH)
    print(f"\nModel (.clsmdl): {MODEL_PATH} ({size / 1024:.1f} KB)")
    if size > 10 * 1024 * 1024:
        print("WARNING: Model exceeds 10MB")
    else:
        print("PASS: Model < 10MB")

    # Export .tflite
    tflite_size = export_tflite(model, TFLITE_PATH)
    if tflite_size > 0:
        print(f"Model (.tflite): {TFLITE_PATH} ({tflite_size / 1024:.1f} KB)")
        if tflite_size > 10 * 1024 * 1024:
            print("WARNING: TFLite model exceeds 10MB")
        else:
            print("PASS: TFLite model < 10MB")
    else:
        print("TFLite export skipped")

    # Metrics
    metrics = {
        'training_date': datetime.now().isoformat(),
        'epochs_trained': ep + 1,
        'train_mse': train_mse, 'val_mse': val_mse,
        'test_mse': test_mse, 'test_mae': test_mae,
        'correlation': corr,
        'model_target': 'r>0.7 with self-reported focus',
        'architecture': {'input': 8, 'hidden1': 32, 'hidden2': 16, 'output_0_to_100': True},
        'dataset': {'total': len(X), 'train': len(X_tr), 'val': len(X_va), 'test': len(X_te)},
        'models': {
            'clsmdl': {'path': MODEL_PATH, 'size_kb': size / 1024},
            'tflite': {'path': TFLITE_PATH, 'size_kb': tflite_size / 1024} if tflite_size > 0 else None,
        },
    }
    with open(METRICS_PATH, 'w') as f:
        json.dump(metrics, f, indent=2)
    print(f"Metrics: {METRICS_PATH}")

    # Benchmark
    print("\nInference Benchmark (1000 iterations):")
    from cognitive_server.ml.inference import compute_cls_model
    feat = {'kpm': 0.5, 'switch_rate': 0.5, 'scroll_entropy': 0.3,
            'mouse_entropy': 0.4, 'idle_ratio': 0.2, 'tab_count': 0.5,
            'domain_switches': 0.2, 'time_of_day': 0.0}
    import time
    t0 = time.perf_counter()
    for _ in range(1000):
        cls, conf = compute_cls_model(feat)
    avg_ms = (time.perf_counter() - t0) / 1000 * 1000
    print(f"  {avg_ms:.3f} ms avg | Target < 50ms | {'PASS' if avg_ms < 50 else 'NEEDS OPTIMIZATION'}")
    print(f"  Sample: CLS={cls:.2f}, confidence={conf:.3f}")


if __name__ == "__main__":
    train()