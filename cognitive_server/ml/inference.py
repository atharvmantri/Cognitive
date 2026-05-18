"""
Cognitive Server - ML Inference
TFLite + custom binary (.clsmdl) model runtime + heuristic fallback.
Phase 2: Supports trained model for Cognitive Load Score prediction.
"""

import os, struct, json
import numpy as np


# ═══════════════════════════════════════════════════════════════════
# Custom Binary Model (.clsmdl)
# ═══════════════════════════════════════════════════════════════════
# Format (little-endian):
#   [4B magic "CLSM"][4B version=1][4B num_layers]
#   Per layer:
#     [4B in_dim][4B out_dim][1B activation][3B padding]
#     [in_dim*out_dim * 4B weights (row-major, shape [in, out])]
#     [out_dim * 4B biases]
#
# Inference: output = relu(dot(input, W) + b)  where W shape is [in, out]

class CLSSimpleModel:
    def __init__(self, layers):
        self.layers = layers  # list of {'W': ndarray[in,out], 'b': ndarray[out], 'act': str}

    def predict(self, x):
        if x.ndim == 1:
            x = x.reshape(1, -1)
        out = x.astype(np.float32)
        for layer in self.layers:
            out = np.dot(out, layer['W']) + layer['b']
            if layer['act'] == 'relu':
                out = np.maximum(0, out)
            elif layer['act'] == 'sigmoid':
                out = 1.0 / (1.0 + np.exp(-np.clip(out, -500, 500)))
        return out.flatten() * 100.0  # Scale to [0, 100]


def load_custom_model(filepath):
    with open(filepath, 'rb') as f:
        data = f.read()

    if data[:4] != b'CLSM':
        raise ValueError(f"Invalid model magic: {data[:4]}")

    version = struct.unpack('<I', data[4:8])[0]
    num_layers = struct.unpack('<I', data[8:12])[0]

    if version != 1:
        raise ValueError(f"Unsupported version: {version}")
    if not (1 <= num_layers <= 20):
        raise ValueError(f"Invalid layer count: {num_layers}")

    ACTIONS = {0: 'relu', 1: 'sigmoid', 2: 'linear'}
    layers = []
    offset = 12

    for _ in range(num_layers):
        in_dim, out_dim, act_code = struct.unpack('<IIB', data[offset:offset + 9])
        offset += 9 + 3  # skip padding

        w_bytes = in_dim * out_dim * 4
        W = np.frombuffer(data[offset:offset + w_bytes], dtype=np.float32).reshape(in_dim, out_dim)
        offset += w_bytes

        b_bytes = out_dim * 4
        b = np.frombuffer(data[offset:offset + b_bytes], dtype=np.float32)
        offset += b_bytes

        layers.append({'W': W.copy(), 'b': b.copy(), 'act': ACTIONS.get(act_code, 'relu')})

    return CLSSimpleModel(layers)


# ═══════════════════════════════════════════════════════════════════
# TFLite Model (optional, requires tflite-runtime)
# ═══════════════════════════════════════════════════════════════════

_tflite_interpreter = None


def _load_tflite(path):
    global _tflite_interpreter
    if _tflite_interpreter is not None:
        return _tflite_interpreter
    try:
        from tflite_runtime.interpreter import Interpreter
        interp = Interpreter(model_path=path)
        interp.allocate_tensors()
        _tflite_interpreter = interp
    except ImportError:
        try:
            import tensorflow.lite as tflite
            interp = tflite.Interpreter(model_path=path)
            interp.allocate_tensors()
            _tflite_interpreter = interp
        except Exception:
            _tflite_interpreter = None
    except Exception:
        _tflite_interpreter = None
    return _tflite_interpreter


def _run_tflite(interp, features):
    inp = np.array([features], dtype=np.float32)
    out = interp.get_output_details()
    inp_d = interp.get_input_details()
    interp.set_tensor(inp_d[0]['index'], inp)
    interp.invoke()
    score = float(interp.get_tensor(out[0]['index'])[0][0])
    score = score * 100.0  # Scale sigmoid [0,1] to [0,100]
    score = max(0.0, min(100.0, score))
    conf = 1.0 - abs(score - 50) / 100.0
    if len(out) > 1:
        conf = float(interp.get_tensor(out[1]['index'])[0][0])
    return round(score, 2), round(max(0.1, min(1.0, conf)), 3)


# ═══════════════════════════════════════════════════════════════════
# Heuristic Fallback (Phase 1 legacy)
# ═══════════════════════════════════════════════════════════════════

_HW = {"kpm": 0.20, "switch_rate": 0.20, "scroll_entropy": 0.10,
        "mouse_entropy": 0.10, "idle_ratio": 0.15, "tab_count": 0.10,
        "domain_switches": 0.10, "time_of_day": 0.05}


def _heuristic(features):
    score = 0.0
    for k, w in _HW.items():
        v = features.get(k, 0.0)
        if k == "idle_ratio":
            score += v * w
        elif k == "time_of_day":
            score += ((v + 1) / 2) * w * 0.5
        elif k in ("kpm", "switch_rate", "scroll_entropy", "tab_count", "domain_switches"):
            score += min(v, 1.0) * w
        else:
            score += v * w
    return round(min(100.0, max(0.0, score * 100.0)), 2)


def _confidence(features, cls):
    nz = sum(1 for v in features.values() if v > 0.01)
    comp = nz / max(len(features), 1)
    k, i = features.get("kpm", 0), features.get("idle_ratio", 0)
    pen = 0.3 if (k > 0.6 and i > 0.6) else 0
    pen = 0.5 if (k > 0.8 and i > 0.8) else pen
    return round(max(0.1, min(1.0, comp * (1 - pen))), 3)


# ═══════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════

def compute_cls_heuristic(features):
    """Compute CLS using the heuristic fallback (no model required)."""
    return _heuristic(features)


def compute_confidence(features, cls):
    """Compute confidence score for a given CLS prediction."""
    return _confidence(features, cls)


def compute_cls_model(features: dict):
    """
    Compute CLS using the best available model.
    Priority: TFLite > .clsmdl > heuristic fallback.
    Returns (cls_score [0-100], confidence [0-1]).
    """
    mdir = os.path.dirname(os.path.abspath(__file__))

    # 1. Try TFLite first (universal, cross-platform)
    tflite = os.path.join(mdir, "model.tflite")
    if os.path.exists(tflite):
        try:
            interp = _load_tflite(tflite)
            if interp:
                feat = [features[k] for k in
                    ['kpm', 'switch_rate', 'scroll_entropy', 'mouse_entropy',
                     'idle_ratio', 'tab_count', 'domain_switches', 'time_of_day']]
                return _run_tflite(interp, feat)
        except Exception as e:
            print(f"[ml] TFLite error: {e}")

    # 2. Try custom .clsmdl model
    custom = os.path.join(mdir, "model.clsmdl")
    if os.path.exists(custom):
        try:
            model = load_custom_model(custom)
            arr = np.array([[features[k] for k in
                ['kpm', 'switch_rate', 'scroll_entropy', 'mouse_entropy',
                 'idle_ratio', 'tab_count', 'domain_switches', 'time_of_day']]],
                dtype=np.float32)
            cls = float(model.predict(arr)[0])
            cls = max(0.0, min(100.0, cls))
            return round(cls, 2), _confidence(features, cls)
        except Exception as e:
            print(f"[ml] Custom model error: {e}")

    # 3. Heuristic fallback
    cls = _heuristic(features)
    conf = _confidence(features, cls)
    return cls, conf


def get_model_info():
    mdir = os.path.dirname(os.path.abspath(__file__))
    # Report in priority order (TFLite first)
    for name in ("model.tflite", "model.clsmdl"):
        path = os.path.join(mdir, name)
        if os.path.exists(path):
            return {"type": name.split(".")[-1], "path": path,
                    "size_kb": os.path.getsize(path) / 1024}
    return {"type": "heuristic", "path": None, "size_kb": 0}


def get_schema():
    """Load feature schema and normalization parameters."""
    mdir = os.path.dirname(os.path.abspath(__file__))
    schema_path = os.path.join(mdir, "schema.json")
    if os.path.exists(schema_path):
        with open(schema_path, "r") as f:
            return json.load(f)
    return None