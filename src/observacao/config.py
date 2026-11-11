"""Carrega app.json, screens.json e cameras.json, com sobrescrita pelo perfil."""
import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
CONFIG = RAIZ / "config"


def _ler(caminho):
    with open(caminho, encoding="utf-8") as f:
        return json.load(f)


def _mesclar(base, extra):
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _mesclar(base[k], v)
        else:
            base[k] = v
    return base


def carregar(perfil="ilha"):
    """Perfis com a chave "ilha" (montagem em ilha, desde 11/11) geram telas e
    posição das câmeras de tracking a partir das medidas de config/ilha.json.
    Perfis antigos (U) continuam lendo screens.json."""
    app = _ler(CONFIG / "app.json")
    p = CONFIG / "profiles" / f"{perfil}.json"
    prof = _ler(p) if p.exists() else {}
    _mesclar(app, prof.get("app", {}))
    cam_path = RAIZ / prof.get("cameras", "config/cameras.json")
    cams = _ler(cam_path) if cam_path.exists() else {"cameras": []}
    cfg = {"perfil": perfil, "app": app, "cameras": cams, "cameras_path": cam_path, "ilha": None}
    if prof.get("ilha"):
        from . import ilha
        il = ilha.Ilha(ilha.carregar(RAIZ / prof["ilha"]))
        il.aplicar_em_cameras(cams)
        cfg["ilha"] = il
        cfg["ilha_path"] = RAIZ / prof["ilha"]
        cfg["telas"] = {"telas": il.telas}
    else:
        cfg["telas"] = _ler(RAIZ / prof.get("screens", "config/screens.json"))
    return cfg


def salvar_cameras(cfg):
    with open(cfg["cameras_path"], "w", encoding="utf-8") as f:
        json.dump(cfg["cameras"], f, ensure_ascii=False, indent=2)
